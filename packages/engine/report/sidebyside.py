"""Side-by-side evidence — SPEC §12.2.

Design frame and live page, the same region ringed on both, matched heights, a 24px
gutter, captioned Live and Design. The point is that the reader can see the difference
without being told where to look.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from engine.artifact.models import Box
from engine.issues.models import Severity
from engine.report.annotate import SEVERITY_COLOUR, Annotation, render

GUTTER = 24
CAPTION_HEIGHT = 22
BACKGROUND = (14, 15, 17)
CAPTION_INK = (230, 231, 234)
MAX_PANE_WIDTH = 620


def _font(size: int = 13) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=size)
    except (AttributeError, TypeError):  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def _fit(image: Image.Image, height: int) -> Image.Image:
    """Matched heights: the two crops are of different things at different scales, and
    the eye cannot compare them unless they line up."""
    if image.height == height:
        return image
    width = max(1, round(image.width * height / image.height))
    return image.resize((width, height), Image.Resampling.LANCZOS)


def compose(
    live_png: Path,
    design_png: Path,
    *,
    live_box: Box,
    design_box: Box,
    live_scale: float,
    design_scale: float,
    number: int,
    severity: Severity,
    out: Path,
) -> Path | None:
    live = render(
        live_png, [Annotation(number=number, box=live_box, severity=severity)], scale=live_scale
    )
    design = render(
        design_png,
        [Annotation(number=number, box=design_box, severity=severity)],
        scale=design_scale,
    )
    if live is None or design is None:
        return None

    height = min(max(live.height, design.height), 900)
    live, design = _fit(live, height), _fit(design, height)
    for pane in (live, design):
        if pane.width > MAX_PANE_WIDTH:
            height = min(height, round(height * MAX_PANE_WIDTH / pane.width))
    live, design = _fit(live, height), _fit(design, height)

    width = live.width + GUTTER + design.width
    canvas = Image.new("RGB", (width, height + CAPTION_HEIGHT), BACKGROUND)
    canvas.paste(live, (0, CAPTION_HEIGHT))
    canvas.paste(design, (live.width + GUTTER, CAPTION_HEIGHT))

    draw = ImageDraw.Draw(canvas)
    font = _font()
    draw.text((2, 4), "Live", fill=CAPTION_INK, font=font)
    draw.text((live.width + GUTTER + 2, 4), "Design", fill=CAPTION_INK, font=font)
    accent = SEVERITY_COLOUR[severity]
    draw.line(
        (live.width + GUTTER // 2, 0, live.width + GUTTER // 2, height + CAPTION_HEIGHT),
        fill=accent,
        width=1,
    )

    out.parent.mkdir(parents=True, exist_ok=True)
    canvas.save(out, format="PNG", optimize=True)
    return out
