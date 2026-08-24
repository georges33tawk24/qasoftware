"""The control plane — SPEC §17.

FastAPI, SQLModel, SSE for progress. Runs are long, so nothing here does the work: a run
is queued and the browser watches it happen.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from sqlmodel import Session, SQLModel, func, select

from bureau_api import db, delivery, knowledge
from bureau_api import issues as issue_index
from bureau_api.events import events
from bureau_api.jobs import queue
from bureau_api.models import (
    Comment,
    ExportTarget,
    Issue,
    IssueState,
    Knowledge,
    NotifyChannel,
    Persona,
    Project,
    Recording,
    Run,
    RunState,
    Schedule,
)
from bureau_api.schemas import (
    BoardColumn,
    BoardOut,
    ChannelIn,
    ChannelOut,
    CiRunIn,
    CommentIn,
    CommentOut,
    ExportResultOut,
    ExportRunIn,
    ExportTargetIn,
    ExportTargetOut,
    IssueOut,
    IssueUpdate,
    KnowledgeIn,
    KnowledgeOut,
    KnowledgeUpdate,
    PersonaIn,
    PersonaOut,
    ProjectIn,
    ProjectOut,
    RecordingIn,
    RecordingOut,
    RunIn,
    RunOut,
    ScheduleIn,
    ScheduleOut,
    VolatileCandidate,
    VolatileIn,
    VolatileOut,
)
from engine import branding
from engine.artifact.store import RunPaths
from engine.capture.flows.record import parse as parse_script
from engine.capture.secrets import SecretError
from engine.capture.secrets import resolve as resolve_secret
from engine.exporters import base as exporters
from engine.knowledge.parse import entries_from
from engine.notify.channels import SENDERS
from engine.schedule import CronError
from engine.schedule import parse as cron_parse
from engine.schedule import zone as cron_zone

STREAM_MAX_SECONDS = 60 * 30
"""A run that has not finished in half an hour has a problem the browser cannot help
with. The client reconnects with `after=` and loses nothing."""

COLUMNS: list[tuple[IssueState, str]] = [
    (IssueState.new, "New"),
    (IssueState.confirmed, "Confirmed"),
    (IssueState.regressed, "Regressed"),
    (IssueState.fixed, "Fixed"),
    (IssueState.wont_fix, "Won't fix"),
    (IssueState.dismissed, "Dismissed"),
]
"""SPEC §13's columns. `regressed` earns its own because those are the ones worth
looking at first, and `wont_fix` is kept apart from `dismissed` because one is a
decision about the finding and the other is a decision about the checker."""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """The scheduler ticks alongside the API — SPEC §15.

    Here rather than in the worker because the API is the process that is always up, and
    a schedule that only fires when a worker happens to be running is not a schedule.
    """
    ticker = delivery.Ticker()
    if os.environ.get("BUREAU_SCHEDULER", "1") != "0":
        ticker.start()
    try:
        yield
    finally:
        ticker.stop()


app = FastAPI(title=f"{branding.PRODUCT_NAME} API", version="0.1.0", lifespan=lifespan)

# The browser opens the event stream against this API directly rather than through the
# web app's proxy: a rewrite proxy buffers `text/event-stream` and the progress arrives in
# one lump at the end, which is exactly the spinner SPEC §16 exists to avoid.
#
# No credentials are allowed with the wildcard. `api.probes` flags precisely that
# combination on other people's APIs and it would be poor form to ship it on our own.
_origins = [
    origin.strip()
    for origin in os.environ.get("BUREAU_ALLOWED_ORIGINS", "*").split(",")
    if origin.strip()
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE"],
    allow_headers=["content-type"],
)
DB = Annotated[Session, Depends(db.get_session)]


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "product": branding.PRODUCT_NAME}


# -------------------------------------------------------------------- projects


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(session: DB) -> list[ProjectOut]:
    out = []
    for project in session.exec(
        select(Project).where(Project.archived == False)  # noqa: E712 - SQL, not Python
    ).all():
        out.append(_project_out(session, project))
    return out


@app.post("/api/projects", response_model=ProjectOut, status_code=201)
def create_project(payload: ProjectIn, session: DB) -> ProjectOut:
    project = Project(**payload.model_dump())
    session.add(project)
    session.commit()
    session.refresh(project)
    return _project_out(session, project)


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: str, session: DB) -> ProjectOut:
    return _project_out(session, _require(session, Project, project_id))


@app.patch("/api/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: str, payload: ProjectIn, session: DB) -> ProjectOut:
    project = _require(session, Project, project_id)
    for key, value in payload.model_dump(exclude_unset=True).items():
        setattr(project, key, value)
    session.add(project)
    session.commit()
    return _project_out(session, project)


# -------------------------------------------------------------------- personas


@app.get("/api/projects/{project_id}/personas", response_model=list[PersonaOut])
def list_personas(project_id: str, session: DB) -> list[Persona]:
    _require(session, Project, project_id)
    return list(session.exec(select(Persona).where(Persona.projectId == project_id)).all())


@app.post("/api/projects/{project_id}/personas", response_model=PersonaOut, status_code=201)
def create_persona(project_id: str, payload: PersonaIn, session: DB) -> Persona:
    _require(session, Project, project_id)
    persona = Persona(projectId=project_id, **payload.model_dump())
    session.add(persona)
    session.commit()
    session.refresh(persona)
    return persona


@app.delete("/api/personas/{persona_id}", status_code=204)
def delete_persona(persona_id: str, session: DB) -> Response:
    session.delete(_require(session, Persona, persona_id))
    session.commit()
    return Response(status_code=204)


# ------------------------------------------------------------------------ runs


@app.get("/api/projects/{project_id}/runs", response_model=list[RunOut])
def list_runs(project_id: str, session: DB, limit: int = 25) -> list[RunOut]:
    _require(session, Project, project_id)
    rows = session.exec(
        select(Run).where(Run.projectId == project_id).order_by(Run.queuedAt.desc()).limit(limit)  # type: ignore[attr-defined]
    ).all()
    return [_run_out(row) for row in rows]


@app.post("/api/projects/{project_id}/runs", response_model=RunOut, status_code=202)
def start_run(project_id: str, payload: RunIn, session: DB) -> RunOut:
    """SPEC §15's CI hook is this endpoint. It returns immediately; the run is queued."""
    _require(session, Project, project_id)
    run = Run(
        projectId=project_id,
        baseRunId=payload.baseRunId,
        triggeredBy=payload.triggeredBy,
        notes={"knowledge": [payload.knowledge] if payload.knowledge else []},
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    events().publish(run.id, {"kind": "stage", "stage": "queued", "at": _now()})
    queue().enqueue(run.id)
    return _run_out(run)


@app.get("/api/runs/{run_id}", response_model=RunOut)
def get_run(run_id: str, session: DB) -> RunOut:
    return _run_out(_require(session, Run, run_id))


@app.get("/api/runs/{run_id}/events")
def run_events(run_id: str, session: DB, after: int = 0) -> StreamingResponse:
    """Server-sent events — SPEC §16. Never a spinner: pages appear as they are checked
    and issues appear as they are found."""
    _require(session, Run, run_id)
    channel = events()

    def stream() -> Iterator[str]:
        yield ": open\n\n"
        deadline = time.monotonic() + STREAM_MAX_SECONDS
        for event in channel.stream(run_id, after=after):
            if event.get("kind") == "heartbeat":
                # A comment line keeps the connection open through proxies without
                # looking like an event to the browser.
                if time.monotonic() > deadline:
                    return
                yield ": keep-alive\n\n"
                continue
            yield f"event: {event.get('kind', 'message')}\ndata: {json.dumps(event)}\n\n"
            if event.get("kind") == "stage" and event.get("stage") in ("done", "failed"):
                return

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.get("/api/runs/{run_id}/report")
def run_report(run_id: str, session: DB) -> FileResponse:
    run = _require(session, Run, run_id)
    if not run.artifactPath:
        raise HTTPException(404, "this run produced no artifact")
    report = RunPaths(Path(run.artifactPath)).report
    if not report.is_file():
        raise HTTPException(404, "this run produced no report")
    return FileResponse(report, media_type="text/html")


@app.get("/api/runs/{run_id}/media/{path:path}")
def run_media(run_id: str, path: str, session: DB) -> FileResponse:
    """Evidence lives beside the run, and the UI needs to show it."""
    run = _require(session, Run, run_id)
    if not run.artifactPath:
        raise HTTPException(404, "this run produced no artifact")
    root = Path(run.artifactPath).resolve()
    target = (root / path).resolve()
    if not target.is_file() or root not in target.parents:
        raise HTTPException(404, "no such file in this run")
    return FileResponse(target)


# ---------------------------------------------------------------------- issues


@app.get("/api/projects/{project_id}/issues", response_model=list[IssueOut])
def list_issues(
    project_id: str,
    session: DB,
    severity: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    checker: Annotated[str | None, Query()] = None,
    include_dismissed: bool = False,
) -> list[Issue]:
    _require(session, Project, project_id)
    return issue_index.visible(
        session,
        project_id,
        severity=severity,
        category=category,
        state=state,
        checker=checker,
        include_dismissed=include_dismissed,
    )


@app.get("/api/issues/{issue_id}", response_model=IssueOut)
def get_issue(issue_id: str, session: DB) -> Issue:
    return _require(session, Issue, issue_id)


@app.patch("/api/issues/{issue_id}", response_model=IssueOut)
def update_issue(issue_id: str, payload: IssueUpdate, session: DB) -> Issue:
    """Severity is always editable and the human's dismissal always wins (SPEC §2)."""
    issue = _require(session, Issue, issue_id)
    if payload.state:
        issue.state = IssueState(payload.state)
        if issue.state is IssueState.dismissed:
            issue.dismissedAt = datetime.now(UTC)
            issue.dismissedBy = payload.by
            issue.dismissedReason = payload.reason
    if payload.severity:
        issue.severity = payload.severity
    if payload.assignee is not None:
        issue.assignee = payload.assignee or None
    if payload.labels is not None:
        issue.labels = sorted({label.strip() for label in payload.labels if label.strip()})
    if payload.dueDate is not None:
        issue.dueDate = payload.dueDate
    session.add(issue)
    session.commit()
    session.refresh(issue)
    if payload.intoKnowledge and payload.reason:
        # SPEC §10: a dismissal with a reason is something someone was told. It becomes a
        # draft, not a rule — nothing is applied until it is confirmed.
        knowledge.draft(
            session, issue.projectId, payload.reason, created_by=payload.by, source="dismissal"
        )
    return issue


@app.get("/api/issues/{issue_id}/evidence")
def issue_evidence(issue_id: str, session: DB) -> dict[str, Any]:
    """SPEC §16's evidence viewer wants the two images and the measurements, not a
    composited picture of them."""
    issue = _require(session, Issue, issue_id)
    run_id = issue.lastSeenRunId or issue.firstSeenRunId
    run = session.get(Run, run_id) if run_id else None
    if run is None:
        raise HTTPException(404, "this issue has no run to draw from")
    payload = issue_index.evidence_for(run, issue)
    if payload is None:
        raise HTTPException(404, "no evidence was captured for this issue")
    return payload


@app.get("/api/issues/{issue_id}/comments", response_model=list[CommentOut])
def list_comments(issue_id: str, session: DB) -> list[Comment]:
    _require(session, Issue, issue_id)
    return list(
        session.exec(
            select(Comment).where(Comment.issueId == issue_id).order_by(Comment.createdAt)  # type: ignore[arg-type]
        ).all()
    )


@app.post("/api/issues/{issue_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(issue_id: str, payload: CommentIn, session: DB) -> Comment:
    """Anyone with the project link can comment; developers will not sign up for another
    tool (SPEC §13)."""
    issue = _require(session, Issue, issue_id)
    comment = Comment(issueId=issue_id, author=payload.author, body=payload.body)
    if payload.intoKnowledge:
        # The loop from SPEC §13: "the client changed this, ignore it" is parsed into a
        # draft entry, so the next run already knows once someone confirms it.
        drafted = knowledge.draft(
            session, issue.projectId, payload.body, created_by=payload.author, source="comment"
        )
        comment.knowledgeId = drafted.id
    session.add(comment)
    session.commit()
    session.refresh(comment)
    return comment


# ------------------------------------------------------------------- knowledge


@app.get("/api/projects/{project_id}/knowledge", response_model=list[KnowledgeOut])
def list_knowledge(project_id: str, session: DB) -> list[Knowledge]:
    _require(session, Project, project_id)
    return list(
        session.exec(
            select(Knowledge)
            .where(Knowledge.projectId == project_id)
            .order_by(Knowledge.createdAt.desc())  # type: ignore[attr-defined]
        ).all()
    )


@app.post("/api/projects/{project_id}/knowledge", response_model=KnowledgeOut, status_code=201)
def add_knowledge(project_id: str, payload: KnowledgeIn, session: DB) -> Knowledge:
    """Parsed into entries and stored unconfirmed. A human confirms before a run uses any
    of it — never silently applied (SPEC §10)."""
    _require(session, Project, project_id)
    return knowledge.draft(session, project_id, payload.raw, created_by=payload.createdBy)


@app.patch("/api/knowledge/{knowledge_id}", response_model=KnowledgeOut)
def update_knowledge(knowledge_id: str, payload: KnowledgeUpdate, session: DB) -> Knowledge:
    """Confirm, correct, or retire an entry. Corrections are the point: the model's
    reading of "make the CTA green" is a draft for someone to fix, not a decision."""
    row = _require(session, Knowledge, knowledge_id)
    if payload.entries is not None:
        row.entries = [e.model_dump(mode="json") for e in entries_from(payload.entries)]
    if payload.archived is not None:
        row.archived = payload.archived
    row.confirmed = row.confirmed or payload.confirm
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/api/knowledge/{knowledge_id}", status_code=204)
def delete_knowledge(knowledge_id: str, session: DB) -> Response:
    session.delete(_require(session, Knowledge, knowledge_id))
    session.commit()
    return Response(status_code=204)


@app.post("/api/projects/{project_id}/volatile", response_model=VolatileOut)
async def find_volatile(project_id: str, payload: VolatileIn, session: DB) -> VolatileOut:
    """Load the project's page twice and nominate what would not hold still — SPEC §5.

    ponytail: runs in the request rather than the queue. It is a deliberate, one-off,
    thirty-second action a person waits for, and a job would need a second progress
    surface for no benefit. If it ever needs to sweep every page, it becomes a run.
    """
    from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
    from engine.capture.volatile import sample

    project = _require(session, Project, project_id)
    config = RunConfig.model_validate(project.config or {})
    report = await sample(
        payload.url or project.target,
        config=config,
        viewport=VIEWPORT_PRESETS.get(payload.viewport),
        loads=max(2, min(payload.loads, 4)),
    )
    return VolatileOut(
        url=report.url,
        viewport=report.viewport,
        compared=report.compared,
        candidates=[
            VolatileCandidate(selector=c.selector, kind=c.kind, detail=c.detail)
            for c in report.candidates
        ],
        selectors=report.selectors(),
    )


# ----------------------------------------------------------------------- board


@app.get("/api/projects/{project_id}/board", response_model=BoardOut)
def board(project_id: str, session: DB) -> BoardOut:
    """SPEC §13. A view over the issue records, not a Jira clone: columns, assignee,
    labels, comments. No sprints, no epics, no burndown."""
    _require(session, Project, project_id)
    rows = issue_index.visible(session, project_id, include_dismissed=True)
    columns = [
        BoardColumn(
            state=state.value,
            title=title,
            issues=[
                IssueOut.model_validate(r, from_attributes=True) for r in rows if r.state is state
            ],
        )
        for state, title in COLUMNS
    ]
    return BoardOut(
        projectId=project_id,
        columns=columns,
        assignees=sorted({r.assignee for r in rows if r.assignee}),
        labels=sorted({label for r in rows for label in (r.labels or [])}),
    )


@app.get("/api/runs/{run_id}/diff")
def run_diff(run_id: str, session: DB) -> dict[str, Any]:
    """SPEC §11, read back from the artifact so an old run still answers."""
    run = _require(session, Run, run_id)
    if not run.artifactPath:
        raise HTTPException(404, "this run has no artifact")
    path = Path(run.artifactPath) / "diff.json"
    if not path.is_file():
        raise HTTPException(404, "this run was not diffed")
    return dict(json.loads(path.read_text()))


# ---------------------------------------------------------------- export targets


@app.get("/api/projects/{project_id}/exports", response_model=list[ExportTargetOut])
def list_exports(project_id: str, session: DB) -> list[ExportTarget]:
    _require(session, Project, project_id)
    return list(
        session.exec(select(ExportTarget).where(ExportTarget.projectId == project_id)).all()
    )


@app.post("/api/projects/{project_id}/exports", response_model=ExportTargetOut, status_code=201)
def create_export(project_id: str, payload: ExportTargetIn, session: DB) -> ExportTarget:
    _require(session, Project, project_id)
    known = set(exporters.discover())
    if payload.kind not in known:
        raise HTTPException(422, f"unknown exporter {payload.kind!r}; have {sorted(known)}")
    row = ExportTarget(projectId=project_id, **payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.patch("/api/exports/{export_id}", response_model=ExportTargetOut)
def update_export(export_id: str, payload: ExportTargetIn, session: DB) -> ExportTarget:
    row = _require(session, ExportTarget, export_id)
    for name, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/api/exports/{export_id}", status_code=204)
def delete_export(export_id: str, session: DB) -> Response:
    session.delete(_require(session, ExportTarget, export_id))
    session.commit()
    return Response(status_code=204)


@app.post("/api/exports/{export_id}/run", response_model=list[ExportResultOut])
def push_export(
    export_id: str, payload: ExportRunIn, session: DB, request: Request
) -> list[dict[str, Any]]:
    """Push now. A second push updates rather than duplicates (SPEC §14)."""
    row = _require(session, ExportTarget, export_id)
    if not row.enabled:
        raise HTTPException(409, "this target is disabled")
    results = delivery.run_export(
        session, row, issue_ids=payload.issueIds, report_url=str(request.base_url).rstrip("/")
    )
    return [
        {
            "fingerprint": r.fingerprint,
            "remoteKey": r.remote_key,
            "url": r.url,
            "action": r.action,
            "error": r.error,
            "attachments": r.attachments,
        }
        for r in results
    ]


@app.get("/api/exporters")
def list_exporters() -> dict[str, list[str]]:
    return {"kinds": sorted(exporters.discover())}


# -------------------------------------------------------------------- schedules


@app.get("/api/projects/{project_id}/schedules", response_model=list[ScheduleOut])
def list_schedules(project_id: str, session: DB) -> list[Schedule]:
    _require(session, Project, project_id)
    return list(session.exec(select(Schedule).where(Schedule.projectId == project_id)).all())


@app.post("/api/projects/{project_id}/schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(project_id: str, payload: ScheduleIn, session: DB) -> Schedule:
    _require(session, Project, project_id)
    row = Schedule(projectId=project_id, **payload.model_dump())
    _validate_schedule(row)
    session.add(delivery.refresh(row))
    session.commit()
    session.refresh(row)
    return row


@app.patch("/api/schedules/{schedule_id}", response_model=ScheduleOut)
def update_schedule(schedule_id: str, payload: ScheduleIn, session: DB) -> Schedule:
    row = _require(session, Schedule, schedule_id)
    for name, value in payload.model_dump(exclude_unset=True).items():
        setattr(row, name, value)
    _validate_schedule(row)
    session.add(delivery.refresh(row))
    session.commit()
    session.refresh(row)
    return row


@app.delete("/api/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: str, session: DB) -> Response:
    session.delete(_require(session, Schedule, schedule_id))
    session.commit()
    return Response(status_code=204)


def _validate_schedule(row: Schedule) -> None:
    """A bad expression is refused here, not discovered at three in the morning."""
    try:
        cron_parse(row.expression)
        cron_zone(row.timezone)
    except CronError as exc:
        raise HTTPException(422, str(exc)) from exc


# --------------------------------------------------------------------- channels


@app.get("/api/projects/{project_id}/channels", response_model=list[ChannelOut])
def list_channels(project_id: str, session: DB) -> list[NotifyChannel]:
    _require(session, Project, project_id)
    return list(
        session.exec(select(NotifyChannel).where(NotifyChannel.projectId == project_id)).all()
    )


@app.post("/api/projects/{project_id}/channels", response_model=ChannelOut, status_code=201)
def create_channel(project_id: str, payload: ChannelIn, session: DB) -> NotifyChannel:
    _require(session, Project, project_id)
    if payload.kind not in SENDERS:
        raise HTTPException(422, f"unknown channel {payload.kind!r}; have {sorted(SENDERS)}")
    row = NotifyChannel(projectId=project_id, **payload.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/api/channels/{channel_id}", status_code=204)
def delete_channel(channel_id: str, session: DB) -> Response:
    session.delete(_require(session, NotifyChannel, channel_id))
    session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------------- ci


@app.post("/api/ci/runs", status_code=202)
def ci_run(payload: CiRunIn, session: DB) -> dict[str, Any]:
    """SPEC §15's CI hook: a target URL in, new-issue counts and a report URL out.

    A pipeline knows the site it just deployed, not a project id, so the project is
    found by target or created. `wait` blocks until the run finishes; without it the
    caller gets a run id to poll.
    """
    project = (
        session.get(Project, payload.projectId)
        if payload.projectId
        else session.exec(select(Project).where(Project.target == payload.target)).first()
    )
    if project is None:
        project = Project(
            name=payload.name or payload.target,
            target=payload.target,
            authorisedBy=payload.authorisedBy,
            config=payload.config,
        )
        session.add(project)
        session.commit()
        session.refresh(project)

    run = Run(projectId=project.id, baseRunId=payload.baseRunId, triggeredBy="ci")
    session.add(run)
    session.commit()
    session.refresh(run)
    queue().enqueue(run.id)

    if payload.wait <= 0:
        return {"runId": run.id, "projectId": project.id, "state": run.state.value}
    finished = delivery.wait_for(run.id, payload.wait)
    if finished is None:
        raise HTTPException(500, "the run disappeared while we waited for it")
    with db.session() as fresh:
        reloaded = fresh.get(Run, run.id)
        assert reloaded is not None
        summary: dict[str, Any] = delivery.issues_for_ci(fresh, reloaded)
        return {"projectId": project.id, **summary}


@app.get("/api/runs/{run_id}/ci")
def run_ci(run_id: str, session: DB) -> dict[str, Any]:
    run = _require(session, Run, run_id)
    return delivery.issues_for_ci(session, run)


# ------------------------------------------------------------------- recordings


@app.get("/api/projects/{project_id}/recordings", response_model=list[RecordingOut])
def list_recordings(project_id: str, session: DB) -> list[Recording]:
    _require(session, Project, project_id)
    return list(session.exec(select(Recording).where(Recording.projectId == project_id)).all())


@app.post("/api/projects/{project_id}/recordings", response_model=RecordingOut, status_code=201)
def create_recording(project_id: str, payload: RecordingIn, session: DB) -> Recording:
    """A journey saved as a named regression test — SPEC §15's record-a-flow."""
    _require(session, Project, project_id)
    fields = payload.model_dump()
    # Steps as given, or read out of the recorded script — the descriptions are generated
    # either way, never typed by hand (SPEC §15).
    fields["steps"] = payload.steps or [step.as_dict() for step in parse_script(payload.script)]
    row = Recording(projectId=project_id, **fields)
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@app.delete("/api/recordings/{recording_id}", status_code=204)
def delete_recording(recording_id: str, session: DB) -> Response:
    session.delete(_require(session, Recording, recording_id))
    session.commit()
    return Response(status_code=204)


# --------------------------------------------------------------------- helpers


def _require[T: SQLModel](session: Session, model: type[T], identifier: str) -> T:
    row = session.get(model, identifier)
    if row is None:
        raise HTTPException(404, f"no {model.__name__.lower()} {identifier}")
    return row


def _project_out(session: Session, project: Project) -> ProjectOut:
    runs = session.exec(
        select(func.count()).select_from(Run).where(Run.projectId == project.id)
    ).one()
    open_issues = session.exec(
        select(func.count())
        .select_from(Issue)
        .where(Issue.projectId == project.id)
        .where(Issue.state.not_in([IssueState.dismissed, IssueState.fixed]))  # type: ignore[attr-defined]
    ).one()
    return ProjectOut(
        **project.model_dump(exclude={"archived"}),
        runs=int(runs),
        openIssues=int(open_issues),
        credentials=_credential_status(project),
    )


def _credential_status(project: Project) -> dict[str, str]:
    """Does each reference resolve, right now, on this machine?

    Reports the *status* and never the value. "Your Figma token is unset" is the message
    someone needs; the token itself is not something an API should be able to return.
    """
    out: dict[str, str] = {}
    for name, ref in (("figma", project.figmaTokenRef), ("model", project.modelTokenRef)):
        if not ref:
            continue
        try:
            resolve_secret(ref)
            out[name] = "ok"
        except SecretError as exc:
            out[name] = str(exc)
    return out


def _run_out(run: Run) -> RunOut:
    return RunOut(
        **run.model_dump(exclude={"artifactPath", "notes", "triggeredBy"}),
        reportUrl=f"/api/runs/{run.id}/report" if run.state is RunState.complete else None,
        diff=dict((run.notes or {}).get("diff") or {}),
    )


def _now() -> str:
    return datetime.now(UTC).isoformat()
