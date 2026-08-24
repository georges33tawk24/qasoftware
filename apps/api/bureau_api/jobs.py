"""Running a run — SPEC §17.

RQ against Redis when there is one, an inline worker thread when there is not. Runs are
long; nothing blocks a request either way.
"""

from __future__ import annotations

import asyncio
import logging
import os
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

from sqlmodel import select

from bureau_api import db, knowledge
from bureau_api.events import events
from bureau_api.models import Project, Run, RunState
from engine.artifact.models import RunConfig
from engine.capture.auth import ANONYMOUS, Persona
from engine.capture.secrets import SecretError
from engine.capture.secrets import resolve as resolve_secret
from engine.progress import Progress
from engine.run import RunRequest, execute

QUEUE_NAME = "bureau"
logger = logging.getLogger("bureau.jobs")


class Queue(Protocol):
    name: str

    def enqueue(self, run_id: str) -> None: ...


class InlineQueue:
    """A thread per run. Correct for one process, and honest about being that."""

    name = "inline"

    def __init__(self) -> None:
        self.threads: list[threading.Thread] = []

    def enqueue(self, run_id: str) -> None:
        thread = threading.Thread(target=run_job, args=(run_id,), daemon=True)
        thread.start()
        self.threads.append(thread)

    def wait(self, timeout: float = 300) -> None:
        """Used by the tests; a real deployment never waits on its own queue."""
        for thread in list(self.threads):
            thread.join(timeout)


class RedisQueue:
    name = "redis"

    def __init__(self, url: str) -> None:
        import redis
        from rq import Queue as RQQueue

        self.connection = redis.Redis.from_url(url)
        self.queue = RQQueue(QUEUE_NAME, connection=self.connection)

    def enqueue(self, run_id: str) -> None:
        self.queue.enqueue(run_job, run_id, job_timeout=60 * 60)


_queue: Queue | None = None


def queue() -> Queue:
    global _queue
    if _queue is None:
        _queue = _build()
    return _queue


def _build() -> Queue:
    url = os.environ.get("REDIS_URL")
    if not url:
        return InlineQueue()
    try:
        candidate = RedisQueue(url)
        candidate.connection.ping()
    except Exception:
        return InlineQueue()
    return candidate


def use(replacement: Queue) -> None:
    global _queue
    _queue = replacement


# ---------------------------------------------------------------------- the job


def run_job(run_id: str) -> None:
    """Executed by the worker. Everything it needs is in the database.

    Anything that escapes here is recorded against the run: a worker that dies quietly
    leaves a run queued forever, and a queued run with no explanation is the worst
    possible failure mode for something that takes minutes.
    """
    try:
        _run_job(run_id)
    except BaseException as exc:
        logger.exception("run %s failed in the worker", run_id)
        _record_failure(run_id, f"{type(exc).__name__}: {exc}")
        raise


def _record_failure(run_id: str, error: str) -> None:
    try:
        with db.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                return
            run.state = RunState.failed
            run.error = error
            run.finishedAt = datetime.now(UTC)
            session.add(run)
            session.commit()
    except Exception:
        logger.exception("could not record the failure of run %s", run_id)
    events().publish(run_id, {"kind": "stage", "stage": "failed", "text": error})


def _run_job(run_id: str) -> None:
    channel = events()
    progress = Progress(lambda event: channel.publish(run_id, event.as_dict()))

    base_id: str | None = None
    with db.session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        project = session.get(Project, run.projectId)
        if project is None:
            return
        personas = _personas(session, project.id)
        request = _request(session, project, run, personas)
        base = _base_run(session, project.id, run)
        base_id = base.id if base else None
        project_id = project.id
        run.state = RunState.running
        run.startedAt = datetime.now(UTC)
        session.add(run)
        session.commit()

    summary = asyncio.run(execute(request, progress))

    with db.session() as session:
        run = session.get(Run, run_id)
        if run is None:
            return
        run.finishedAt = datetime.now(UTC)
        run.pages = summary.pages
        run.issues = summary.issues
        run.counts = dict(summary.counts)
        run.notes = {
            "notes": summary.notes,
            "aiCostUsd": summary.aiCostUsd,
            "diff": summary.diff,
            "knowledgeChanges": summary.knowledgeChanges,
        }
        run.baseRunId = run.baseRunId or base_id
        run.artifactRunId = summary.runId or None
        run.artifactPath = str(summary.root) if summary.root else None
        run.error = summary.error
        run.state = (
            RunState.complete
            if summary.status.value == "complete"
            else RunState.aborted
            if summary.status.value == "aborted"
            else RunState.failed
        )
        session.add(run)
        session.commit()
        _index_issues(session, run)
        # SPEC §15: only when something new or regressed appears. A quiet run is silent,
        # which is the whole reason anyone leaves the notifications switched on.
        from bureau_api import delivery  # local: delivery imports this module's queue

        for kind, outcome in delivery.notify(session, run, origin=_origin()):
            logger.info("run %s: %s → %s", run.id, kind, outcome)

    _prune(project_id)

    # The terminal event goes last, after the issues are queryable. A browser that is
    # told a run is done and then finds an empty list has been told the wrong thing.
    channel.publish(
        run_id,
        {
            "kind": "stage",
            "stage": "done" if summary.error is None else "failed",
            "issues": summary.issues,
            "pages": summary.pages,
            "text": summary.error,
        },
    )


def _recordings(session: Any, project_id: str) -> list[dict[str, Any]]:
    """Saved journeys travel in the run config, so the artifact records exactly what ran."""
    from bureau_api.models import Recording

    rows = session.exec(
        select(Recording).where(Recording.projectId == project_id).where(Recording.enabled == True)  # noqa: E712 - SQLModel needs the comparison
    ).all()
    return [
        {
            "name": row.name,
            "persona": row.persona,
            "steps": list(row.steps or []),
            "enabled": True,
        }
        for row in rows
    ]


