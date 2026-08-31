"""Group B, the findings about things you cannot actually click — SPEC §8.4 B."""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.geometry import bottom, contains, right
from engine.checkers.base import checker
from engine.checkers.support import clipped_away, element_finding, surfaces
from engine.issues.models import Category, Finding, Severity

MIN_OVERLAP_AREA = 16.0
MIN_OVERLAP_SPAN = 2.0
MAX_CLICKABLES = 400
"""ponytail: the overlap test is O(n²) on clickable elements. Four hundred is already an
unusual page; beyond it the pairwise pass is skipped rather than run for a minute.
Upgrade path is a sweep line if a real site ever needs it.

A 1px overlap (e.g. margin-left: -1px) is the standard CSS border-collapse idiom used
across button groups, pagination, tabs, and input groups. Both width and height must
overlap by more than a border seam to be an actual control overlap defect."""


@checker
class OverlappingClickables:
    id = "layout.overlapping-clickables"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            clickables = [
                e for e in surface.laid_out if e.clickable and not clipped_away(surface, e)
            ]
            if len(clickables) > MAX_CLICKABLES:
                continue
            for index, first in enumerate(clickables):
                for second in clickables[index + 1 :]:
                    if second.id in first.childIds or first.id in second.childIds:
                        continue
                    if contains(first.box, second.box) or contains(second.box, first.box):
                        continue
                    w = min(right(first.box), right(second.box)) - max(first.box.x, second.box.x)
                    h = min(bottom(first.box), bottom(second.box)) - max(first.box.y, second.box.y)
                    if w < MIN_OVERLAP_SPAN or h < MIN_OVERLAP_SPAN:
                        continue
                    area = w * h
                    if area < MIN_OVERLAP_AREA:
                        continue
                    yield element_finding(
                        self,
                        surface,
                        first,
                        kind="overlapping-clickables",
                        title="Two clickable elements overlap",
                        description=(
                            f"{first.selector} overlaps {second.selector} by "
                            f"{area:.0f}px². Whichever is on top takes the tap."
                        ),
                        expected="no overlap between separate controls",
                        actual=f"{area:.0f}px² shared",
                        groupAs="overlap",
                        data={"other": second.selector, "area": area},
                    )


@checker
class OccludedClickables:
    id = "layout.occluded-clickable"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                if not element.clickable or element.occludedBy is None:
                    continue
                blocker = surface.by_id.get(element.occludedBy)
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="occluded-clickable",
                    title="Control is covered at its centre point",
                    description=(
                        "elementFromPoint at the middle of this control returns "
                        f"{blocker.selector if blocker else 'another element'}, so a click "
                        "there does not reach it."
                    ),
                    expected="the control receives its own clicks",
                    actual=f"covered by {blocker.selector if blocker else 'another element'}",
                    groupAs="occluded",
                    data={"occludedBy": blocker.selector if blocker else element.occludedBy},
                )


@checker
class StickyHeaderAnchors:
    id = "layout.sticky-anchor"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            bars = [
                e
                for e in surface.laid_out
                if e.styles.position in ("sticky", "fixed")
                and e.boxViewport.y <= 1
                and e.box.h > 0
                and e.box.w > surface.viewport.width * 0.5
            ]
            if not bars:
                continue
            cover = max(bar.box.h for bar in bars)
            targets = {
                e.link.href.lstrip("#"): e
                for e in surface.elements
                if e.link and e.link.href.startswith("#") and len(e.link.href) > 1
            }
            if not targets:
                continue
            for element in surface.laid_out:
                if element.htmlId not in targets or element.styles.scrollMarginTop >= cover:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="anchor-under-sticky-header",
                    title="Anchor target lands underneath the sticky header",
                    description=(
                        f"A {cover:g}px sticky bar covers the top of the page and this "
                        "target has no scroll-margin-top to clear it."
                    ),
                    expected=f"scroll-margin-top of at least {cover:g}px",
                    actual=f"{element.styles.scrollMarginTop:g}px",
                    groupAs="sticky",
                    data={"headerHeight": cover, "anchor": element.htmlId},
                )
