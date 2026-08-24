"""Visual regression against the previous run — SPEC §5's hardening.

**SSIM plus a structural element diff, never a raw pixel diff.** A raw diff fires on an
antialiased glyph and a one-pixel scroll offset, so it gets switched off within a week
and then it is not protecting anything. SSIM compares local structure, and the element
diff says *what* changed in terms someone can act on: this card is gone, that button
moved 40px.

Volatile regions — timestamps, carousels, randomised content, A/B variants — are blanked
in **both** images before comparison, using the project's mask selectors. Blanked at
compare time rather than trusting the capture, so adding a mask fixes old runs too.

This records what changed; `checkers/visual.py` decides what it means, the same division
as the API probes.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image
from pydantic import Field

from engine.artifact.context import RunContext
from engine.artifact.models import ArtifactModel, Box, ElementRecord
from engine.artifact.selectors import any_matches

COMPARE_WIDTH = 512
"""Both images are scaled to this before comparison. Structure survives downscaling;
antialiasing, subpixel text rendering and a scrollbar's width do not."""

WINDOW = 8
C1 = (0.01 * 255) ** 2
C2 = (0.03 * 255) ** 2
"""Wang et al.'s stabilising constants, for 8-bit luminance."""

MOVED_PX = 8.0
"""Under this, an element has not moved; it has been laid out slightly differently by a
font that loaded at a different moment."""


class ElementChange(ArtifactModel):
    stableKey: str
    selector: str | None = None
    kind: str
    """`added`, `removed`, `moved` or `resized`."""

    before: Box | None = None
    after: Box | None = None
    delta: float = 0.0


class SurfaceComparison(ArtifactModel):
    pageId: str
    pagePath: str
    viewport: str
    ssim: float = 1.0
    """1.0 is identical. Below `checkers/visual.py`'s threshold is a finding."""

    compared: bool = False
    """False when one side had no screenshot — a new page, or a pruned old run."""

    note: str = ""
    added: int = 0
    removed: int = 0
    moved: int = 0
    changes: list[ElementChange] = Field(default_factory=list)


class VisualFile(ArtifactModel):
    baseRunId: str = ""
    maskSelectors: list[str] = Field(default_factory=list)
    surfaces: list[SurfaceComparison] = Field(default_factory=list)


# ------------------------------------------------------------------------ SSIM


@dataclass
class _Grey:
    width: int
    height: int
    pixels: list[int]

    def block(self, x: int, y: int, size: int) -> list[int]:
        out: list[int] = []
        for row in range(y, min(y + size, self.height)):
            start = row * self.width + x
            out.extend(self.pixels[start : min(start + size, (row + 1) * self.width)])
        return out


def _prepare(path: Path, boxes: list[Box], scale: float) -> _Grey | None:
    """Greyscale, masked, and downscaled to a common width."""
    with Image.open(path) as image:
        picture = image.convert("L")
        if boxes:
            blank = Image.new("L", picture.size, 128)
            for box in boxes:
                left, top = int(box.x * scale), int(box.y * scale)
                right, bottom = int((box.x + box.w) * scale), int((box.y + box.h) * scale)
                if right <= left or bottom <= top:
                    continue
                region = (
                    max(0, left),
                    max(0, top),
                    min(picture.width, right),
                    min(picture.height, bottom),
                )
                if region[2] > region[0] and region[3] > region[1]:
                    picture.paste(blank.crop(region), region)
        if picture.width == 0 or picture.height == 0:
            return None
        height = max(1, round(picture.height * COMPARE_WIDTH / picture.width))
        small = picture.resize((COMPARE_WIDTH, height), Image.Resampling.BILINEAR)
        return _Grey(small.width, small.height, list(small.getdata()))


def _stats(values: list[int]) -> tuple[float, float]:
    count = len(values)
    mean = sum(values) / count
    variance = sum((v - mean) ** 2 for v in values) / count
    return mean, variance


