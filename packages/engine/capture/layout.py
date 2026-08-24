"""`layout.json` — SPEC §4.2.

Derived once at capture because every layout checker needs it and recomputing per
checker is wasteful. Pure functions over `elements.json`: no browser, no I/O.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from statistics import median
from typing import Literal

from engine.artifact.geometry import by_parent as _group_by_parent
from engine.artifact.geometry import inside_svg as _inside_svg
from engine.artifact.geometry import sibling_gaps
from engine.artifact.models import (
    AlignmentSet,
    ColourUsage,
    ElementRecord,
    LayoutRecord,
    RepeatedGroup,
    SpacingBucket,
    TypeStyleUsage,
)

ALIGNMENT_CLUSTER_PX = 12.0
"""Membership tolerance, deliberately wider than the 1px *deviation* tolerance in §4.2.
Cluster at 1px and a drifted element forms its own set of one, so nothing is ever
flagged — the misaligned element has to stay inside the set to be measured against it."""

ALIGNMENT_MIN_MEMBERS = 3
"""A median needs three points to mean anything. With two, both members are equidistant
from their own average and the arithmetic cannot say which one moved."""

MAX_MEANINGFUL_GAP = 400.0
TRANSPARENT = "rgba(0, 0, 0, 0)"

ALIGNMENT_SIZE_TOLERANCE = 0.10
"""Past this much size spread, a set is measured on its centres instead of its edges.

An emphasised member of a rating scale is deliberately bigger than its neighbours, so
its top edge is *supposed* to sit higher — and its centre is not. Measuring edges turns
correct vertical centring into a finding on every page the widget appears on.
"""

OVERLAY_POSITIONS = frozenset({"absolute", "fixed"})
"""Overlays are placed against their container, not lined up with their siblings.

A badge pinned to the corner of a card image shares that image's parent and sits 8px
inside its top edge on purpose. Aligning it to the image is measuring a decision.
"""


def derive(page_id: str, viewport: str, elements: list[ElementRecord]) -> LayoutRecord:
    excluded = _inside_svg(elements)
    laid_out = [
        e for e in elements if e.visible and e.box.w > 0 and e.box.h > 0 and e.id not in excluded
    ]
    return LayoutRecord(
        pageId=page_id,
        viewport=viewport,
        alignmentSets=alignment_sets(laid_out),
        repeatedGroups=repeated_groups(laid_out),
        spacingHistogram=spacing_histogram(laid_out),
        typeInventory=type_inventory(laid_out),
        colourInventory=colour_inventory(laid_out),
    )


def _by_parent(elements: Iterable[ElementRecord]) -> dict[str | None, list[ElementRecord]]:
    return _group_by_parent(list(elements))


def _cluster(
    siblings: list[ElementRecord], value: Callable[[ElementRecord], float]
) -> list[list[ElementRecord]]:
    ordered = sorted(siblings, key=value)
    clusters: list[list[ElementRecord]] = []
    for element in ordered:
        if clusters and value(element) - value(clusters[-1][-1]) <= ALIGNMENT_CLUSTER_PX:
            clusters[-1].append(element)
        else:
            clusters.append([element])
    return clusters


_AXES: list[tuple[Literal["x", "y"], Callable[[ElementRecord], float]]] = [
    ("x", lambda e: e.box.x),
    ("y", lambda e: e.box.y),
]


def _pulled_outside(element: ElementRecord) -> bool:
    """A negative horizontal margin puts an element outside its container's content box
    on purpose — the grid-row idiom. Its left edge is not where its in-flow siblings'
    edges are, and an `<hr>` that respects the padding they negate is not misaligned."""
    return min(element.styles.marginLeft, element.styles.marginRight) < 0


def _size(axis: Literal["x", "y"], element: ElementRecord) -> float:
    return element.box.h if axis == "y" else element.box.w


def _measure(
    axis: Literal["x", "y"], cluster: list[ElementRecord], start: Callable[[ElementRecord], float]
) -> tuple[Literal["start", "centre"], Callable[[ElementRecord], float]]:
    """Edges when the members are the same size, centres when they are not.

    Only on the y axis. A set sharing a top edge is a row, so members of different
    heights are a vertical-centring question. A set sharing a *left* edge is a stack of
    blocks that are meant to be left-aligned, and comparing the centres of a narrow
    input and a full-width paragraph reports the layout working as a 92px defect.
    """
    if axis == "x":
        return "start", start
    sizes = [_size(axis, e) for e in cluster]
    middle = median(sizes)
    if middle <= 0:
        return "start", start
    if all(abs(s - middle) / middle <= ALIGNMENT_SIZE_TOLERANCE for s in sizes):
        return "start", start
    return "centre", lambda e: start(e) + _size(axis, e) / 2


def alignment_sets(elements: list[ElementRecord]) -> list[AlignmentSet]:
    """Siblings that were meant to line up, with each member's deviation from the median."""
    sets: list[AlignmentSet] = []
    in_flow = [
        e for e in elements if e.styles.position not in OVERLAY_POSITIONS and not _pulled_outside(e)
    ]
    for parent, siblings in sorted(_by_parent(in_flow).items(), key=lambda kv: kv[0] or ""):
        if len(siblings) < ALIGNMENT_MIN_MEMBERS:
            continue
        for axis, start in _AXES:
            for cluster in _cluster(siblings, start):
                if len(cluster) < ALIGNMENT_MIN_MEMBERS:
                    continue
                edge, value = _measure(axis, cluster, start)
                centre = round(median([value(e) for e in cluster]), 2)
                sets.append(
                    AlignmentSet(
                        axis=axis,
                        edge=edge,
                        parentId=parent,
                        median=centre,
                        elementIds=[e.id for e in cluster],
                        deviations={e.id: round(value(e) - centre, 2) for e in cluster},
                    )
                )
    return sets


