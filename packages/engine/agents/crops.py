"""Region crops for the verifier — SPEC §9.4.

The verifier is given the candidate's region at 2x rather than the whole page: judging is
easier than spotting, and a close crop is what makes it easier.
"""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image

from engine.artifact.models import Box

CONTEXT = 0.35
"""More context than the report's crop. The verifier's question is "is this real *here*",
which needs the surroundings the annotation deliberately trims."""

ZOOM = 2.0
MIN_SIDE = 320
MAX_SIDE = 900
"""Below the whole-page budget on purpose. Magnifying a wide region until it is as many
pixels as the page would make the verify call cost as much as the sweep it exists to make
cheap (SPEC §9.1)."""


def region(
    screenshot: Path, box: Box, *, scale: float = 1.0, zoom: float = ZOOM
) -> tuple[bytes, int, int] | None:
    """PNG bytes for the region, magnified, with its pixel size for cost estimation."""
    if not screenshot.is_file() or box.w <= 0 or box.h <= 0:
        return None
    with Image.open(screenshot) as raw:
        image = raw.convert("RGB")

    pad_x = max(48.0, box.w * CONTEXT) * scale
    pad_y = max(48.0, box.h * CONTEXT) * scale
    left = int(box.x * scale - pad_x)
    top = int(box.y * scale - pad_y)
    right = int((box.x + box.w) * scale + pad_x)
    bottom = int((box.y + box.h) * scale + pad_y)

    left, right = _fit(left, right, image.width, int(MIN_SIDE * scale))
    top, bottom = _fit(top, bottom, image.height, int(MIN_SIDE * scale))
    crop = image.crop((left, top, right, bottom))

    target = (
        min(int(crop.width * zoom), MAX_SIDE),
        min(int(crop.height * zoom), MAX_SIDE),
    )
    if target[0] > 0 and target[1] > 0 and target != (crop.width, crop.height):
        crop = crop.resize(target, Image.Resampling.LANCZOS)

    buffer = io.BytesIO()
    crop.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue(), crop.width, crop.height


def _fit(low: int, high: int, limit: int, minimum: int) -> tuple[int, int]:
    if high - low < minimum:
        grow = (minimum - (high - low)) // 2
        low, high = low - grow, high + grow
    span = min(high - low, limit)
    low = max(0, min(low, limit - span))
    return low, low + span


def whole_page(screenshot: Path, max_side: int = 1568) -> tuple[bytes, int, int] | None:
    """The full-page screenshot, scaled down to something a vision model reads cheaply.

    A 1440×6000 page at full resolution is thousands of tokens per sweep call and no more
    legible than this.
    """
    if not screenshot.is_file():
        return None
    with Image.open(screenshot) as raw:
        image = raw.convert("RGB")
        if max(image.width, image.height) > max_side:
            ratio = max_side / max(image.width, image.height)
            image = image.resize(
                (max(1, int(image.width * ratio)), max(1, int(image.height * ratio))),
                Image.Resampling.LANCZOS,
            )
        buffer = io.BytesIO()
        image.save(buffer, format="PNG", optimize=True)
        return buffer.getvalue(), image.width, image.height