def ssim(first: _Grey, second: _Grey) -> float:
    """Mean SSIM over 8×8 windows. Pure Python on a 512px-wide image is milliseconds, and
    a dependency on numpy or scikit-image for one formula is not worth carrying."""
    height = min(first.height, second.height)
    scores: list[float] = []
    for y in range(0, height, WINDOW):
        for x in range(0, COMPARE_WIDTH, WINDOW):
            a = first.block(x, y, WINDOW)
            b = second.block(x, y, WINDOW)
            if len(a) != len(b) or not a:
                continue
            mean_a, var_a = _stats(a)
            mean_b, var_b = _stats(b)
            covariance = sum(
                (pa - mean_a) * (pb - mean_b) for pa, pb in zip(a, b, strict=True)
            ) / len(a)
            numerator = (2 * mean_a * mean_b + C1) * (2 * covariance + C2)
            denominator = (mean_a**2 + mean_b**2 + C1) * (var_a + var_b + C2)
            scores.append(numerator / denominator if denominator else 1.0)
    if not scores:
        return 1.0
    score = sum(scores) / len(scores)
    # Different page heights mean content was added or removed; SSIM over the shared top
    # would call that identical, which is exactly the change worth catching.
    ratio = min(first.height, second.height) / max(first.height, second.height, 1)
    return round(score * ratio, 4)


# ------------------------------------------------------- the structural element diff


def _elements(ctx: RunContext, page_id: str, viewport: str) -> dict[str, ElementRecord]:
    try:
        return {e.stableKey: e for e in ctx.elements(page_id, viewport) if e.stableKey}
    except (OSError, ValueError):
        return {}


def structural(
    current: dict[str, ElementRecord],
    base: dict[str, ElementRecord],
    masks: list[str],
    limit: int = 40,
) -> list[ElementChange]:
    """What changed, in terms of elements rather than pixels."""
    changes: list[ElementChange] = []
    for key, element in current.items():
        if any_matches(masks, element):
            continue
        was = base.get(key)
        if was is None:
            changes.append(
                ElementChange(
                    stableKey=key, selector=element.selector, kind="added", after=element.box
                )
            )
            continue
        moved = max(abs(element.box.x - was.box.x), abs(element.box.y - was.box.y))
        resized = max(abs(element.box.w - was.box.w), abs(element.box.h - was.box.h))
        if moved > MOVED_PX or resized > MOVED_PX:
            changes.append(
                ElementChange(
                    stableKey=key,
                    selector=element.selector,
                    kind="moved" if moved >= resized else "resized",
                    before=was.box,
                    after=element.box,
                    delta=round(max(moved, resized), 1),
                )
            )
    for key, element in base.items():
        if key in current or any_matches(masks, element):
            continue
        changes.append(
            ElementChange(
                stableKey=key, selector=element.selector, kind="removed", before=element.box
            )
        )
    changes.sort(key=lambda c: (c.kind, -c.delta, c.stableKey))
    return changes[:limit]


# ------------------------------------------------------------------ the comparison


def compare(ctx: RunContext, base: RunContext, masks: list[str] | None = None) -> VisualFile:
    """Every surface this run and the base run have in common."""
    selectors = list(masks if masks is not None else ctx.manifest.config.maskSelectors)
    regions = list(ctx.manifest.config.maskRegions)
    out = VisualFile(baseRunId=base.run_id, maskSelectors=selectors)
    base_pages = {page.path: page.id for page in base.pages()}

    for page in ctx.pages():
        base_id = base_pages.get(page.path)
        for viewport in ctx.viewport_names(page.id):
            surface = SurfaceComparison(pageId=page.id, pagePath=page.path, viewport=viewport)
            if base_id is None:
                surface.note = "this page is new since the last run"
                out.surfaces.append(surface)
                continue

            current_elements = _elements(ctx, page.id, viewport)
            base_elements = _elements(base, base_id, viewport)
            changes = structural(current_elements, base_elements, selectors)
            surface.changes = changes
            surface.added = sum(1 for c in changes if c.kind == "added")
            surface.removed = sum(1 for c in changes if c.kind == "removed")
            surface.moved = sum(1 for c in changes if c.kind in ("moved", "resized"))

            scale = next((v.deviceScaleFactor for v in ctx.viewports if v.name == viewport), 1.0)
            boxes = [e.box for e in current_elements.values() if any_matches(selectors, e)]
            boxes += [r.box() for r in regions if r.viewport in (None, viewport)]
            here = ctx.paths.full_png(page.id, viewport)
            there = base.paths.full_png(base_id, viewport)
            if not here.is_file() or not there.is_file():
                surface.note = "no screenshot on one side, so only the elements were compared"
                out.surfaces.append(surface)
                continue
            first = _prepare(here, boxes, scale)
            second = _prepare(there, boxes, scale)
            if first is None or second is None:
                surface.note = "a screenshot could not be read"
                out.surfaces.append(surface)
                continue
            surface.ssim = ssim(first, second)
            surface.compared = True
            out.surfaces.append(surface)
    return out
