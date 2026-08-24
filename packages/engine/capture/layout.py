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


def derive(page_id: str, viewport: str, elements: list[ElementRecord]) -> LayoutRecord:
    laid_out = [e for e in elements if e.visible and e.box.w > 0 and e.box.h > 0]
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


def alignment_sets(elements: list[ElementRecord]) -> list[AlignmentSet]:
    """Siblings that were meant to line up, with each member's deviation from the median."""
    sets: list[AlignmentSet] = []
    for parent, siblings in sorted(_by_parent(elements).items(), key=lambda kv: kv[0] or ""):
        if len(siblings) < ALIGNMENT_MIN_MEMBERS:
            continue
        for axis, value in _AXES:
            for cluster in _cluster(siblings, value):
                if len(cluster) < ALIGNMENT_MIN_MEMBERS:
                    continue
                centre = round(median([value(e) for e in cluster]), 2)
                sets.append(
                    AlignmentSet(
                        axis=axis,
                        edge="start",
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
