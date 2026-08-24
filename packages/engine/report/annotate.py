"""Annotated evidence — SPEC §12.2.

Drawn on the captured PNG with Pillow rather than injected into the page, so the
screenshot itself stays honest: what the report shows is what the browser rendered, with
a ring added on top.

Crops are the region plus 15% context. A full-page screenshot with a tiny circle in it
is useless.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from engine.artifact.models import Box
from engine.checkers import colour
from engine.issues.models import Severity

SEVERITY_COLOUR = {
    Severity.blocker: "#C7443A",
    Severity.critical: "#D2683C",
    Severity.major: "#C99A2E",
    Severity.minor: "#5C7FA8",
    Severity.trivial: "#6A6E78",
}
"""SPEC §16's severity palette, so the report and the UI agree."""

CONTEXT_RATIO = 0.15
MIN_CONTEXT_PX = 32
MIN_CROP_PX = 420
"""In CSS pixels; scaled by the device pixel ratio before use, or a 2x mobile capture
crops to a third of what a reader needs."""

MAX_CROP_PX = 1400
"""The union of several rings stops growing here. Past it the crop is a full-page
screenshot with rings in it, which SPEC §12.2 is explicit about being useless."""

RING_WIDTH = 3
HALO_WIDTH = 2
MIN_RING_CONTRAST = 3.0
LABEL_PAD = 6
LABEL_HEIGHT = 22


@dataclass(frozen=True)
class Annotation:
    number: int
    box: Box
    severity: Severity


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(value: int) -> float:
        c = value / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2])


def _contrast(a: tuple[int, int, int], b: tuple[int, int, int]) -> float:
    first, second = _luminance(a) + 0.05, _luminance(b) + 0.05
    return first / second if first > second else second / first


