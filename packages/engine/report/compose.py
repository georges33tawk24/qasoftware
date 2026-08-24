"""Assemble the report model — SPEC §12.1.

Everything the report needs is computed here; `html.py` only renders. That split is what
lets the report be tested without parsing HTML.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PIL import Image

from engine.artifact.context import RunContext
from engine.artifact.models import Box
from engine.artifact.store import RunPaths
from engine.figma import ingest
from engine.issues.models import (
    Category,
    Evidence,
    EvidenceKind,
    Issue,
    IssuesFile,
    Severity,
)
from engine.report import sidebyside
from engine.report.annotate import Annotation, annotate

INLINE_BUDGET_BYTES = 6_000_000
"""Past this the images move to a sibling `media/` folder. The report has to survive
being emailed, and a 40MB attachment does not."""

MAX_CROP_WIDTH = 1200
DESIGN_EXPORT_SCALE = 2.0
"""SPEC §6 exports frames at 2x."""

SEVERITIES = [s.value for s in Severity]


@dataclass
class Media:
    """One annotated image, held until we know whether it can be inlined."""

    issue_id: str
    name: str
    data: bytes
    caption: str
    kind: EvidenceKind = EvidenceKind.crop

    @property
    def path(self) -> str:
        return f"media/{self.issue_id}/{self.name}"

    def data_uri(self) -> str:
        return "data:image/png;base64," + base64.b64encode(self.data).decode()


@dataclass
class Composed:
    payload: dict[str, Any] = field(default_factory=dict)
    media: list[Media] = field(default_factory=list)

    @property
    def inline(self) -> bool:
        return sum(len(m.data) for m in self.media) <= INLINE_BUDGET_BYTES


def _screenshot_for(ctx: RunContext, page_id: str, viewport: str) -> tuple[Path, float] | None:
    path = ctx.paths.full_png(page_id, viewport)
    if not path.is_file():
        return None
    scale = next((v.deviceScaleFactor for v in ctx.viewports if v.name == viewport), 1.0)
    return path, scale


def _shrink(data: bytes, tmp: Path) -> bytes:
    with Image.open(tmp) as image:
        if image.width <= MAX_CROP_WIDTH:
            return data
        ratio = MAX_CROP_WIDTH / image.width
        size = (MAX_CROP_WIDTH, int(image.height * ratio))
        resized = image.resize(size, Image.Resampling.LANCZOS)
        resized.save(tmp, format="PNG", optimize=True)
    return tmp.read_bytes()


def design_evidence(ctx: RunContext, issue: Issue, index: int, work: Path) -> Media | None:
    """Live and design, ringed on both — SPEC §12.2.

    Falls back to None (and therefore to the plain live crop) whenever the frame export
    is missing, which is every run against a file we could not fetch images for.
    """
    document = ctx.figma()
    if document is None:
        return None
    located = [i for i in issue.instances if i.box is not None and i.elementId]
    if not located:
        return None
    lead = located[0]

    mapping = ctx.mapping(lead.pageId, lead.viewport)
    if mapping is None:
        return None
    record = next(
        (m for m in mapping.matches if m.elementId == lead.elementId and m.figmaNodeId), None
    )
    node = document.nodes.get(record.figmaNodeId or "") if record else None
    if node is None:
        return None
    frame = document.frame(node.frameId)
    if frame is None:
        return None

    design_png = ingest.frame_png(ctx.paths, frame.id)
    live = _screenshot_for(ctx, lead.pageId, lead.viewport)
    if not design_png.is_file() or live is None:
        return None
    live_png, live_scale = live

    target = work / issue.id / "side-by-side.png"
    written = sidebyside.compose(
        live_png,
        design_png,
        live_box=lead.box,  # type: ignore[arg-type]  # filtered above
        design_box=Box(
            x=node.box.x - frame.box.x, y=node.box.y - frame.box.y, w=node.box.w, h=node.box.h
        ),
        live_scale=live_scale,
        design_scale=DESIGN_EXPORT_SCALE,
        number=index,
        severity=issue.severity,
        out=target,
    )
    if written is None:
        return None
    return Media(
        issue_id=issue.id,
        name="side-by-side.png",
        data=_shrink(target.read_bytes(), target),
        caption=f"{lead.pagePath} at {lead.viewport} · live vs {frame.name}",
    )


MAX_STEP_SHOTS = 6


def flow_evidence(ctx: RunContext, issue: Issue, work: Path) -> list[Media]:
    """A functional issue arrives with a screenshot per step, a trace and a video.

    The screenshots are inlined so the report still shows the failure when it is read on
    its own; the trace and the video stay as links, because they only mean anything next
    to the run they came from (SPEC §12.1).
    """
    shots = [e for e in issue.evidence if e.kind is EvidenceKind.screenshot]
    if not shots:
        return []
    keep = shots if len(shots) <= MAX_STEP_SHOTS else _spread(shots)
    out: list[Media] = []
    for position, evidence in enumerate(keep, start=1):
        source = ctx.paths.root / evidence.path
        if not source.is_file():
            continue
        target = work / issue.id / f"step-{position:02d}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        out.append(
            Media(
                issue_id=issue.id,
                name=target.name,
                data=_shrink(target.read_bytes(), target),
                caption=evidence.caption or "",
                kind=EvidenceKind.screenshot,
            )
        )
    return out


def _spread(shots: list[Evidence]) -> list[Evidence]:
    """An even spread ending on the last step: a twenty-step flow does not need twenty
    screenshots in the report to be understood, but it does need the one it failed on."""
    step = max(1, len(shots) // (MAX_STEP_SHOTS - 1))
    return [*shots[::step][: MAX_STEP_SHOTS - 1], shots[-1]]


def build_evidence(ctx: RunContext, issue: Issue, index: int, work: Path) -> Media | None:
    """One annotated crop per issue, ringed at every instance on the leading page.

    The number matches the issue's position in the list, which is the whole reason the
    ring is worth drawing at all (SPEC §12.2).
    """
    if issue.category is Category.figma:
        pair = design_evidence(ctx, issue, index, work)
        if pair is not None:
            return pair
    located = [i for i in issue.instances if i.box is not None]
    if not located:
        return None
    lead = located[0]
    found = _screenshot_for(ctx, lead.pageId, lead.viewport)
    if found is None:
        return None
    screenshot, scale = found

    same_surface = [
        i
        for i in located
        if i.pageId == lead.pageId and i.viewport == lead.viewport and i.box is not None
    ]
    annotations = [
        Annotation(number=index, box=i.box, severity=issue.severity)
        for i in same_surface
        if isinstance(i.box, Box)
    ]
    target = work / issue.id / "evidence.png"
    if annotate(screenshot, target, annotations, scale=scale) is None:
        return None
    return Media(
        issue_id=issue.id,
        name="evidence.png",
        data=_shrink(target.read_bytes(), target),
        caption=f"{lead.pagePath} at {lead.viewport}",
    )


def compose(ctx: RunContext, issues_file: IssuesFile, work: Path) -> Composed:
    result = Composed()
    manifest = ctx.manifest
    pages = {page.id: page for page in ctx.pages()}

    # SPEC §11: dismissed before it is rendered, not greyed out in the output. The counts,
    # the tally and the appendix all come from what is left.
    hidden = dismissed_fingerprints(ctx)
    if hidden:
        issues_file = issues_file.model_copy(
            update={"issues": [i for i in issues_file.issues if i.fingerprint not in hidden]}
        )

    # Grouped apart rather than dropped: an intermittent finding is still worth a look,
    # just not worth mixing into a list people are meant to work through in order.
    intermittent = flaky_fingerprints(ctx)
    steady = [i for i in issues_file.issues if i.fingerprint not in intermittent]
    unsteady = [i for i in issues_file.issues if i.fingerprint in intermittent]
    issues_file = issues_file.model_copy(update={"issues": steady})

    counts = dict.fromkeys(SEVERITIES, 0)
    for issue in issues_file.issues:
        counts[issue.severity.value] += 1

    issue_payloads: list[dict[str, Any]] = []
    for position, issue in enumerate(issues_file.issues, start=1):
        pictures = flow_evidence(ctx, issue, work)
        if not pictures:
            single = build_evidence(ctx, issue, position, work)
            pictures = [single] if single is not None else []
        result.media.extend(pictures)
        issue_payloads.append(_issue_payload(issue, position, pictures))

    noisy = {issue.checkerId for issue in issues_file.issues}
    result.payload = {
        "run": {
            "runId": manifest.runId,
            "target": manifest.target,
            "startedAt": manifest.startedAt.isoformat(),
            "durationMs": manifest.durationMs,
            "checkersSha": manifest.checkersSha,
            "driver": manifest.config.driver,
            "viewports": [v.name for v in manifest.config.viewports],
            "personas": manifest.config.personas,
            "figmaFile": manifest.config.figmaFileKey,
            "pageCount": len(pages),
            "blockedPages": sum(1 for p in ctx.pages() if p.crawlBlocked),
        },
        "counts": counts,
        "totals": {
            "issues": len(issues_file.issues),
            "instances": sum(i.instanceCount for i in issues_file.issues),
        },
        "diff": _diff(ctx),
        "requestedChanges": _requested(ctx),
        "flaky": [
            {
                "title": issue.title,
                "severity": issue.severity.value,
                "checkerId": issue.checkerId,
                "instances": issue.instanceCount,
            }
            for issue in unsteady
        ],
        "pages": [
            {
                "id": page.id,
                "path": page.path,
                "title": page.title,
                "status": page.status,
                "depth": page.depth,
                "discoveredFrom": page.discoveredFrom,
                "crawlBlocked": page.crawlBlocked,
            }
            for page in sorted(ctx.pages(), key=lambda p: (p.depth, p.path))
        ],
        "issues": issue_payloads,
        "appendix": {
            "silent": sorted(set(issues_file.checkersRan) - noisy),
            "reported": sorted(noisy),
            "skipped": issues_file.checkersSkipped,
        },
        "agents": _agent_report(ctx),
    }
    return result


def dismissed_fingerprints(ctx: RunContext) -> set[str]:
    """SPEC §11: an issue someone dismissed is filtered before the report is rendered.

    Read from the artifact rather than passed in, so re-rendering an old run hides the
    same issues it hid the first time.
    """
    from engine.artifact.store import read_json

    stored = read_json(ctx.paths.dismissed) or {}
    listed = stored.get("fingerprints") or []
    return {str(f) for f in listed}


def flaky_fingerprints(ctx: RunContext) -> set[str]:
    """Findings that come and go on an unchanged site — SPEC §5's flake control."""
    from engine.artifact.store import read_json

    stored = read_json(ctx.paths.dismissed) or {}
    listed = stored.get("flaky") or []
    return {str(f) for f in listed}