def _prune(project_id: str) -> None:
    """Issues are kept forever; screenshots beyond the last few runs are not."""
    from engine.retention import KEEP_MEDIA_RUNS, prune

    keep = int(os.environ.get("BUREAU_KEEP_MEDIA_RUNS", KEEP_MEDIA_RUNS))
    directory = db.artifact_root() / project_id
    if keep <= 0 or not directory.is_dir():
        return
    try:
        result = prune(directory, keep=keep)
    except OSError as exc:
        logger.warning("could not prune %s: %s", directory, exc)
        return
    if result.files:
        logger.info(
            "pruned %s media file(s) (%.1fMB) from %s older run(s)",
            result.files,
            result.megabytes,
            result.runs,
        )


def _origin() -> str:
    """What to put in a digest's links. Set it or the links are left out."""
    return os.environ.get("BUREAU_PUBLIC_ORIGIN", "").rstrip("/")


def _request(session: Any, project: Project, run: Run, personas: list[Persona]) -> RunRequest:
    config = RunConfig.model_validate(project.config or {})
    config.authorisedBy = project.authorisedBy
    config.figmaFileKey = project.figmaFileKey
    config.recordings = _recordings(session, project.id)
    base = _base_run(session, project.id, run)
    return RunRequest(
        target=project.target,
        out_dir=db.artifact_root() / project.id,
        config=config,
        personas=personas or [ANONYMOUS],
        figma_key=project.figmaFileKey,
        figma_token=_secret(project.figmaTokenRef, "figma"),
        figma_frames=dict(project.figmaFrames or {}),
        provider=_provider(project),
        knowledge=[n for n in knowledge.notes_for(session, project.id) if n.entries],
        base_run=Path(base.artifactPath) if base and base.artifactPath else None,
        previously_fixed=_previously_fixed(session, project.id),
        dismissed=_dismissed(session, project.id),
        flaky=_flaky(session, project.id),
        terminal=False,
    )


def _secret(ref: str | None, what: str) -> str | None:
    """Resolve a per-project reference at run time.

    Nothing is cached and nothing is written down: the value exists for the length of
    the run and lives in the environment or the keychain the rest of the time.
    """
    if not ref:
        return None
    try:
        return resolve_secret(ref)
    except SecretError as exc:
        logger.warning("%s credential unavailable: %s", what, exc)
        return None


def _provider(project: Project) -> Any:
    """The model provider for this project, or none.

    A project may name its own key; otherwise the deployment's own key is used, which is
    the usual arrangement. With no key anywhere this returns None and the run does the
    deterministic sweep and nothing else — which is a normal way to run this product,
    not a failure.
    """
    from bureau_api.knowledge import provider as shared_provider
    from engine.agents.providers import build

    if project.modelTokenRef:
        key = _secret(project.modelTokenRef, "model")
        if key:
            try:
                return build(project.provider or "anthropic", api_key=key)
            except Exception:
                logger.warning("could not build the %s provider", project.provider)
                return None
    return shared_provider()


def _base_run(session: Any, project_id: str, run: Run) -> Run | None:
    """What this run is compared against: whatever was named, else the last one that
    finished. SPEC §11 wants every run after the first to arrive as a diff."""
    if run.baseRunId:
        found: Run | None = session.get(Run, run.baseRunId)
        return found
    previous: Run | None = session.exec(
        select(Run)
        .where(Run.projectId == project_id)
        .where(Run.state == RunState.complete)
        .where(Run.id != run.id)
        .order_by(Run.finishedAt.desc())  # type: ignore[union-attr]  # SQLModel column
    ).first()
    return previous


def _dismissed(session: Any, project_id: str) -> list[str]:
    """SPEC §1.7. Passed to the run so the report never shows them; the artifact still
    records that they were found, which is what keeps the index honest."""
    from bureau_api.issues import CARRIED
    from bureau_api.models import Issue

    rows = session.exec(
        select(Issue.fingerprint)
        .where(Issue.projectId == project_id)
        .where(Issue.state.in_([state.value for state in CARRIED]))  # type: ignore[attr-defined]
    ).all()
    return [str(row) for row in rows]


def _flaky(session: Any, project_id: str) -> list[str]:
    """Which findings have come and gone. Only the index has watched enough runs to know."""
    from bureau_api.models import Issue

    rows = session.exec(
        select(Issue.fingerprint).where(Issue.projectId == project_id).where(Issue.flaky == True)  # noqa: E712 - SQLModel needs the comparison
    ).all()
    return [str(row) for row in rows]


def _previously_fixed(session: Any, project_id: str) -> list[str]:
    """Regressed means fixed once and back again, and only the index remembers that."""
    from bureau_api.models import Issue, IssueState

    rows = session.exec(
        select(Issue.fingerprint)
        .where(Issue.projectId == project_id)
        .where(Issue.state == IssueState.fixed)
    ).all()
    return [str(row) for row in rows]


def _personas(session: Any, project_id: str) -> list[Persona]:
    from bureau_api.models import Persona as Row

    rows = session.exec(select(Row).where(Row.projectId == project_id)).all()
    out = []
    for row in rows:
        try:
            out.append(Persona.model_validate({"name": row.name, **(row.config or {})}))
        except Exception:
            continue
    return out


def _index_issues(session: Any, run: Run) -> None:
    """Copy the neutral issue records into the index so the board can query them.

    The artifact stays the source of truth; this is a projection of it, rebuilt from the
    artifact whenever a run finishes.
    """
    from bureau_api.issues import index_run

    if run.artifactPath:
        index_run(session, run, Path(run.artifactPath))
