"""Normalised Figma nodes — SPEC §6.

The REST API returns colours as 0–1 floats, opacity nested through the tree, and two
different bounding boxes. This module turns all of that into records that can be compared
to `elements.json` directly, because the matching engine has enough to do without also
doing unit conversion.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from engine.artifact.models import Box

Quad = list[float]


class FigmaModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeRole(StrEnum):
    """The structural role signal from §7. Deliberately coarse: it is worth 0.10, and a
    finer taxonomy would only produce confident nonsense."""

    text = "text"
    heading = "heading"
    button = "button"
    input = "input"
    image = "image"
    icon = "icon"
    container = "container"
    frame = "frame"
    other = "other"


class TypeStyle(FigmaModel):
    fontFamily: str = ""
    fontWeight: int = 400
    fontSize: float = 0.0
    lineHeightPx: float | None = None
    letterSpacing: float = 0.0
    textAlign: str = "left"
    textCase: str = "ORIGINAL"


class Effect(FigmaModel):
    type: str
    colour: str | None = None
    radius: float = 0.0
    offsetX: float = 0.0
    offsetY: float = 0.0
    spread: float = 0.0

    def as_css(self) -> str:
        """Compared against `boxShadow`, so it has to speak the same language."""
        if self.type not in ("DROP_SHADOW", "INNER_SHADOW"):
            return "none"
        inset = "inset " if self.type == "INNER_SHADOW" else ""
        spread = f" {self.spread:g}px" if self.spread else ""
        return (
            f"{inset}{self.offsetX:g}px {self.offsetY:g}px {self.radius:g}px{spread} {self.colour}"
        )


class FigmaNode(FigmaModel):
    id: str
    name: str
    type: str
    role: NodeRole = NodeRole.other
    visible: bool = True

    box: Box
    renderBox: Box | None = None
    """`absoluteRenderBounds` — the visual edge once effects are drawn. SPEC §6 says to
    prefer it, so position checks use it and layout checks use `box`."""

    opacity: float = 1.0
    """Already multiplied down the ancestor chain."""

    fill: str | None = None
    stroke: str | None = None
    strokeWeight: float = 0.0
    cornerRadius: Quad = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])
    effects: list[Effect] = Field(default_factory=list)

    characters: str | None = None
    labelText: str | None = None
    """A designer draws a button as a filled frame with a label node inside it; the DOM
    keeps both on one element. Treating a container's label as its own text is what makes
    those two descriptions of the same button comparable."""

    style: TypeStyle | None = None

    layoutMode: str | None = None
    itemSpacing: float = 0.0
    padding: Quad = Field(default_factory=lambda: [0.0, 0.0, 0.0, 0.0])

    imageRef: str | None = None
    parentId: str | None = None
    childIds: list[str] = Field(default_factory=list)
    frameId: str = ""
    depth: int = 0

    @property
    def text(self) -> str:
        return (self.characters or self.labelText or "").strip()

    @property
    def shadow(self) -> str:
        visible = [e for e in self.effects if e.type in ("DROP_SHADOW", "INNER_SHADOW")]
        return ", ".join(e.as_css() for e in visible) if visible else "none"


class Frame(FigmaModel):
    """A top-level frame — one screen of the design."""

    id: str
    name: str
    box: Box
    pageName: str = ""
    nodeIds: list[str] = Field(default_factory=list)


class FigmaDocument(FigmaModel):
    fileKey: str
    name: str = ""
    version: str = ""
    lastModified: str = ""
    frames: list[Frame] = Field(default_factory=list)
    nodes: dict[str, FigmaNode] = Field(default_factory=dict)

    def frame(self, frame_id: str) -> Frame | None:
        return next((f for f in self.frames if f.id == frame_id), None)

    def nodes_in(self, frame_id: str) -> list[FigmaNode]:
        frame = self.frame(frame_id)
        return [self.nodes[i] for i in frame.nodeIds if i in self.nodes] if frame else []


class Tokens(FigmaModel):
    """`figma/tokens.json` — SPEC §6.

    Extracted even when only a desktop frame exists: these are what let the deterministic
    checkers judge a tablet or mobile viewport that has no design frame at all.
    """

    palette: list[str] = Field(default_factory=list)
    paletteUsage: dict[str, int] = Field(default_factory=dict)
    typeScale: list[float] = Field(default_factory=list)
    fontFamilies: list[str] = Field(default_factory=list)
    fontWeights: list[int] = Field(default_factory=list)
    spacing: list[float] = Field(default_factory=list)
    radii: list[float] = Field(default_factory=list)
    strokeWidths: list[float] = Field(default_factory=list)
    shadows: list[str] = Field(default_factory=list)
