"""Group C, type — SPEC §8.4 C.

Every threshold here comes from the site's own inventory. A site on a 15px base is
unusual, not broken, and a tool that says otherwise is wrong about it on every run.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers import scales
from engine.checkers.base import checker
from engine.checkers.support import (
    element_finding,
    judged_by_design,
    page_finding,
    surfaces,
    synthetic_key,
)
from engine.issues.models import Category, Finding, Severity

FONT_SIZE_TOLERANCE_PX = 0.5
MIN_LINE_HEIGHT_RATIO = 1.1
MAX_LINE_HEIGHT_RATIO = 2.2
MIN_MEASURE = 45
MAX_MEASURE = 95
BODY_COPY_MIN_CHARS = 200
BODY_COPY_SIZES = (12.0, 24.0)
SPRAWL_FONT_SIZES = 12
SPRAWL_COLOURS = 20


def _families(family: str) -> str:
    return family.split(",")[0].strip().strip("\"'")


@checker
class TypeScale:
    id = "typography.scale"
    category = Category.typography
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        all_surfaces = list(surfaces(ctx))
        scale = scales.derive([s.layout for s in all_surfaces], ctx.tokens())
        if not scale.usable:
            return
        scale_source = scale.source
        for surface in all_surfaces:
            if scale_source == "design" and judged_by_design(ctx, surface):
                continue
            for element in surface.laid_out:
                if not element.text:
                    continue
                styles = element.styles
                if scale.fontSizes and scales.off_scale(
                    styles.fontSize, scale.fontSizes, FONT_SIZE_TOLERANCE_PX
                ):
                    nearest = scales.nearest_step(styles.fontSize, scale.fontSizes)
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="off-type-scale",
                        title=f"{styles.fontSize:g}px is not on this site's type scale",
                        description=f"{scale.source.capitalize()} type scale: "
                        + ", ".join(f"{s:g}" for s in scale.fontSizes)
                        + "px.",
                        expected=f"{nearest:g}px" if nearest else "a size on the scale",
                        actual=f"{styles.fontSize:g}px",
                        groupAs=f"{styles.fontSize:g}",
                        data={"fontSize": styles.fontSize, "scale": scale.fontSizes},
                    )
                if scale.weights and styles.fontWeight not in scale.weights:
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="off-weight-set",
                        title=f"Font weight {styles.fontWeight} is used almost nowhere else",
                        expected="one of " + ", ".join(str(w) for w in scale.weights),
                        actual=str(styles.fontWeight),
                        severity=Severity.trivial,
                        data={"weight": styles.fontWeight, "allowed": scale.weights},
                    )
                family = _families(styles.fontFamily)
                if scale.families and family not in scale.families:
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="off-family-set",
                        title=f"{family!r} is not one of this site's fonts",
                        expected="one of " + ", ".join(scale.families),
                        actual=family,
                        data={"family": family, "allowed": scale.families},
                    )


@checker
class LineHeight:
    id = "typography.line-height"
    category = Category.typography
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                styles = element.styles
                if not element.text or not styles.lineHeight or styles.fontSize <= 0:
                    continue
                ratio = styles.lineHeight / styles.fontSize
                if MIN_LINE_HEIGHT_RATIO <= ratio <= MAX_LINE_HEIGHT_RATIO:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="line-height-out-of-range",
                    title=f"Line height is {ratio:.2f}× the font size",
                    description="Tight enough to collide, or loose enough to stop reading "
                    "as a paragraph.",
                    expected=f"{MIN_LINE_HEIGHT_RATIO}–{MAX_LINE_HEIGHT_RATIO}×",
                    actual=f"{ratio:.2f}×",
                    groupAs="line-height",
                    data={"lineHeight": styles.lineHeight, "fontSize": styles.fontSize},
                )


@checker
class FontFallback:
    id = "typography.fallback-font"
    category = Category.typography
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            seen: set[tuple[str, str]] = set()
            for element in surface.laid_out:
                font = element.font
                if font is None or not font.fallbackUsed:
                    continue
                key = (font.requested, font.rendered)
                if key in seen:
                    continue
                seen.add(key)
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="webfont-not-loaded",
                    title=f"{font.requested!r} never loaded; text is rendering in "
                    f"{font.rendered!r}",
                    expected=font.requested,
                    actual=font.rendered,
                    data={"requested": font.requested, "rendered": font.rendered},
                )


@checker
class Measure:
    id = "typography.measure"
    category = Category.typography
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                styles = element.styles
                length = element.textLength
                if (
                    length < BODY_COPY_MIN_CHARS
                    or not styles.lineHeight
                    or not BODY_COPY_SIZES[0] <= styles.fontSize <= BODY_COPY_SIZES[1]
                ):
                    continue
                lines = max(1, round(element.box.h / styles.lineHeight))
                if lines < 2:
                    continue
                measure = round(length / lines)
                if MIN_MEASURE <= measure <= MAX_MEASURE:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="measure-too-wide" if measure > MAX_MEASURE else "measure-too-narrow",
                    title=f"Body copy runs about {measure} characters per line",
                    description="Comfortable reading is 45–95 characters.",
                    expected=f"{MIN_MEASURE}–{MAX_MEASURE} characters",
                    actual=f"{measure} characters",
                    groupAs="measure",
                    data={"chars": length, "lines": lines},
                )


@checker
class TokenSprawl:
    id = "typography.sprawl"
    category = Category.typography
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            sizes = {style.fontSize for style in surface.layout.typeInventory}
            colours = {c.colour for c in surface.layout.colourInventory}
            if len(sizes) > SPRAWL_FONT_SIZES:
                yield page_finding(
                    self,
                    surface.page,
                    viewport=surface.viewport.name,
                    kind="font-size-sprawl",
                    title=f"{len(sizes)} distinct font sizes on one page",
                    expected=f"at most {SPRAWL_FONT_SIZES}",
                    actual=str(len(sizes)),
                    stable_key=synthetic_key(self.id, "font-size"),
                    data={"sizes": sorted(sizes)},
                )
            if len(colours) > SPRAWL_COLOURS:
                yield page_finding(
                    self,
                    surface.page,
                    viewport=surface.viewport.name,
                    kind="colour-sprawl",
                    title=f"{len(colours)} distinct colours on one page",
                    expected=f"at most {SPRAWL_COLOURS}",
                    actual=str(len(colours)),
                    stable_key=synthetic_key(self.id, "colour"),
                    data={"colours": sorted(colours)},
                )
