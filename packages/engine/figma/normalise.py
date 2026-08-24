"""Flatten the Figma REST response into comparable records — SPEC §6."""

from __future__ import annotations

from typing import Any

from engine.artifact.models import Box
from engine.figma.models import Effect, FigmaDocument, FigmaNode, Frame, NodeRole, TypeStyle

HEADING_HINTS = ("title", "heading", "headline", "h1", "h2", "h3")
BUTTON_HINTS = ("button", "btn", "cta", "action")
INPUT_HINTS = ("input", "field", "textbox", "select")
ICON_HINTS = ("icon", "glyph", "logo-mark")


def colour(value: dict[str, Any] | None, opacity: float = 1.0) -> str | None:
    """Figma colours are 0–1 floats. Everything downstream speaks CSS."""
    if not value:
        return None
    alpha = float(value.get("a", 1.0)) * opacity
    r, g, b = (round(float(value.get(k, 0.0)) * 255) for k in ("r", "g", "b"))
    if alpha >= 0.999:
        return f"rgb({r}, {g}, {b})"
    return f"rgba({r}, {g}, {b}, {round(alpha, 3)})"


def _first_visible_paint(paints: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    for paint in paints or []:
        if paint.get("visible", True) and paint.get("type") != "IMAGE":
            return paint
    return None


def _image_ref(paints: list[dict[str, Any]] | None) -> str | None:
    for paint in paints or []:
        if paint.get("visible", True) and paint.get("type") == "IMAGE":
            ref = paint.get("imageRef")
            return str(ref) if ref else None
    return None


def _box(raw: dict[str, Any] | None) -> Box | None:
    if not raw:
        return None
    return Box(
        x=round(float(raw.get("x", 0.0)), 2),
        y=round(float(raw.get("y", 0.0)), 2),
        w=round(float(raw.get("width", 0.0)), 2),
        h=round(float(raw.get("height", 0.0)), 2),
    )


def _corner_radius(raw: dict[str, Any]) -> list[float]:
    corners = raw.get("rectangleCornerRadii")
    if isinstance(corners, list) and len(corners) == 4:
        return [float(c) for c in corners]
    single = float(raw.get("cornerRadius", 0.0) or 0.0)
    return [single] * 4


def _padding(raw: dict[str, Any]) -> list[float]:
    return [
        float(raw.get("paddingTop", 0.0) or 0.0),
        float(raw.get("paddingRight", 0.0) or 0.0),
        float(raw.get("paddingBottom", 0.0) or 0.0),
        float(raw.get("paddingLeft", 0.0) or 0.0),
    ]


def _type_style(raw: dict[str, Any] | None) -> TypeStyle | None:
    if not raw:
        return None
    size = float(raw.get("fontSize", 0.0) or 0.0)
    line_height = raw.get("lineHeightPx")
    if line_height is None and raw.get("lineHeightPercentFontSize") and size:
        line_height = size * float(raw["lineHeightPercentFontSize"]) / 100
    return TypeStyle(
        fontFamily=str(raw.get("fontFamily", "")),
        fontWeight=int(raw.get("fontWeight", 400) or 400),
        fontSize=round(size, 2),
        lineHeightPx=round(float(line_height), 2) if line_height else None,
        letterSpacing=round(float(raw.get("letterSpacing", 0.0) or 0.0), 2),
        textAlign=str(raw.get("textAlignHorizontal", "LEFT")).lower(),
        textCase=str(raw.get("textCase", "ORIGINAL")),
    )


def role_of(raw: dict[str, Any], style: TypeStyle | None) -> NodeRole:
    """Layer-name conventions plus node type, which is all Figma gives you (SPEC §7)."""
    name = str(raw.get("name", "")).lower()
    kind = str(raw.get("type", ""))

    if kind == "TEXT":
        if any(hint in name for hint in HEADING_HINTS):
            return NodeRole.heading
        if any(hint in name for hint in BUTTON_HINTS):
            return NodeRole.button
        if style and style.fontSize >= 24:
            return NodeRole.heading
        return NodeRole.text
    if any(hint in name for hint in BUTTON_HINTS):
        return NodeRole.button
    if any(hint in name for hint in INPUT_HINTS):
        return NodeRole.input
    if any(hint in name for hint in ICON_HINTS):
        return NodeRole.icon
    if _image_ref(raw.get("fills")):
        return NodeRole.image
    if kind in ("RECTANGLE", "ELLIPSE", "VECTOR", "LINE", "BOOLEAN_OPERATION"):
        return NodeRole.other
    if kind in ("FRAME", "GROUP", "COMPONENT", "INSTANCE", "SECTION"):
        return NodeRole.container
    return NodeRole.other


def normalise(raw_file: dict[str, Any], file_key: str) -> FigmaDocument:
    """Walk the document and flatten every frame's subtree."""
    document = FigmaDocument(
        fileKey=file_key,
        name=str(raw_file.get("name", "")),
        version=str(raw_file.get("version", "")),
        lastModified=str(raw_file.get("lastModified", "")),
    )

    for canvas in raw_file.get("document", {}).get("children", []):
        if canvas.get("type") != "CANVAS":
            continue
        page_name = str(canvas.get("name", ""))
        for child in canvas.get("children", []):
            if child.get("type") not in ("FRAME", "COMPONENT", "SECTION"):
                continue
            box = _box(child.get("absoluteBoundingBox"))
            if box is None:
                continue
            frame = Frame(
                id=str(child["id"]), name=str(child.get("name", "")), box=box, pageName=page_name
            )
            _walk(child, document, frame, parent_id=None, opacity=1.0, depth=0)
            document.frames.append(frame)
    return document


def _label_text(raw: dict[str, Any]) -> str | None:
    """The text of a container's own label nodes, one level down."""
    if raw.get("characters") or raw.get("type") not in ("FRAME", "GROUP", "COMPONENT", "INSTANCE"):
        return None
    labels = [
        str(child.get("characters", "")).strip()
        for child in raw.get("children", [])
        if child.get("type") == "TEXT" and child.get("visible", True) and child.get("characters")
    ]
    return " ".join(labels) or None


def _walk(
    raw: dict[str, Any],
    document: FigmaDocument,
    frame: Frame,
    *,
    parent_id: str | None,
    opacity: float,
    depth: int,
) -> None:
    box = _box(raw.get("absoluteBoundingBox"))
    if box is None:
        return
    # Opacity nests in Figma and does not in CSS; multiplying here means every downstream
    # comparison sees the value the eye sees.
    effective = opacity * float(raw.get("opacity", 1.0) or 1.0)
    visible = bool(raw.get("visible", True))

    style = _type_style(raw.get("style"))
    fill_paint = _first_visible_paint(raw.get("fills"))
    stroke_paint = _first_visible_paint(raw.get("strokes"))

    node = FigmaNode(
        id=str(raw["id"]),
        name=str(raw.get("name", "")),
        type=str(raw.get("type", "")),
        role=role_of(raw, style),
        visible=visible,
        box=box,
        renderBox=_box(raw.get("absoluteRenderBounds")),
        opacity=round(effective, 3),
        fill=colour(fill_paint.get("color"), float(fill_paint.get("opacity", 1.0)))
        if fill_paint
        else None,
        stroke=colour(stroke_paint.get("color"), float(stroke_paint.get("opacity", 1.0)))
        if stroke_paint
        else None,
        strokeWeight=float(raw.get("strokeWeight", 0.0) or 0.0),
        cornerRadius=_corner_radius(raw),
        effects=[
            Effect(
                type=str(effect.get("type", "")),
                colour=colour(effect.get("color")),
                radius=float(effect.get("radius", 0.0) or 0.0),
                offsetX=float((effect.get("offset") or {}).get("x", 0.0)),
                offsetY=float((effect.get("offset") or {}).get("y", 0.0)),
                spread=float(effect.get("spread", 0.0) or 0.0),
            )
            for effect in raw.get("effects", [])
            if effect.get("visible", True)
        ],
        characters=raw.get("characters"),
        labelText=_label_text(raw),
        style=style,
        layoutMode=raw.get("layoutMode"),
        itemSpacing=float(raw.get("itemSpacing", 0.0) or 0.0),
        padding=_padding(raw),
        imageRef=_image_ref(raw.get("fills")),
        parentId=parent_id,
        childIds=[str(c["id"]) for c in raw.get("children", []) if "id" in c],
        frameId=frame.id,
        depth=depth,
    )
    document.nodes[node.id] = node
    frame.nodeIds.append(node.id)

    if not visible:
        return  # a hidden branch is not in the design either
    for child in raw.get("children", []):
        _walk(child, document, frame, parent_id=node.id, opacity=effective, depth=depth + 1)
