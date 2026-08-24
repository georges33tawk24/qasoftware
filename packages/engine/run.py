"""One run, end to end — SPEC §3.

capture → ingest → match → check → exercise → reason → render, with progress emitted at
every step. The CLI and the worker both call this; neither reimplements the order.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from engine.agents.config import AgentConfig
from engine.agents.provider import LLMProvider
from engine.artifact.context import RunContext
from engine.artifact.models import PageRecord, RunConfig, RunStatus
from engine.artifact.store import RunPaths, write_bytes, write_run_manifest
from engine.capture.auth import ANONYMOUS, Persona
from engine.capture.challenge import RunBlocked
from engine.capture.exercise import exercise
from engine.capture.run import capture
from engine.checkers import runner
from engine.figma.client import FigmaClient, FigmaError
from engine.figma.ingest import ingest
from engine.issues.diff import Change
from engine.issues.diff import diff as run_diff
from engine.knowledge.apply import apply as apply_knowledge
from engine.knowledge.models import KnowledgeFile, Note, Verdict
from engine.matching import run as matching
from engine.progress import NULL, Progress, Stage
from engine.report import build as build_report
from engine.visual import compare as visual_compare


@dataclass
class RunRequest:
    target: str
    out_dir: Path
    config: RunConfig = field(default_factory=RunConfig)
    personas: list[Persona] = field(default_factory=lambda: [ANONYMOUS])
    knowledge: list[Note] = field(default_factory=list)
    """Confirmed project knowledge (SPEC §10). Unconfirmed notes never reach a run — the
    control plane is where a human says yes, and `apply` ignores anything else."""
    figma_key: str | None = None
    figma_token: str | None = None
    """Resolved from the project's reference by the caller. It exists in memory for the
    length of the run and is never written into the artifact, the manifest or a log."""
    figma_frames: dict[str, str] = field(default_factory=dict)
    accept_suggested_frames: bool = False
    agents: AgentConfig | None = None
    provider: LLMProvider | None = None
    base_run: Path | None = None
    """The previous run's artifact, for the New / Still open / Fixed / Regressed diff."""

    dismissed: list[str] = field(default_factory=list)
    """Issue fingerprints someone has dismissed. SPEC §11 filters these before the report
    is rendered — but they stay in `issues.json`, because the artifact records what was
    found and the index records what people decided about it."""

    flaky: list[str] = field(default_factory=list)
    """Fingerprints the index has watched appear, vanish and come back. Reported apart
    from everything else and never as a regression."""

    previously_fixed: list[str] = field(default_factory=list)
    """Fingerprints the caller's index says were fixed before this run. History lives in
    the control plane, not in an artifact, so being once-fixed has to be told to us."""

    report: bool = True
    terminal: bool = True
    """Whether `execute` emits the closing `done` event.

    A caller that has work of its own to do after the engine finishes — indexing the
    issues, say — owns the terminal event instead, because a browser that sees `done` and
    then finds an empty issue list has been told the wrong thing.
    """


@dataclass
class RunSummary:
    runId: str = ""
    root: Path | None = None
    status: RunStatus = RunStatus.pending
    pages: int = 0
    issues: int = 0
    counts: dict[str, int] = field(default_factory=dict)
    flows: int = 0
    flowFailures: int = 0
    aiFindings: int = 0
    aiCostUsd: float = 0.0
    knowledgeChanges: int = 0
    diff: dict[str, int] = field(default_factory=dict)
    visualChanged: int = 0
    reportPath: Path | None = None
    notes: list[str] = field(default_factory=list)
    error: str | None = None


async def execute(request: RunRequest, progress: Progress = NULL) -> RunSummary:
    summary = RunSummary()
    try:
        return await _execute(request, progress, summary)
    except RunBlocked as exc:
        summary.status = RunStatus.aborted
        summary.error = str(exc)
        progress.error(str(exc))
        return summary
    except Exception as exc:
        summary.status = RunStatus.failed
        summary.error = f"{type(exc).__name__}: {exc}"
        progress.error(summary.error)
        return summary