def signature(element: ElementRecord) -> str:
    """Tag plus sorted classes — what makes a card a card."""
    return element.tag + "".join(f".{c}" for c in sorted(element.classes))


def repeated_groups(elements: list[ElementRecord]) -> list[RepeatedGroup]:
    """Card grids, nav items and listings, checkable as a unit."""
    groups: list[RepeatedGroup] = []
    for parent, siblings in sorted(_by_parent(elements).items(), key=lambda kv: kv[0] or ""):
        buckets: dict[str, list[ElementRecord]] = defaultdict(list)
        for element in siblings:
            buckets[signature(element)].append(element)
        for sig, members in sorted(buckets.items()):
            # ponytail: two is a repetition. Checkers that need two gaps to compare
            # (inconsistent spacing) filter for three themselves.
            if len(members) >= 2:
                groups.append(
                    RepeatedGroup(
                        signature=sig,
                        parentId=parent,
                        elementIds=[e.id for e in members],
                    )
                )
    return groups


def spacing_histogram(elements: list[ElementRecord]) -> list[SpacingBucket]:
    """Every gap between adjacent siblings. A healthy site clusters on a scale; the
    outliers against that derived scale are the findings (SPEC §4.2)."""
    counts: Counter[float] = Counter()
    for siblings in _by_parent(elements).values():
        for _, _, gap in sibling_gaps(siblings):
            if 0 <= gap <= MAX_MEANINGFUL_GAP:
                counts[round(gap * 2) / 2] += 1
    return [
        SpacingBucket(gap=gap, count=count)
        for gap, count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]


def type_inventory(elements: list[ElementRecord]) -> list[TypeStyleUsage]:
    counts: Counter[tuple[str, float, int, float | None]] = Counter()
    for element in elements:
        if not element.text:
            continue
        style = element.styles
        family = style.fontFamily.split(",")[0].strip().strip("\"'")
        counts[(family, style.fontSize, style.fontWeight, style.lineHeight)] += 1
    return [
        TypeStyleUsage(
            fontFamily=family, fontSize=size, fontWeight=weight, lineHeight=leading, count=count
        )
        for (family, size, weight, leading), count in sorted(
            counts.items(), key=lambda kv: (-kv[1], kv[0])
        )
    ]


def colour_inventory(elements: list[ElementRecord]) -> list[ColourUsage]:
    counts: Counter[tuple[str, str]] = Counter()
    for element in elements:
        style = element.styles
        if element.text:
            counts[(style.color, "color")] += 1
        if style.backgroundColor != TRANSPARENT:
            counts[(style.backgroundColor, "backgroundColor")] += 1
        if any(width > 0 for width in style.borderWidth):
            counts[(style.borderColor, "borderColor")] += 1
    # nearestToken and deltaE stay empty until phase 4 derives tokens from Figma.
    return [
        ColourUsage(colour=colour, property=prop, count=count)
        for (colour, prop), count in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    ]
