"""Group B, the rhythm findings — SPEC §8.4 B.

A three-pixel misalignment is a subtraction problem, not a judgement call (SPEC §1.2).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from statistics import median

from engine.artifact.context import Capability, RunContext
from engine.artifact.geometry import by_parent, sibling_gaps
from engine.artifact.models import ElementRecord
from engine.checkers import scales
from engine.checkers.base import checker
from engine.checkers.support import (
    Surface,
    contiguous_runs,
    element_finding,
    judged_by_design,
    surfaces,
)
from engine.issues.models import Category, Finding, Severity

ALIGNMENT_TOLERANCE_PX = 1.0
"""SPEC §4.2. Anything past this is a real deviation, not sub-pixel rounding."""

CONSENSUS = 0.6
"""A set only has an outlier if most of it agrees. Three elements at 24, 31 and 38 are
not one drifted card, they are a layout this checker has misread."""

GAP_TOLERANCE_PX = 1.0
MIN_GAP_PX = 2.0
RARE_GAP_USES = 3


@checker
class SiblingAlignment:
    id = "layout.alignment"
    category = Category.layout
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for group in surface.layout.alignmentSets:
                deviations = group.deviations
                if len(deviations) < 3:
                    continue
                agreed = sum(1 for d in deviations.values() if abs(d) <= ALIGNMENT_TOLERANCE_PX)
                if agreed / len(deviations) < CONSENSUS:
                    continue
                axis_edge = "left" if group.axis == "x" else "top"
                edge = "centre" if group.edge == "centre" else axis_edge
                for element_id, deviation in sorted(deviations.items()):
                    if abs(deviation) <= ALIGNMENT_TOLERANCE_PX:
                        continue
                    element = surface.by_id.get(element_id)
                    if element is None:
                        continue
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind=f"misaligned-{group.axis}",
                        title=(
                            f"{edge.capitalize()} is {abs(deviation):g}px off its siblings"
                            if group.edge == "centre"
                            else f"{edge.capitalize()} edge is {abs(deviation):g}px off its "
                            "siblings"
                        ),
                        description=(
                            f"{agreed} of {len(deviations)} siblings share a {edge} at "
                            f"{group.median:g}px. This one does not."
                            if group.edge == "centre"
                            else f"{agreed} of {len(deviations)} siblings share a {edge} edge "
                            f"at {group.median:g}px. This one does not."
                        ),
                        expected=f"{edge} edge at {group.median:g}px",
                        actual=f"{group.median + deviation:g}px",
                        groupAs=f"{group.axis}:{group.parentId}",
                        data={
                            "axis": group.axis,
                            "median": group.median,
                            "deviation": deviation,
                            "siblings": len(deviations),
                        },
                    )


@checker
class RepeatedGroupGaps:
    id = "layout.group-gaps"
    category = Category.layout
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for group in surface.layout.repeatedGroups:
                for members in contiguous_runs(surface, group.elementIds):
                    if len(members) < 3:
                        continue  # two members give one gap, and one gap is never uneven
                    yield from self._run(surface, group.signature, members)

    def _run(
        self, surface: Surface, signature: str, members: list[ElementRecord]
    ) -> Iterable[Finding]:
        if members[0].styles.display == "inline":
            return  # inline text links inside prose have gaps decided by words, not layout
        gaps = list(sibling_gaps(members))
        if len(gaps) < 2:
            return
        values = [round(gap, 1) for _, _, gap in gaps]
        if any(v < 0 for v in values):
            return  # wrapped across lines or multi-column, not a single 1D row or column
        expected = median(values)
        for (_, second, _raw), value in zip(gaps, values, strict=True):
            if abs(value - expected) <= GAP_TOLERANCE_PX:
                continue
            yield element_finding(
                self,
                surface,
                second,
                kind="uneven-gap-in-group",
                title=f"Gap in a repeated group is {value:g}px, not {expected:g}px",
                description=f"{len(members)} × {signature} with gaps {values}.",
                expected=f"{expected:g}px",
                actual=f"{value:g}px",
                groupAs=signature,
                data={"signature": signature, "gaps": values},
            )


@checker
class SpacingScale:
    id = "layout.spacing-scale"
    category = Category.layout
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        all_surfaces = list(surfaces(ctx))
        scale = scales.derive([s.layout for s in all_surfaces], ctx.tokens())
        if len(scale.spacing) < 2:
            return  # no rhythm to be off

        site_wide: Counter[float] = Counter()
        for surface in all_surfaces:
            for bucket in surface.layout.spacingHistogram:
                site_wide[bucket.gap] += bucket.count

        for surface in all_surfaces:
            if scale.source == "design" and judged_by_design(ctx, surface):
                continue
            yield from self._page(surface, scale, site_wide)

    def _page(
        self, surface: Surface, scale: scales.Scales, site_wide: Counter[float]
    ) -> Iterable[Finding]:
        seen: set[tuple[str, float]] = set()
        for parent_id, siblings in by_parent(surface.laid_out).items():
            if parent_id is None or len(siblings) < 2:
                continue
            for _, second, raw in sibling_gaps(siblings):
                gap = round(raw * 2) / 2
                if gap < MIN_GAP_PX or not scales.off_scale(gap, scale.spacing, GAP_TOLERANCE_PX):
                    continue
                # A one-off is a mistake; a value used all over the site is a decision
                # this checker has not been told about.
                if site_wide[gap] >= RARE_GAP_USES:
                    continue
                if (second.id, gap) in seen:
                    continue
                seen.add((second.id, gap))
                nearest = scales.nearest_step(gap, scale.spacing)
                yield element_finding(
                    self,
                    surface,
                    second,
                    kind="off-spacing-scale",
                    title=f"{gap:g}px gap is not on this site's spacing scale",
                    description=f"{scale.source.capitalize()} scale: "
                    + ", ".join(f"{s:g}" for s in scale.spacing)
                    + ".",
                    expected=f"{nearest:g}px" if nearest else "a value on the scale",
                    actual=f"{gap:g}px",
                    groupAs="off-scale",
                    data={"gap": gap, "scale": scale.spacing, "usesAcrossSite": site_wide[gap]},
                )
