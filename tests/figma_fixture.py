"""Build a synthetic Figma file from a captured page — the design side of the fixture.

There is no real Figma file to point at, so one is generated from a real capture and then
deliberately walked away from. Every delta planted here is listed in
`tests/fixtures/figma/DELTAS.md` and asserted by `tests/test_figma.py`.

Generating the design *from* the page is the honest way round: it means every difference
the matcher reports is a difference this file put there on purpose, and a false positive
has nowhere to hide.
"""

from __future__ import annotations

import re
from typing import Any

from engine.artifact.models import ElementRecord
from engine.checkers import colour as colour_maths

FRAME_ORIGIN = (120.0, 240.0)
"""Frames live somewhere on a Figma canvas, never at the origin. Anything that assumes
otherwise breaks on the first real file."""

TRANSPARENT = "rgba(0, 0, 0, 0)"


def to_figma_colour(css: str | None) -> dict[str, float] | None:
    parsed = colour_maths.parse(css or "")
    if parsed is None or parsed[3] == 0:
        return None
    return {
        "r": round(parsed[0] / 255, 6),
        "g": round(parsed[1] / 255, 6),
        "b": round(parsed[2] / 255, 6),
        "a": round(parsed[3], 3),
    }


def solid(css: str | None) -> list[dict[str, Any]]:
    value = to_figma_colour(css)
    return [{"blendMode": "NORMAL", "type": "SOLID", "color": value}] if value else []


def layer_name(element: ElementRecord) -> str:
    if element.tag in ("h1", "h2", "h3"):
        return f"Heading/{element.text[:24] or element.tag}"
    if element.clickable:
        return f"Button/{element.text[:24] or element.tag}"
    if element.image:
        return "Image/logo"
    if element.classes:
        return "/".join(part.title() for part in re.split(r"[-_]", element.classes[0]))[:32]
    return element.tag.title()


def significant(element: ElementRecord) -> bool:
    if not element.visible or element.box.w <= 0 or element.box.h <= 0:
        return False
    return bool(
        element.text
        or element.image
        or element.clickable
        or element.styles.backgroundColor != TRANSPARENT
        or any(w > 0 for w in element.styles.borderWidth)
    )


def _node(element: ElementRecord, children: list[dict[str, Any]]) -> dict[str, Any]:
    styles = element.styles
    box = {
        "x": FRAME_ORIGIN[0] + element.box.x,
        "y": FRAME_ORIGIN[1] + element.box.y,
        "width": element.box.w,
        "height": element.box.h,
    }
    node: dict[str, Any] = {
        "id": f"1:{abs(hash(element.id)) % 90000 + 1000}",
        "name": layer_name(element),
        "visible": True,
        "opacity": styles.opacity,
        "absoluteBoundingBox": box,
        "absoluteRenderBounds": dict(box),
        "blendMode": "PASS_THROUGH",
    }

    text_style = {
        "fontFamily": styles.fontFamily.split(",")[0].strip().strip("\"'"),
        "fontWeight": styles.fontWeight,
        "fontSize": styles.fontSize,
        "lineHeightPx": styles.lineHeight or styles.fontSize * 1.4,
        "letterSpacing": styles.letterSpacing,
        "textAlignHorizontal": styles.textAlign.upper(),
    }
    decorated = styles.backgroundColor != TRANSPARENT or any(styles.borderRadius)

    if element.text and decorated:
        # A designer draws a button as a filled frame with a label inside it. The DOM
        # keeps both on one element, and that mismatch is worth having in the fixture.
        node["type"] = "FRAME"
        node["fills"] = solid(styles.backgroundColor)
        node["children"] = [
            {
                "id": node["id"] + ":label",
                "name": "Label",
                "type": "TEXT",
                "visible": True,
                "opacity": 1.0,
                "characters": element.text,
                "absoluteBoundingBox": dict(box),
                "fills": solid(styles.color),
                "style": text_style,
            }
        ]
    elif element.text:
        node["type"] = "TEXT"
        node["characters"] = element.text
        node["fills"] = solid(styles.color)
        node["style"] = text_style
    elif element.image:
        node["type"] = "RECTANGLE"
        node["fills"] = [
            {"blendMode": "NORMAL", "type": "IMAGE", "imageRef": "img-1", "scaleMode": "FILL"}
        ]
    else:
        node["type"] = "FRAME" if children else "RECTANGLE"
        node["fills"] = solid(styles.backgroundColor)

    if any(w > 0 for w in styles.borderWidth):
        node["strokes"] = solid(styles.borderColor)
        node["strokeWeight"] = styles.borderWidth[0]
    if any(styles.borderRadius):
        node["cornerRadius"] = styles.borderRadius[0]
    if styles.gap:
        node["layoutMode"] = "HORIZONTAL" if styles.flexDirection == "row" else "VERTICAL"
        node["itemSpacing"] = styles.gap
    if any((styles.paddingTop, styles.paddingRight, styles.paddingBottom, styles.paddingLeft)):
        node["paddingTop"] = styles.paddingTop
        node["paddingRight"] = styles.paddingRight
        node["paddingBottom"] = styles.paddingBottom
        node["paddingLeft"] = styles.paddingLeft
    if styles.boxShadow != "none":
        node["effects"] = [
            {
                "type": "DROP_SHADOW",
                "visible": True,
                "color": {"r": 0, "g": 0, "b": 0, "a": 0.05},
                "offset": {"x": 0, "y": 1},
                "radius": 2,
                "spread": 0,
            }
        ]
    if children:
        node["children"] = node.get("children", []) + children
    return node