def _requested(ctx: RunContext) -> list[dict[str, Any]]:
    """SPEC §10's second line: what the client asked for, and whether they got it."""
    from engine.knowledge.models import KnowledgeFile

    if not ctx.paths.knowledge.is_file():
        return []
    knowledge = KnowledgeFile.model_validate_json(ctx.paths.knowledge.read_bytes())
    return [
        {
            "headline": change.headline(),
            "verdict": change.verdict.value,
            "detail": change.detail,
            "note": change.entry.note,
            "scope": change.entry.scope,
            "suppressed": change.suppressed,
            "pagePaths": change.pagePaths,
        }
        for change in knowledge.changes
    ]


def _diff(ctx: RunContext) -> dict[str, Any] | None:
    """SPEC §11. Absent on a first run, because "everything is new" is not information."""
    from engine.issues.diff import RunDiff

    if not ctx.paths.diff.is_file():
        return None
    run = RunDiff.model_validate_json(ctx.paths.diff.read_bytes())
    if not run.baseRunId:
        return None
    return {
        "baseRunId": run.baseRunId,
        "counts": run.counts(),
        "entries": [e.model_dump(mode="json") for e in run.entries],
    }


def _agent_report(ctx: RunContext) -> dict[str, Any] | None:
    """The internal dashboard SPEC §9.4 asks for: what each agent flagged, what survived
    verification, and what the run cost."""
    from engine.artifact.store import read_json

    calibration = read_json(ctx.paths.agents / "calibration.json")
    cost = read_json(ctx.paths.agents / "cost.json")
    if calibration is None and cost is None:
        return None
    return {"calibration": calibration, "cost": cost}


