"""Exports, digests and schedules in the control plane — SPEC §14, §15.

The engine decides *what* an export or a digest looks like. This decides *when*, and
carries the two pieces of state the engine cannot: which remote key an issue already has,
and when a schedule last fired.
"""

from __future__ import annotations

import logging
import tempfile
import threading
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlmodel import Session, select

from bureau_api import db
from bureau_api.issues import visible
from bureau_api.models import (
    ExportTarget,
    IssueState,
    NotifyChannel,
    Project,
    Run,
    RunState,
    Schedule,
)
from engine.exporters.base import ExportResult, Target, export
from engine.issues.diff import Change, DiffEntry, RunDiff
from engine.issues.models import Issue as NeutralIssue
from engine.issues.models import Severity
from engine.notify import digest as digests
from engine.notify.channels import Channel, send
from engine.schedule import CronError, due, next_after

logger = logging.getLogger("bureau.delivery")

TICK_SECONDS = 60
"""One minute is the resolution of a crontab expression; anything finer is wasted work."""

EXPORTABLE = {IssueState.new, IssueState.confirmed, IssueState.regressed}
"""What an export sends. A dismissed issue is not somebody else's backlog item."""


# --------------------------------------------------------------------- exporting


def target_from(row: ExportTarget) -> Target:
    config = dict(row.config or {})
    return Target(
        kind=row.kind,
        base_url=str(config.get("baseUrl") or ""),
        project=str(config.get("project") or ""),
        token_env=str(config.get("tokenEnv") or ""),
        user=str(config.get("user") or ""),
        priorities=dict(config.get("priorities") or {}),
        labels=list(config.get("labels") or []),
        extra=dict(config.get("extra") or {}),
        dry_run=bool(config.get("dryRun")),
    )


def run_export(
    session: Session,
    row: ExportTarget,
    *,
    issue_ids: list[str] | None = None,
    report_url: str = "",
) -> list[ExportResult]:
    """Push, then remember the keys so the next export updates rather than duplicates."""
    rows = [
        issue
        for issue in visible(session, row.projectId, include_dismissed=False)
        if issue.state in EXPORTABLE and (issue_ids is None or issue.id in issue_ids)
    ]
    if not rows:
        return []

    by_fingerprint = {issue.fingerprint: issue for issue in rows}
    known = {
        issue.fingerprint: str(key)
        for issue in rows
        if (key := (issue.remoteKeys or {}).get(row.kind))
    }
    neutral = [_neutral(issue.payload) for issue in rows]
    run_dir = _latest_artifact(session, row.projectId)

    with tempfile.TemporaryDirectory(prefix="bureau-export-") as work:
        results = export(
            neutral,
            target_from(row),
            run_dir=run_dir,
            report_url=report_url,
            known=known,
            work=Path(work),
        )

    for result in results:
        issue = by_fingerprint.get(result.fingerprint)
        if issue is None or not result.remote_key:
            continue
        # A JSON column needs a new dict to be seen as changed.
        issue.remoteKeys = {**(issue.remoteKeys or {}), row.kind: result.remote_key}
        session.add(issue)
    row.lastExportedAt = datetime.now(UTC)
    session.add(row)
    session.commit()
    return results


DERIVED = ("instanceCount", "pagePaths")
"""The index adds these to the payload so a list can be rendered without opening an
artifact. They are properties on the neutral record, which forbids unknown fields."""


def _neutral(payload: dict[str, Any]) -> NeutralIssue:
    return NeutralIssue.model_validate({k: v for k, v in payload.items() if k not in DERIVED})


def _latest_artifact(session: Session, project_id: str) -> Path | None:
    run = session.exec(
        select(Run)
        .where(Run.projectId == project_id)
        .where(Run.state == RunState.complete)
        .order_by(Run.finishedAt.desc())  # type: ignore[union-attr]  # SQLModel column
    ).first()
    return Path(run.artifactPath) if run and run.artifactPath else None


# --------------------------------------------------------------------- notifying


def channel_from(row: NotifyChannel) -> Channel:
    config = dict(row.config or {})
    return Channel(
        kind=row.kind,
        url_env=str(config.get("urlEnv") or ""),
        url=str(config.get("url") or ""),
        to=list(config.get("to") or []),
        smtp_host=str(config.get("smtpHost") or ""),
        smtp_port=int(config.get("smtpPort") or 587),
        smtp_user=str(config.get("smtpUser") or ""),
        smtp_password_env=str(config.get("smtpPasswordEnv") or ""),
        sender=str(config.get("sender") or ""),
        extra=dict(config.get("extra") or {}),
    )


def notify(session: Session, run: Run, *, origin: str = "") -> list[tuple[str, str]]:
    """SPEC §15: only when something new or regressed appears.

    A run with nothing new sends nothing, and says so in the log rather than silently —
    "did the digest work?" should be answerable without a database.
    """
    project = session.get(Project, run.projectId)
    if project is None or not run.artifactPath:
        return []
    path = Path(run.artifactPath) / "diff.json"
    diff = RunDiff.model_validate_json(path.read_bytes()) if path.is_file() else RunDiff()
    if not diff.baseRunId:
        # A first run has nothing to compare against, so the engine writes no diff. For a
        # digest, everything it found is news — and it is the one run where the whole
        # list is the point rather than the noise.
        diff = _everything_is_new(session, run)

    payload = digests.build(
        diff,
        project=project.name,
        target=project.target,
        run_id=run.id,
        report_url=f"{origin}/api/runs/{run.id}/report" if origin else "",
        board_url=f"{origin}/projects/{project.id}/board" if origin else "",
    )
    if not payload.worth_sending:
        logger.info("run %s: nothing new or regressed, sending nothing", run.id)
        return []

    rows = list(
        session.exec(
            select(NotifyChannel)
            .where(NotifyChannel.projectId == project.id)
            .where(NotifyChannel.enabled == True)  # noqa: E712 - SQLModel needs the comparison
        ).all()
    )
    wanted = [row for row in rows if digests.above(payload, _severity(row.minSeverity))]
    results = send(payload, [channel_from(row) for row in wanted])
    now = datetime.now(UTC)
    for row in wanted:
        row.lastSentAt = now
        session.add(row)
    session.commit()
    return results


