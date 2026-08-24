"""Projecting a run's issues into the index — SPEC §11, §17.

The artifact is the source of truth. This copies the neutral issue records into the
database so the board can query them, and carries a dismissal forward: an issue dismissed
once must never reappear, on any run, forever (SPEC §1.7).

Run diffing and the full lifecycle are phase 8; what is here is the part the UI needs to
show a list at all, plus the dismissal rule, which is the one that decides whether the
tool gets used past week two.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from bureau_api.models import Issue, IssueState, Run
from engine.artifact.store import RunPaths
from engine.checkers import runner
from engine.issues.models import IssuesFile

CARRIED = {IssueState.dismissed, IssueState.wont_fix}
"""States that survive a re-run untouched. A dismissal is permanent."""

FLAKY_FLIPS = 2
"""Present, absent, present. Two changes of state is the smallest pattern that means
"intermittent" rather than "fixed" followed by "someone broke it again"."""


def read_issues(artifact: Path) -> IssuesFile | None:
    paths = RunPaths(artifact)
    if not paths.issues.is_file():
        return None
    return runner.read(paths)


def index_run(session: Session, run: Run, artifact: Path) -> int:
    payload = read_issues(artifact)
    if payload is None:
        return 0

    existing = {
        row.fingerprint: row
        for row in session.exec(select(Issue).where(Issue.projectId == run.projectId)).all()
    }
    seen: set[str] = set()
    now = datetime.now(UTC)

    for issue in payload.issues:
        seen.add(issue.fingerprint)
        row = existing.get(issue.fingerprint)
        record = issue.model_dump(mode="json")
        record["instanceCount"] = issue.instanceCount
        record["pagePaths"] = issue.pagePaths

        if row is None:
            session.add(
                Issue(
                    projectId=run.projectId,
                    fingerprint=issue.fingerprint,
                    checkerId=issue.checkerId,
                    issueKind=issue.issueKind,
                    category=issue.category.value,
                    severity=issue.severity.value,
                    defaultSeverity=issue.defaultSeverity.value,
                    title=issue.title,
                    firstSeenRunId=run.id,
                    lastSeenRunId=run.id,
                    firstSeenAt=now,
                    lastSeenAt=now,
                    instanceCount=issue.instanceCount,
                    payload=record,
                )
            )
            continue

        row.lastSeenRunId = run.id
        row.lastSeenAt = now
        row.instanceCount = issue.instanceCount
        row.title = issue.title
        row.payload = record
        returned = not row.presentLastRun
        if returned:
            row.flips += 1
            row.flaky = row.flaky or row.flips >= FLAKY_FLIPS
        row.presentLastRun = True
        # A dismissed issue that comes back stays dismissed. A flaky one comes back all
        # the time, and calling that a regression is how the regressed column becomes
        # noise nobody reads.
        if row.state not in CARRIED and row.state is IssueState.fixed:
            row.state = IssueState.confirmed if row.flaky else IssueState.regressed
        session.add(row)

    for fingerprint, row in existing.items():
        if fingerprint in seen or row.state in CARRIED:
            continue
        if row.presentLastRun:
            row.flips += 1
            row.presentLastRun = False
            session.add(row)
        if row.state is not IssueState.fixed and not row.flaky:
            row.state = IssueState.fixed
            row.lastSeenAt = now
            session.add(row)

    session.commit()
    return len(payload.issues)


def visible(session: Session, project_id: str, **filters: Any) -> list[Issue]:
    """Dismissed issues are filtered before anything is rendered (SPEC §11)."""
    statement = select(Issue).where(Issue.projectId == project_id)
    if not filters.get("include_dismissed"):
        statement = statement.where(Issue.state != IssueState.dismissed)
    if filters.get("flaky") is not None:
        statement = statement.where(Issue.flaky == bool(filters["flaky"]))
    for column, value in (
        ("severity", filters.get("severity")),
        ("category", filters.get("category")),
        ("state", filters.get("state")),
        ("checkerId", filters.get("checker")),
    ):
        if value:
            statement = statement.where(getattr(Issue, column) == value)
    rows = list(session.exec(statement).all())
    order = ["blocker", "critical", "major", "minor", "trivial"]
    rows.sort(
        key=lambda row: (
            order.index(row.severity) if row.severity in order else len(order),
            -row.instanceCount,
            row.title,
        )
    )
    return rows


def evidence_for(run: Run, issue: Issue) -> dict[str, Any] | None:
    """What the evidence viewer needs — SPEC §16's signature element.

    Computed from the artifact rather than stored on the issue: the live screenshot, the
    design frame if one matched, and each instance's measured delta with the box to hang
    a leader line from.
    """
    if not run.artifactPath:
        return None
    from engine.artifact.context import RunContext

    root = Path(run.artifactPath)
    if not (root / "run.json").is_file():
        return None
    ctx = RunContext.open(root)

    payload = issue.payload or {}
    instances = payload.get("instances") or []
    located = [i for i in instances if i.get("box")]
    if not located:
        return None
    lead = located[0]
    viewport = lead.get("viewport") or ""
    page_id = lead.get("pageId") or ""
    scale = next((v.deviceScaleFactor for v in ctx.viewports if v.name == viewport), 1.0)

    live_png = ctx.paths.full_png(page_id, viewport)
    if not live_png.is_file():
        return None

    out: dict[str, Any] = {
        "issueId": issue.id,
        "title": issue.title,
        "severity": issue.severity,
        "live": {
            "src": _media(run, live_png, root),
            "scale": scale,
            "page": lead.get("pagePath"),
            "viewport": viewport,
        },
        "design": _design(ctx, run, root, page_id, viewport, lead),
        "deltas": [
            {
                "label": payload.get("title") or issue.title,
                "expected": payload.get("expected"),
                "actual": instance.get("actual") or payload.get("actual"),
                "selector": instance.get("selector"),
                "box": instance.get("box"),
            }
            for instance in located[:12]
        ],
    }
    return out


def _media(run: Run, path: Path, root: Path) -> str:
    return f"/api/runs/{run.id}/media/{path.relative_to(root).as_posix()}"


def _design(
    ctx: Any, run: Run, root: Path, page_id: str, viewport: str, lead: dict[str, Any]
) -> dict[str, Any] | None:
    """The matched frame and where this element sits inside it, in frame coordinates."""
    from engine.figma.ingest import frame_png

    document = ctx.figma()
    mapping = ctx.mapping(page_id, viewport)
    if document is None or mapping is None or not mapping.confident:
        return None
    record = next(
        (m for m in mapping.matches if m.elementId == lead.get("elementId") and m.figmaNodeId),
        None,
    )
    node = document.nodes.get(record.figmaNodeId or "") if record else None
    if node is None:
        return None
    frame = document.frame(node.frameId)
    image = frame_png(ctx.paths, node.frameId)
    if frame is None or not image.is_file():
        return None
    return {
        "src": _media(run, image, root),
        "scale": 2.0,
        "frame": frame.name,
        "box": {
            "x": node.box.x - frame.box.x,
            "y": node.box.y - frame.box.y,
            "w": node.box.w,
            "h": node.box.h,
        },
    }
