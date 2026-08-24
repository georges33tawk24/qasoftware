"""Group C, colour against the derived palette — SPEC §8.4 C.

ΔE in CIELAB, never RGB distance (CLAUDE.md). The finding names the nearest token and
the distance, because "this blue is wrong" is useless and "ΔE 3.1 from #3B7DD8" is not.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers import colour, scales
from engine.checkers.base import checker
from engine.checkers.support import element_finding, judged_by_design, surfaces
from engine.issues.models import Category, Finding, Severity

MIN_PALETTE_SIZE = 3
"""Below this there is no palette to be off."""

NEAR_MISS_DELTA_E = 12.0
"""Past this the colour is a different colour on purpose, not a missed token."""

ESTABLISHED_USES = 3
"""A colour used this often across the site is part of the design, whether or not it
cleared the bar for the derived palette. Flagging it says more about the derivation than
about the site."""

STRUCTURAL = frozenset({"#ffffff", "#000000"})
"""White and black are structure, not brand. Reporting "white is ΔE 5 from your nearest
token" is the kind of finding that gets a tool switched off."""


@checker
class OffPalette:
    id = "typography.palette"
    category = Category.typography
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        all_surfaces = list(surfaces(ctx))
        derived = scales.derive([s.layout for s in all_surfaces], ctx.tokens())
        palette = derived.palette
        if len(palette) < MIN_PALETTE_SIZE:
            return

        scale_source = derived.source
        established: Counter[str] = Counter()
        for surface in all_surfaces:
            for usage in surface.layout.colourInventory:
                established[usage.colour] += usage.count

        for surface in all_surfaces:
            if scale_source == "design" and judged_by_design(ctx, surface):
                continue
            seen: set[tuple[str, str]] = set()
            for element in surface.laid_out:
                for prop, value in (
                    ("color", element.styles.color if element.text else None),
                    ("backgroundColor", element.styles.backgroundColor),
                ):
                    if not value or value in palette:
                        continue
                    if established[value] >= ESTABLISHED_USES:
                        continue
                    parsed = colour.parse(value)
                    if parsed is None or parsed[3] == 0:
                        continue
                    if colour.to_hex(parsed) in STRUCTURAL:
                        continue
                    match = colour.nearest(value, palette)
                    if match is None:
                        continue
                    token, delta = match
                    if delta <= colour.DEFAULT_DELTA_E or delta > NEAR_MISS_DELTA_E:
                        continue
                    if (prop, value) in seen:
                        continue
                    seen.add((prop, value))
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind=f"off-palette-{prop}",
                        title=f"{colour.to_hex(parsed)} is ΔE {delta:.1f} from the nearest "
                        "palette colour",
                        description="Close enough to be a mistake rather than a decision.",
                        expected=token,
                        actual=value,
                        data={"property": prop, "deltaE": round(delta, 2), "nearest": token},
                    )
