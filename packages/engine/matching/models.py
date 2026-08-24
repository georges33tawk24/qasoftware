"""`mapping/{page_id}.{viewport}.json` — SPEC §4, §7.

SPEC §4 describes the file as a list of match records. It carries the surrounding context
too, because the build prompt is right that this is the file you live in when a false
positive needs explaining, and a score with no scale factor or anchor count next to it
explains nothing.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MatchModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class MatchRecord(MatchModel):
    figmaNodeId: str | None = None
    elementId: str | None = None
    score: float = 0.0
    method: str = "assignment"
    """`pinned`, `anchor`, `assignment`, or `unmatched`."""

    unmatched: bool = False
    signals: dict[str, float] = Field(default_factory=dict)
    nodeName: str | None = None
    nodeText: str | None = None
    selector: str | None = None
    containerNodeId: str | None = None
    containerElementId: str | None = None
    rejectedBecause: str | None = None


class MappingFile(MatchModel):
    pageId: str
    viewport: str
    frameId: str
    frameName: str
    scale: float
    """Figma frame width to live viewport width. Every delta is converted with this."""

    threshold: float
    anchors: int
    offsetX: float = 0.0
    offsetY: float = 0.0
    matched: int = 0
    unmatchedNodes: int = 0
    unmatchedElements: int = 0
    matches: list[MatchRecord] = Field(default_factory=list)

    @property
    def confident(self) -> bool:
        """Below a real match rate the frame is not this page, and every delta it would
        produce is noise. SPEC §7: one wrong match produces a page of nonsense."""
        total = self.matched + self.unmatchedNodes
        return total > 0 and self.matched / total >= 0.3 and self.anchors >= 2