async def _execute(request: RunRequest, progress: Progress, summary: RunSummary) -> RunSummary:
    progress.enter(Stage.capture, target=request.target)

    def announce(page: PageRecord) -> None:
        progress.emit(
            "page",
            path=page.path,
            title=page.title,
            status=page.status,
            depth=page.depth,
            blocked=page.crawlBlocked,
        )

    captured = await capture(
        request.target,
        request.out_dir,
        config=request.config,
        personas=request.personas,
        on_page=announce,
    )
    paths = captured.paths
    summary.runId = captured.manifest.runId
    summary.root = paths.root
    summary.pages = len(captured.manifest.pageIds)
    for problem in captured.problems:
        progress.note(f"artifact: {problem}")

    ctx = RunContext.open(paths.root)
    if request.figma_key or (paths.figma / "file.json").is_file():
        await _design(request, paths, ctx, progress, summary)

    if request.config.flows or request.config.apiProbes:
        progress.enter(Stage.exercise)
        outcome = await exercise(paths, ctx, personas=request.personas)
        summary.flows = len(outcome.flows)
        summary.flowFailures = outcome.failed
        for flow in outcome.flows:
            progress.emit("flow", name=flow.name, status=flow.status.value, persona=flow.persona)
        summary.notes.extend(outcome.notes)
        for note in outcome.notes:
            progress.note(note)

    _visual(request, paths, ctx, progress, summary)

    progress.enter(Stage.check)
    ctx = RunContext.open(paths.root)
    checked = runner.check(ctx)
    payload = runner.write(paths, ctx, checked)
    for issue in checked.issues:
        progress.emit(
            "issue",
            id=issue.id,
            severity=issue.severity.value,
            title=issue.title,
            checkerId=issue.checkerId,
            instances=issue.instanceCount,
        )

    if request.provider is not None and (request.agents is None or request.agents.enabled):
        payload = await _reason(request, paths, ctx, payload, progress, summary)

    progress.enter(Stage.resolve)
    payload = _knowledge(request, paths, ctx, payload, progress, summary)
    write_bytes(
        paths.dismissed,
        json.dumps(
            {
                "fingerprints": sorted(set(request.dismissed)),
                "flaky": sorted(set(request.flaky)),
            },
            indent=2,
        ).encode(),
    )
    _diff(request, paths, payload, progress, summary)
    summary.issues = len(payload.issues)
    for issue in payload.issues:
        summary.counts[issue.severity.value] = summary.counts.get(issue.severity.value, 0) + 1

    if request.report:
        progress.enter(Stage.render)
        report = build_report(paths.root)
        summary.reportPath = report.path

    manifest = ctx.manifest
    manifest.status = RunStatus.complete
    manifest.finishedAt = datetime.now(UTC)
    manifest.durationMs = int((manifest.finishedAt - manifest.startedAt).total_seconds() * 1000)
    write_run_manifest(paths, manifest)

    summary.status = RunStatus.complete
    if request.terminal:
        progress.enter(Stage.done, issues=summary.issues, pages=summary.pages)
    return summary


def _knowledge(
    request: RunRequest,
    paths: RunPaths,
    ctx: RunContext,
    payload: Any,
    progress: Progress,
    summary: RunSummary,
) -> Any:
    """Suppress what the client already told us, and say whether they got what they asked
    for — SPEC §10. Runs even with no notes, so `knowledge.json` always exists and an old
    run re-read later says the same thing."""
    applied = apply_knowledge(ctx, request.knowledge, payload)
    write_bytes(
        paths.knowledge,
        KnowledgeFile(notes=list(request.knowledge), changes=applied.changes)
        .model_dump_json(indent=2)
        .encode()
        + b"\n",
    )
    if applied.suppressed:
        note = f"{applied.suppressed} issue(s) suppressed by confirmed project knowledge"
        progress.note(note)
        summary.notes.append(note)
    for change in applied.changes:
        if change.verdict is Verdict.not_applied:
            progress.note(change.headline())
            summary.notes.append(change.headline())
    summary.knowledgeChanges = len(applied.changes)
    if applied.suppressed:
        write_bytes(paths.issues, applied.issues.model_dump_json(indent=2).encode() + b"\n")
    return applied.issues


