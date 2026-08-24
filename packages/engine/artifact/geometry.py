"""Box arithmetic shared by the layout derivation and the layout checkers.

Pure functions over records — no browser, no I/O — so both sides of the capture/check
boundary can use them without either importing the other.
"""

from __future__ import annotations

from collections.abc import Iterator
from itertools import pairwise

from engine.artifact.models import Box, ElementRecord


def right(box: Box) -> float:
    return box.x + box.w


def bottom(box: Box) -> float:
    return box.y + box.h


def overlaps(a: Box, b: Box, *, slack: float = 0.0) -> bool:
    return (
        a.x < right(b) - slack
        and b.x < right(a) - slack
        and a.y < bottom(b) - slack
        and b.y < bottom(a) - slack
    )


def intersection_area(a: Box, b: Box) -> float:
    width = min(right(a), right(b)) - max(a.x, b.x)
    height = min(bottom(a), bottom(b)) - max(a.y, b.y)
    return width * height if width > 0 and height > 0 else 0.0


def contains(outer: Box, inner: Box, *, slack: float = 1.0) -> bool:
    return (
        inner.x >= outer.x - slack
        and inner.y >= outer.y - slack
        and right(inner) <= right(outer) + slack
        and bottom(inner) <= bottom(outer) + slack
    )


def by_parent(elements: list[ElementRecord]) -> dict[str | None, list[ElementRecord]]:
    groups: dict[str | None, list[ElementRecord]] = {}
    for element in elements:
        groups.setdefault(element.parentId, []).append(element)
    return groups


def sibling_gaps(
    siblings: list[ElementRecord],
) -> Iterator[tuple[ElementRecord, ElementRecord, float]]:
    """The gap between each pair of adjacent siblings, vertical or horizontal.

    Adjacency is decided by which axis the two boxes already share, which is what makes
    this work for both a stacked list and a row of cards without being told which it is.
    """
    ordered = sorted(siblings, key=lambda e: (e.box.y, e.box.x))
    for first, second in pairwise(ordered):
        a, b = first.box, second.box
        shares_column = a.x < right(b) and b.x < right(a)
        shares_row = a.y < bottom(b) and b.y < bottom(a)
        if shares_row:
            left, rightmost = sorted([first, second], key=lambda e: e.box.x)
            yield left, rightmost, rightmost.box.x - right(left.box)
        elif shares_column:
            yield first, second, b.y - bottom(a)