def sample_background(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    """The average colour immediately around the region, which is what the ring has to
    stand out against."""
    left, top, right, bottom = box
    margin = 12
    # Clamped both ways: an element can sit below the bottom of its own screenshot when a
    # page grew after the capture, and Pillow raises on an inverted rectangle rather than
    # returning nothing. A report that cannot draw one ring must still draw the rest.
    x0 = min(max(0, left - margin), image.width)
    y0 = min(max(0, top - margin), image.height)
    x1 = max(x0, min(image.width, right + margin))
    y1 = max(y0, min(image.height, bottom + margin))
    frame = image.crop((x0, y0, x1, y1)).convert("RGB")
    if frame.width == 0 or frame.height == 0:
        return (255, 255, 255)
    small = frame.resize((1, 1), Image.Resampling.BILINEAR)
    pixel = small.getpixel((0, 0))
    assert isinstance(pixel, tuple)
    return (int(pixel[0]), int(pixel[1]), int(pixel[2]))


def ring_colours(
    severity: Severity, background: tuple[int, int, int]
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """The severity colour, plus a halo when it would not stand out on its own.

    Keeping the severity colour matters — it is how the reader reads the report at a
    glance — so contrast is bought with a halo rather than by changing the hue.
    """
    parsed = colour.parse(SEVERITY_COLOUR[severity])
    assert parsed is not None
    ring = (int(parsed[0]), int(parsed[1]), int(parsed[2]))
    if _contrast(ring, background) >= MIN_RING_CONTRAST:
        return ring, ring
    halo = (255, 255, 255) if _luminance(background) < 0.4 else (14, 15, 17)
    return ring, halo


def crop_window(
    image: Image.Image, boxes: list[tuple[int, int, int, int]], scale: float = 1.0
) -> tuple[int, int, int, int]:
    """Frame the first ring, and any others close enough to fit beside it.

    Cropping to the first ring alone cuts the second one in half; cropping to all of them
    on a long page is the full-page screenshot §12.2 warns about. Growing from the first
    until the window hits its cap is the version that reads.
    """
    minimum = int(MIN_CROP_PX * scale)
    maximum = int(MAX_CROP_PX * scale)
    left, top, right, bottom = boxes[0]
    for other in boxes[1:]:
        merged = (
            min(left, other[0]),
            min(top, other[1]),
            max(right, other[2]),
            max(bottom, other[3]),
        )
        if merged[2] - merged[0] > maximum or merged[3] - merged[1] > maximum:
            continue
        left, top, right, bottom = merged

    pad_x = max(int(MIN_CONTEXT_PX * scale), int((right - left) * CONTEXT_RATIO))
    pad_y = max(int(MIN_CONTEXT_PX * scale), int((bottom - top) * CONTEXT_RATIO))
    left, top = left - pad_x, top - pad_y - int(LABEL_HEIGHT * scale)
    right, bottom = right + pad_x, bottom + pad_y

    # A ringed 20px icon in a 40px crop tells you nothing about where it sits.
    if right - left < minimum:
        grow = (minimum - (right - left)) // 2
        left, right = left - grow, right + grow
    if bottom - top < minimum:
        grow = (minimum - (bottom - top)) // 2
        top, bottom = top - grow, bottom + grow

    # Slide the window back inside the image rather than truncating it: a region against
    # the left edge deserves the same amount of context as one in the middle.
    left, right = _fit(left, right, image.width)
    top, bottom = _fit(top, bottom, image.height)
    return (left, top, right, bottom)


def _fit(low: int, high: int, limit: int) -> tuple[int, int]:
    span = min(high - low, limit)
    low = max(0, min(low, limit - span))
    return low, low + span


def annotate(
    screenshot: Path,
    out: Path,
    annotations: list[Annotation],
    *,
    scale: float = 1.0,
) -> Path | None:
    """Ring every annotation, crop to the first one, and number them to match the list."""
    cropped = render(screenshot, annotations, scale=scale)
    if cropped is None:
        return None
    out.parent.mkdir(parents=True, exist_ok=True)
    cropped.save(out, format="PNG", optimize=True)
    return out


def render(
    screenshot: Path, annotations: list[Annotation], *, scale: float = 1.0
) -> Image.Image | None:
    """The ringed crop itself, for callers that composite it with something else."""
    if not screenshot.is_file() or not annotations:
        return None
    with Image.open(screenshot) as raw:
        image = raw.convert("RGB")

    def device(box: Box) -> tuple[int, int, int, int]:
        return (
            int(box.x * scale),
            int(box.y * scale),
            int((box.x + box.w) * scale),
            int((box.y + box.h) * scale),
        )

    draw = ImageDraw.Draw(image)
    font = _font(scale)
    drawn: list[tuple[int, int, int, int]] = []
    for annotation in annotations:
        left, top, right, bottom = device(annotation.box)
        if right <= left or bottom <= top:
            continue
        drawn.append((left, top, right, bottom))
        ring, halo = ring_colours(
            annotation.severity, sample_background(image, (left, top, right, bottom))
        )
        if halo != ring:
            draw.rectangle(
                (left - HALO_WIDTH, top - HALO_WIDTH, right + HALO_WIDTH, bottom + HALO_WIDTH),
                outline=halo,
                width=RING_WIDTH + HALO_WIDTH,
            )
        draw.rectangle((left, top, right, bottom), outline=ring, width=RING_WIDTH)
        _label(draw, str(annotation.number), (left, top), ring, halo, font, scale)

    if not drawn:
        return None
    return image.crop(crop_window(image, drawn, scale))


def _font(scale: float) -> ImageFont.ImageFont | ImageFont.FreeTypeFont:
    try:
        return ImageFont.load_default(size=int(13 * scale))
    except (AttributeError, TypeError):  # pragma: no cover - older Pillow
        return ImageFont.load_default()


def _label(
    draw: ImageDraw.ImageDraw,
    text: str,
    corner: tuple[int, int],
    ring: tuple[int, int, int],
    halo: tuple[int, int, int],
    font: ImageFont.ImageFont | ImageFont.FreeTypeFont,
    scale: float,
) -> None:
    left, top = corner
    pad = int(LABEL_PAD * scale)
    height = int(LABEL_HEIGHT * scale)
    width = int(9 * scale) * len(text) + pad * 2
    box = (left, max(0, top - height), left + width, max(height, top))
    draw.rectangle(box, fill=ring)
    ink = (255, 255, 255) if _luminance(ring) < 0.5 else (14, 15, 17)
    draw.text((box[0] + pad, box[1] + int(3 * scale)), text, fill=ink, font=font)
    if halo != ring:
        draw.rectangle(box, outline=halo, width=1)