def _visual(
    request: RunRequest,
    paths: RunPaths,
    ctx: RunContext,
    progress: Progress,
    summary: RunSummary,
) -> None:
    """Compare this run's surfaces with the last one's — SPEC §5's hardening.

    Before the sweep, not after: the comparison is data, and `checkers/visual.py` is a
    pure function over it like every other checker.
    """
    if request.base_run is None or not (Path(request.base_run) / "run.json").is_file():
        return
    try:
        base = RunContext.open(request.base_run)
        result = visual_compare(ctx, base)
    except (OSError, ValueError) as exc:
        progress.note(f"visual: could not compare against the previous run ({exc})")
        return
    write_bytes(paths.visual, result.model_dump_json(indent=2).encode() + b"\n")
    changed = [s for s in result.surfaces if s.compared and s.ssim < 1.0]
    summary.visualChanged = len(changed)
    if changed:
        progress.note(f"{len(changed)} surface(s) look different from the last run")


def _diff(
    request: RunRequest,
    paths: RunPaths,
    payload: Any,
    progress: Progress,
    summary: RunSummary,
) -> None:
    """SPEC §11. Written to the artifact so re-reading an old run gives the same answer."""
    base = None
    if request.base_run is not None and RunPaths(request.base_run).issues.is_file():
        base = runner.read(RunPaths(request.base_run))
    result = run_diff(
        payload,
        base,
        previously_fixed=set(request.previously_fixed),
        flaky=set(request.flaky),
    )
    write_bytes(paths.diff, result.model_dump_json(indent=2).encode() + b"\n")
    if base is None:
        return
    summary.diff = result.counts()
    regressed = result.of(Change.regressed)
    for entry in regressed:
        progress.note(f"regressed: {entry.title}")
    if regressed:
        summary.notes.append(f"{len(regressed)} issue(s) regressed since the last run")


async def _design(
    request: RunRequest, paths: RunPaths, ctx: RunContext, progress: Progress, summary: RunSummary
) -> None:
    progress.enter(Stage.ingest)
    client = (
        FigmaClient(request.figma_token, cache_dir=paths.root.parent / ".figma-cache")
        if request.figma_token and request.figma_key
        else None
    )
    try:
        ingested = ingest(
            paths,
            ctx,
            file_key=request.figma_key,
            client=client,
            confirmed=request.figma_frames,
            accept_suggested=request.accept_suggested_frames,
        )
    except FigmaError as exc:
        progress.note(f"figma: {exc}")
        summary.notes.append(f"figma: {exc}")
        return

    for note in ingested.notes:
        progress.note(note)
        summary.notes.append(note)
    if not ingested.frameMap:
        return

    progress.enter(Stage.match)
    matched = matching.run(paths, ctx, ingested.document, ingested.frameMap)
    for mapping in matched.mappings:
        progress.emit(
            "note",
            text=f"{mapping.frameName} → {mapping.pageId} @ {mapping.viewport}: "
            f"{mapping.matched} matched" + ("" if mapping.confident else " (could not match)"),
        )


async def _reason(
    request: RunRequest,
    paths: RunPaths,
    ctx: RunContext,
    payload: Any,
    progress: Progress,
    summary: RunSummary,
) -> Any:
    from engine.agents import pipeline

    progress.enter(Stage.reason)
    assert request.provider is not None
    config = request.agents or AgentConfig()
    # The agents get the knowledge as prose, which is what grounding wants; `apply`
    # gets the structured entries. Same notes, two readers.
    told = [n.raw for n in request.knowledge if n.confirmed and n.raw]
    result = await pipeline.reason_async(ctx, request.provider, config, knowledge=told)
    pipeline.write(paths, result)
    summary.aiFindings = len(result.findings)
    summary.aiCostUsd = round(result.budget.spent, 4)
    for finding in result.findings:
        progress.emit(
            "issue",
            severity=finding.severity.value,
            title=finding.title,
            checkerId=finding.checkerId,
            source=finding.source.value,
        )
    if result.stopped:
        progress.note(result.stopped)
        summary.notes.append(result.stopped)

    merged = pipeline.merge(ctx, payload, result)
    write_bytes(paths.issues, merged.model_dump_json(indent=2).encode() + b"\n")
    return merged