def build_file(
    elements: list[ElementRecord],
    *,
    frame_name: str,
    frame_width: float,
    file_name: str = "Fixture design",
    version: str = "1",
) -> dict[str, Any]:
    """Mirror the element tree, keeping only what a designer would have drawn."""
    index = {e.id: e for e in elements}
    root = next(e for e in elements if e.parentId is None)

    def build(element: ElementRecord) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for child_id in element.childIds:
            child = index.get(child_id)
            if child is None:
                continue
            if significant(child):
                out.append(_node(child, build(child)))
            else:
                out.extend(build(child))
        return out

    height = max((e.box.y + e.box.h for e in elements if e.visible), default=900.0)
    frame = {
        "id": "1:1",
        "name": frame_name,
        "type": "FRAME",
        "visible": True,
        "opacity": 1.0,
        "absoluteBoundingBox": {
            "x": FRAME_ORIGIN[0],
            "y": FRAME_ORIGIN[1],
            "width": frame_width,
            "height": height,
        },
        "fills": solid("rgb(255, 255, 255)"),
        "children": build(root),
    }
    return {
        "name": file_name,
        "version": version,
        "lastModified": "2026-01-15T09:00:00Z",
        "document": {
            "id": "0:0",
            "name": "Document",
            "type": "DOCUMENT",
            "children": [{"id": "0:1", "name": "Page 1", "type": "CANVAS", "children": [frame]}],
        },
    }


# ------------------------------------------------------------------ the deltas


def find(node: dict[str, Any], predicate: Any) -> dict[str, Any] | None:
    if predicate(node):
        return node
    for child in node.get("children", []):
        found = find(child, predicate)
        if found is not None:
            return found
    return None


def find_all(node: dict[str, Any], predicate: Any) -> list[dict[str, Any]]:
    out = [node] if predicate(node) else []
    for child in node.get("children", []):
        out.extend(find_all(child, predicate))
    return out


def shift(node: dict[str, Any], dx: float, dy: float) -> None:
    """Move a node and everything inside it, the way dragging a frame in Figma does."""
    for key in ("absoluteBoundingBox", "absoluteRenderBounds"):
        box = node.get(key)
        if box:
            box["x"] += dx
            box["y"] += dy
    for child in node.get("children", []):
        shift(child, dx, dy)


def by_text(text: str) -> Any:
    return lambda n: (n.get("characters") or "").strip() == text


def by_name(name: str) -> Any:
    return lambda n: n.get("name") == name


# --------------------------------------------------------------- frame preview


def render_frame(node: dict[str, Any], out: Any, scale: int = 2) -> None:
    """Draw the frame as a flat image, standing in for a Figma 2x export.

    A real run downloads this from the images endpoint. The fixture has to produce one
    somehow, and drawing the node tree is the only version that is guaranteed to agree
    with the node tree.
    """
    from PIL import Image, ImageDraw, ImageFont

    box = node["absoluteBoundingBox"]
    origin = (box["x"], box["y"])
    image = Image.new(
        "RGB", (int(box["width"] * scale), int(box["height"] * scale)), (255, 255, 255)
    )
    draw = ImageDraw.Draw(image)

    def css(colour: dict[str, float] | None) -> tuple[int, int, int] | None:
        if not colour:
            return None
        return (round(colour["r"] * 255), round(colour["g"] * 255), round(colour["b"] * 255))

    def paint(current: dict[str, Any]) -> None:
        current_box = current.get("absoluteBoundingBox")
        if current_box:
            left = (current_box["x"] - origin[0]) * scale
            top = (current_box["y"] - origin[1]) * scale
            right = left + current_box["width"] * scale
            bottom = top + current_box["height"] * scale
            fill = next(
                (css(p.get("color")) for p in current.get("fills", []) if p.get("type") == "SOLID"),
                None,
            )
            stroke = next(
                (
                    css(p.get("color"))
                    for p in current.get("strokes", [])
                    if p.get("type") == "SOLID"
                ),
                None,
            )
            radius = int(float(current.get("cornerRadius", 0) or 0) * scale)
            if current.get("type") == "TEXT":
                style = current.get("style", {})
                size = max(8, int(float(style.get("fontSize", 16)) * scale))
                try:
                    font = ImageFont.load_default(size=size)
                except (AttributeError, TypeError):
                    font = ImageFont.load_default()
                draw.text(
                    (left, top),
                    str(current.get("characters", "")),
                    fill=fill or (17, 17, 17),
                    font=font,
                )
            elif fill or stroke:
                if radius:
                    draw.rounded_rectangle(
                        (left, top, right, bottom),
                        radius=radius,
                        fill=fill,
                        outline=stroke,
                        width=max(1, int(float(current.get("strokeWeight", 1)) * scale)),
                    )
                else:
                    draw.rectangle(
                        (left, top, right, bottom),
                        fill=fill,
                        outline=stroke,
                        width=max(1, int(float(current.get("strokeWeight", 1)) * scale)),
                    )
        for child in current.get("children", []):
            paint(child)

    paint(node)
    image.save(out, format="PNG", optimize=True)
