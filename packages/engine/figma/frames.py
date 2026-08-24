"""Frame to route mapping — SPEC §6.

Three routes: by frame name against the URL path, by content similarity, and by hand.
The first two only ever *propose*. A wrong frame-to-page pairing means every delta on
that page is nonsense, so the pairing is confirmed once by a human and then reused
forever (SPEC §6, §7).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from engine.artifact.context import RunContext
from engine.artifact.models import PageRecord
from engine.figma.models import FigmaDocument, Frame
from engine.matching.signals import levenshtein_ratio, normalise_text

NAME_WEIGHT = 0.3
TITLE_WEIGHT = 0.2
CONTENT_WEIGHT = 0.5
"""Content overlap carries the most because it is the signal that survives a frame called
`Desktop / Final v3`, which is most of them."""

SUGGEST_THRESHOLD = 0.45
STRONG_CONTENT = 0.5
"""Half the design's copy appearing on a page is enough on its own — no frame name is
that informative."""

_SPLIT = re.compile(r"[\s_\-/.]+")


def slug(value: str) -> str:
    return " ".join(part for part in _SPLIT.split(value.casefold()) if part and part != "html")


@dataclass
class Proposal:
    frameId: str
    frameName: str
    pageId: str
    pagePath: str
    score: float
    reasons: dict[str, float] = field(default_factory=dict)

    @property
    def suggested(self) -> bool:
        return self.score >= SUGGEST_THRESHOLD or self.reasons.get("content", 0.0) >= STRONG_CONTENT


def _content_overlap(
    document: FigmaDocument, frame: Frame, ctx: RunContext, page: PageRecord
) -> float:
    """Heading text that appears on both sides. The most reliable of the automatic
    signals, and the one that survives a frame called `Desktop / Final v3`."""
    design = {
        normalise_text(node.text)
        for node in document.nodes_in(frame.id)
        if node.text and node.role.value in ("heading", "text", "button")
    }
    design.discard("")
    if not design:
        return 0.0

    viewports = ctx.viewport_names(page.id)
    if not viewports:
        return 0.0
    live = {normalise_text(e.text) for e in ctx.elements(page.id, viewports[-1]) if e.text}
    live.discard("")
    if not live:
        return 0.0
    return len(design & live) / len(design)


def propose(document: FigmaDocument, ctx: RunContext) -> list[Proposal]:
    pages = [p for p in ctx.pages() if not p.crawlBlocked]
    out: list[Proposal] = []
    for frame in document.frames:
        name = slug(frame.name)
        for page in pages:
            path = slug(page.path)
            title = slug(page.title or "")
            reasons = {
                "name": round(levenshtein_ratio(name, path or "home"), 3),
                "title": round(levenshtein_ratio(name, title), 3) if title else 0.0,
                "content": round(_content_overlap(document, frame, ctx, page), 3),
            }
            score = (
                reasons["name"] * NAME_WEIGHT
                + reasons["title"] * TITLE_WEIGHT
                + reasons["content"] * CONTENT_WEIGHT
            )
            out.append(
                Proposal(
                    frameId=frame.id,
                    frameName=frame.name,
                    pageId=page.id,
                    pagePath=page.path,
                    score=round(score, 3),
                    reasons=reasons,
                )
            )
    return sorted(out, key=lambda p: -p.score)


def best_per_frame(proposals: list[Proposal]) -> dict[str, Proposal]:
    best: dict[str, Proposal] = {}
    for proposal in proposals:
        current = best.get(proposal.frameId)
        if current is None or proposal.score > current.score:
            best[proposal.frameId] = proposal
    return best


def resolve(
    document: FigmaDocument,
    ctx: RunContext,
    *,
    confirmed: dict[str, str],
    accept_suggested: bool = False,
) -> dict[str, str]:
    """frame id → page id.

    `confirmed` is keyed by frame id or frame name and valued by page path or page id —
    whatever a human found natural to type. Nothing else is used unless the caller has
    explicitly opted into the automatic guess.
    """
    pages = {p.id: p for p in ctx.pages()}
    by_path = {p.path: p.id for p in pages.values()}

    mapping: dict[str, str] = {}
    for frame in document.frames:
        target = confirmed.get(frame.id) or confirmed.get(frame.name)
        if target:
            page_id = target if target in pages else by_path.get(target)
            if page_id:
                mapping[frame.id] = page_id
            continue

    if accept_suggested:
        for frame_id, proposal in best_per_frame(propose(document, ctx)).items():
            if frame_id not in mapping and proposal.suggested:
                mapping[frame_id] = proposal.pageId
    return mapping
