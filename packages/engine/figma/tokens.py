"""Design tokens from the Figma file — SPEC §6.

Extracted even when only a desktop frame exists. These are what let the deterministic
checkers judge a tablet or mobile viewport that has no design frame at all, so this runs
whether or not any frame is mapped to a route.
"""

from __future__ import annotations

from collections import Counter

from engine.checkers import colour as colour_maths
from engine.figma.models import FigmaDocument, Tokens

CLUSTER_DELTA_E = 2.0
"""SPEC §6: cluster the palette with a small ΔE. Two fills a designer considers the same
colour should not become two tokens because one was typed by hand."""

MIN_USES = 1


def _ranked(counts: Counter[float]) -> list[float]:
    return [value for value, _ in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))]


def cluster_palette(counts: Counter[str]) -> tuple[list[str], dict[str, int]]:
    """Merge near-identical fills, keeping the most-used member as the token."""
    palette: list[str] = []
    usage: dict[str, int] = {}
    for value, count in counts.most_common():
        match = colour_maths.nearest(value, palette)
        if match is not None and match[1] <= CLUSTER_DELTA_E:
            usage[match[0]] += count
            continue
        palette.append(value)
        usage[value] = count
    return palette, usage


def extract(document: FigmaDocument) -> Tokens:
    fills: Counter[str] = Counter()
    sizes: Counter[float] = Counter()
    weights: Counter[int] = Counter()
    families: Counter[str] = Counter()
    spacing: Counter[float] = Counter()
    radii: Counter[float] = Counter()
    strokes: Counter[float] = Counter()
    shadows: Counter[str] = Counter()

    for node in document.nodes.values():
        if not node.visible:
            continue
        if node.fill:
            fills[node.fill] += 1
        if node.stroke:
            fills[node.stroke] += 1
            strokes[round(node.strokeWeight, 2)] += 1
        if node.style and node.style.fontSize:
            sizes[node.style.fontSize] += 1
            weights[node.style.fontWeight] += 1
            if node.style.fontFamily:
                families[node.style.fontFamily] += 1
        if node.itemSpacing:
            spacing[round(node.itemSpacing, 2)] += 1
        for value in node.padding:
            if value:
                spacing[round(value, 2)] += 1
        for value in node.cornerRadius:
            if value:
                radii[round(value, 2)] += 1
        if node.shadow != "none":
            shadows[node.shadow] += 1

    palette, usage = cluster_palette(fills)
    return Tokens(
        palette=palette,
        paletteUsage=usage,
        typeScale=sorted(v for v, c in sizes.items() if c >= MIN_USES),
        fontFamilies=[f for f, _ in families.most_common()],
        fontWeights=sorted(w for w, c in weights.items() if c >= MIN_USES),
        spacing=sorted(v for v, c in spacing.items() if c >= MIN_USES),
        radii=sorted(radii),
        strokeWidths=sorted(s for s in strokes if s),
        shadows=[s for s, _ in shadows.most_common()],
    )