def _issue_payload(issue: Issue, position: int, media: list[Media]) -> dict[str, Any]:
    return {
        "n": position,
        "id": issue.id,
        "fingerprint": issue.fingerprint,
        "checkerId": issue.checkerId,
        "issueKind": issue.issueKind,
        "category": issue.category.value,
        "severity": issue.severity.value,
        "defaultSeverity": issue.defaultSeverity.value,
        "status": issue.status.value,
        "source": issue.source.value,
        "confidence": issue.confidence,
        "title": issue.title,
        "description": issue.description,
        "expected": issue.expected,
        "actual": issue.actual,
        "instanceCount": issue.instanceCount,
        "pagePaths": issue.pagePaths,
        "viewports": sorted({i.viewport for i in issue.instances}),
        "data": issue.data,
        "steps": issue.data.get("steps") or [],
        "evidence": [
            {"kind": item.kind.value, "src": item.path, "caption": item.caption} for item in media
        ]
        + [
            # Trace and video are only meaningful beside the run they came from, so they
            # stay as links rather than being inlined (SPEC §12.1).
            {"kind": e.kind.value, "src": e.path, "caption": e.caption or ""}
            for e in issue.evidence
            if e.kind in (EvidenceKind.trace, EvidenceKind.video, EvidenceKind.steps)
        ],
        "instances": [
            {
                "pagePath": i.pagePath,
                "viewport": i.viewport,
                "selector": i.selector,
                "actual": i.actual,
                "fingerprint": i.fingerprint,
            }
            for i in issue.instances
        ],
    }


def write_media(paths: RunPaths, composed: Composed) -> None:
    """Only called when the images are too big to inline."""
    for media in composed.media:
        target = paths.root / media.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(media.data)
