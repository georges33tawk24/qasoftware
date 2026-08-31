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

MIN_SCALE_USES = 2
"""A value used exactly once is a slip. Twice is a decision.

A floor, not a share — the share threshold is what broke both scales."""


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
    sizes: Counter[float] = Counter()
    weights: Counter[int] = Counter()
    families: Counter[str] = Counter()
    leading: Counter[float] = Counter()
    palette: Counter[str] = Counter()

    for layout in layouts:
        for bucket in layout.spacingHistogram:
            if bucket.gap > 0:
                spacing[bucket.gap] += bucket.count
        for style in layout.typeInventory:
            sizes[style.fontSize] += style.count
            weights[style.fontWeight] += style.count
            families[style.fontFamily] += style.count
            if style.lineHeight:
                leading[round(style.lineHeight / style.fontSize, 2)] += style.count
        for colour in layout.colourInventory:
            palette[colour.colour] += colour.count

    return Scales(
        # Spacing keeps the share-of-volume generator on purpose. It looks like the one
        # replaced for type, but the spacing *checker* already refuses to report a gap the
        # site uses three or more times (RARE_GAP_USES), which is the same "decision, not
        # a slip" test by another route. Moving spacing onto `measured_scale` put every
        # one-off gap on the scale and the checker went silent.
        spacing=sorted(v for v in _dominant(spacing) if isinstance(v, float)),
        fontSizes=measured_scale(sizes, FONT_SIZE_CLUSTER_PX),
        weights=sorted(v for v in _dominant(weights) if isinstance(v, int)),
        families=[v for v in _dominant(families) if isinstance(v, str)],
        palette=[v for v in _dominant(palette) if isinstance(v, str)],
        lineHeights=sorted(v for v in _dominant(leading) if isinstance(v, float)),
    )


def measured_scale(uses: Counter[float], tolerance: float) -> list[float]:
    """The distinct values a page actually chose, clustered, not weighted by volume.

    Share-of-volume is the wrong generator and it broke the same way twice. For type,
    body copy is most of a page's text nodes, so every heading fell under the 5% floor
    and was then reported off a scale that described only the body. For spacing, small
    gaps between adjacent items vastly outnumber the large ones between sections, so
    every section gap on the site came back as an outlier against a scale of margins.

    What matters is whether a value was *chosen*, not how many elements wear it. Two uses
    is a decision; one is a slip and stays off the scale. Clustering pools near-identical
    values — a fluid heading landing on 27.85px and 28px is one decision, and an 8px gap
    beside a 1px border reads as 9px — and every member of a surviving cluster stays on
    the scale, because keeping only the busiest re-flags the rest.

    A page with no coherent scale gets an empty one, and `off_scale` then flags nothing.
    Silence beats flagging everything.
    """
    clusters: list[list[float]] = []
    for value in sorted(uses):
        # Against the anchor, not the last member: chained on the last, 11.2 reaches 12
        # reaches 13 reaches 14, and a whole page of body sizes collapses into one step.
        if clusters and value - clusters[-1][0] <= tolerance:
            clusters[-1].append(value)
        else:
            clusters.append([value])
    return sorted(
        value
        for cluster in clusters
        if sum(uses[v] for v in cluster) >= MIN_SCALE_USES
        for value in cluster
    )


def off_scale(value: float, scale: list[float], tolerance: float) -> bool:
    """True when `value` is not within tolerance of anything on the scale."""
    return bool(scale) and all(abs(value - step) > tolerance for step in scale)


def nearest_step(value: float, scale: list[float]) -> float | None:
    return min(scale, key=lambda step: abs(step - value)) if scale else None