def _everything_is_new(session: Session, run: Run) -> RunDiff:
    return RunDiff(
        baseRunId="",
        entries=[
            DiffEntry(
                fingerprint=issue.fingerprint,
                change=Change.new,
                title=issue.title,
                severity=issue.severity,
                checkerId=issue.checkerId,
                instanceCount=issue.instanceCount,
                delta=issue.instanceCount,
            )
            for issue in visible(session, run.projectId, include_dismissed=False)
            if issue.lastSeenRunId == run.id and not issue.flaky
        ],
    )


def _severity(value: str | None) -> Severity | None:
    try:
        return Severity(value) if value else None
    except ValueError:
        return None


# -------------------------------------------------------------------- scheduling


def refresh(row: Schedule, *, now: datetime | None = None) -> Schedule:
    moment = now or datetime.now(UTC)
    try:
        row.nextFireAt = next_after(row.expression, row.lastFiredAt or moment, row.timezone)
    except CronError:
        row.enabled = False
        row.nextFireAt = None
    return row


def fire_due(session: Session, *, now: datetime | None = None) -> list[str]:
    """Enqueue a run for every schedule whose time has come. Returns the run ids."""
    from bureau_api.jobs import queue

    moment = now or datetime.now(UTC)
    started: list[str] = []
    rows = session.exec(
        select(Schedule).where(Schedule.enabled == True)  # noqa: E712 - SQLModel comparison
    ).all()
    for row in rows:
        try:
            if not due(row.expression, row.lastFiredAt, moment, row.timezone):
                continue
        except CronError:
            logger.warning("schedule %s has an unreadable expression; disabling", row.id)
            row.enabled = False
            session.add(row)
            continue
        if _already_running(session, row.projectId):
            # A site that takes longer to sweep than its own interval must not pile up.
            logger.info("schedule %s: a run is already in flight, skipping this window", row.id)
            row.lastFiredAt = moment
            refresh(row, now=moment)
            session.add(row)
            continue
        run = Run(projectId=row.projectId, triggeredBy=f"schedule:{row.id}")
        session.add(run)
        row.lastFiredAt = moment
        refresh(row, now=moment)
        session.add(row)
        session.commit()
        session.refresh(run)
        queue().enqueue(run.id)
        started.append(run.id)
    session.commit()
    return started


def _already_running(session: Session, project_id: str) -> bool:
    return (
        session.exec(
            select(Run)
            .where(Run.projectId == project_id)
            .where(Run.state.in_([RunState.queued, RunState.running]))  # type: ignore[attr-defined]
        ).first()
        is not None
    )


class Ticker:
    """A thread that checks the schedules once a minute.

    ponytail: one process. Two API replicas would fire a schedule twice; the upgrade path
    is a lock row taken with `SELECT … FOR UPDATE SKIP LOCKED` before enqueuing, which is
    worth doing the day someone runs a second replica and not before.
    """

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, daemon=True, name="bureau-scheduler")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5)
            self._thread = None

    def _loop(self) -> None:
        while not self._stop.wait(TICK_SECONDS):
            try:
                with db.session() as session:
                    for run_id in fire_due(session):
                        logger.info("schedule started run %s", run_id)
            except Exception:
                logger.exception("scheduler tick failed")


def issues_for_ci(session: Session, run: Run) -> dict[str, Any]:
    """What a pipeline needs to decide whether to fail the build — SPEC §15."""
    path = Path(run.artifactPath or "") / "diff.json"
    counts: dict[str, int] = {}
    regressed: dict[str, int] = {}
    if path.is_file():
        diff = RunDiff.model_validate_json(path.read_bytes())
        for entry in diff.entries:
            if entry.change.value == "new":
                counts[entry.severity] = counts.get(entry.severity, 0) + 1
            elif entry.change.value == "regressed":
                regressed[entry.severity] = regressed.get(entry.severity, 0) + 1
    return {
        "runId": run.id,
        "state": run.state.value,
        "new": counts,
        "regressed": regressed,
        "total": dict(run.counts or {}),
        "reportUrl": f"/api/runs/{run.id}/report" if run.state is RunState.complete else None,
        "error": run.error,
    }


def wait_for(run_id: str, timeout: float) -> Run | None:
    """Block until a run finishes. Used by the CI endpoint, never by a browser."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with db.session() as session:
            run = session.get(Run, run_id)
            if run is None:
                return None
            if run.state in (RunState.complete, RunState.failed, RunState.aborted):
                return run
        time.sleep(1.0)
    with db.session() as session:
        return session.get(Run, run_id)


__all__ = [
    "Ticker",
    "channel_from",
    "fire_due",
    "issues_for_ci",
    "notify",
    "refresh",
    "run_export",
    "target_from",
    "wait_for",
]
