"""Scales derived from the page's own measurements — CLAUDE.md.

Never hardcode 4px/8px. A site that runs on a 5px rhythm is unusual, not broken, and a
tool that says otherwise is wrong about that site every single run. The scale comes from
the page; the outliers are measured against it.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from engine.artifact.models import LayoutRecord

if TYPE_CHECKING:
    from engine.figma.models import Tokens

SCALE_SHARE = 0.05
"""A value has to account for this share of its inventory before it counts as part of
the scale. Below that it is an accident, not a decision."""

MIN_USES = 2

FONT_SIZE_CLUSTER_PX = 1.0
"""Sizes within this of each other are one step. A fluid heading lands on 27.85px at one
width and 28px at another; that is one decision, not two."""

MIN_SIZE_USES = 2
"""A size worn by exactly one element is a slip. Two is a decision.

Note this is a floor, not a share. The share threshold is what broke: body copy is 90%
of a page's text nodes, so every heading fell under 5% and the scale ended up describing
only the body. Every size a page genuinely uses now counts the same, whatever its volume.
"""


def _dominant(counts: Counter[float] | Counter[int] | Counter[str]) -> list[object]:
    total = sum(counts.values())
    if not total:
        return []
    floor = max(MIN_USES, total * SCALE_SHARE)
    return [value for value, count in counts.most_common() if count >= floor]


@dataclass
class Scales:
    """What this page (or site) has decided its design language is."""

    spacing: list[float] = field(default_factory=list)
    fontSizes: list[float] = field(default_factory=list)
    weights: list[int] = field(default_factory=list)
    families: list[str] = field(default_factory=list)
    palette: list[str] = field(default_factory=list)
    lineHeights: list[float] = field(default_factory=list)
    source: str = "page"
    """`design` when the Figma tokens were available. Worth saying out loud in a finding:
    "not on the scale" means something different depending on whose scale it is."""

    @property
    def usable(self) -> bool:
        """A page with three text elements has no scale to speak of, and pretending it
        does produces nonsense findings."""
        return len(self.fontSizes) >= 2 or len(self.spacing) >= 2


def derive(layouts: list[LayoutRecord], tokens: Tokens | None = None) -> Scales:
    """The site's scale, from the design when there is one and from the page otherwise.

    SPEC §6: the derived tokens are what let a tablet or mobile viewport be judged when
    no design frame exists for it. Where the design is silent — it has no spacing values,
    say — the page's own histogram fills the gap rather than nothing being checked.
    """
    page = _from_pages(layouts)
    if tokens is None:
        return page
    return Scales(
        spacing=sorted(tokens.spacing) or page.spacing,
        fontSizes=sorted(tokens.typeScale) or page.fontSizes,
        weights=sorted(tokens.fontWeights) or page.weights,
        families=tokens.fontFamilies or page.families,
        palette=tokens.palette or page.palette,
        lineHeights=page.lineHeights,
        source="design",
    )


def _from_pages(layouts: list[LayoutRecord]) -> Scales:
    spacing: Counter[float] = Counter()
    weights: Counter[int] = Counter()
    families: Counter[str] = Counter()
    leading: Counter[float] = Counter()
    palette: Counter[str] = Counter()

    for layout in layouts:
        for bucket in layout.spacingHistogram:
            if bucket.gap > 0:
                spacing[bucket.gap] += bucket.count
        for style in layout.typeInventory:
            weights[style.fontWeight] += style.count
            families[style.fontFamily] += style.count
            if style.lineHeight:
                leading[round(style.lineHeight / style.fontSize, 2)] += style.count
        for colour in layout.colourInventory:
            palette[colour.colour] += colour.count

    return Scales(
        spacing=sorted(v for v in _dominant(spacing) if isinstance(v, float)),
        fontSizes=_type_scale(layouts),
        weights=sorted(v for v in _dominant(weights) if isinstance(v, int)),
        families=[v for v in _dominant(families) if isinstance(v, str)],
        palette=[v for v in _dominant(palette) if isinstance(v, str)],
        lineHeights=sorted(v for v in _dominant(leading) if isinstance(v, float)),
    )


def _type_scale(layouts: list[LayoutRecord]) -> list[float]:
    """The distinct sizes this site actually chose, clustered, not weighted by volume.

    Counting nodes lets body copy bury the headings: a page with four hundred paragraphs
    and two headings derives a scale containing no heading size, and then reports every
    heading on the site as off-scale. What matters is how many separate *decisions* used
    a size, so each distinct style counts once, however many nodes wear it.

    A size that only one style used, only once, is an accident and stays off the scale.
    A site with no coherent scale gets an empty one, and `off_scale` then flags nothing —
    silence beats flagging every heading on the page.
    """
    styles: Counter[float] = Counter()
    elements: Counter[float] = Counter()
    for layout in layouts:
        for style in layout.typeInventory:
            styles[style.fontSize] += 1
            elements[style.fontSize] += style.count

    clusters: list[list[float]] = []
    for size in sorted(elements):
        if clusters and size - clusters[-1][-1] <= FONT_SIZE_CLUSTER_PX:
            clusters[-1].append(size)
        else:
            clusters.append([size])

    return sorted(
        max(cluster, key=lambda s: (styles[s], elements[s], -s))
        for cluster in clusters
        if sum(elements[s] for s in cluster) >= MIN_SIZE_USES
    )


def off_scale(value: float, scale: list[float], tolerance: float) -> bool:
    """True when `value` is not within tolerance of anything on the scale."""
    return bool(scale) and all(abs(value - step) > tolerance for step in scale)


def nearest_step(value: float, scale: list[float]) -> float | None:
    return min(scale, key=lambda step: abs(step - value)) if scale else None
