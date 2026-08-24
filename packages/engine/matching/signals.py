"""The scoring signals from SPEC §7.

Never a single heuristic: text carries most of the weight because it is the strongest
signal by far, but a page is full of boxes with no text in them, and those have to match
on something.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from engine.artifact.models import Box, ElementRecord
from engine.figma.models import FigmaNode, NodeRole

WEIGHTS = {
    "text": 0.35,
    "textSimilarity": 0.20,
    "position": 0.20,
    "role": 0.10,
    "size": 0.10,
    "name": 0.05,
}
"""SPEC §7, verbatim."""

TEXT_SIGNALS = ("text", "textSimilarity")

_PUNCTUATION = re.compile(r"[^\w\s]+", re.UNICODE)
_SPLIT = re.compile(r"[\s_\-/.]+")

ROLE_FROM_TAG = {
    "h1": NodeRole.heading,
    "h2": NodeRole.heading,
    "h3": NodeRole.heading,
    "h4": NodeRole.heading,
    "h5": NodeRole.heading,
    "h6": NodeRole.heading,
    "button": NodeRole.button,
    "input": NodeRole.input,
    "textarea": NodeRole.input,
    "select": NodeRole.input,
    "img": NodeRole.image,
    "svg": NodeRole.icon,
    "p": NodeRole.text,
    "span": NodeRole.text,
    "a": NodeRole.button,
    "li": NodeRole.text,
}

COMPATIBLE = {
    frozenset({NodeRole.heading, NodeRole.text}),
    frozenset({NodeRole.button, NodeRole.text}),
    frozenset({NodeRole.container, NodeRole.frame}),
    frozenset({NodeRole.image, NodeRole.icon}),
}


def normalise_text(value: str | None) -> str:
    """Case, whitespace and punctuation insensitive (SPEC §7)."""
    return " ".join(_PUNCTUATION.sub(" ", (value or "").casefold()).split())


def levenshtein_ratio(a: str, b: str) -> float:
    """1 - normalised edit distance. Catches copy tweaks (SPEC §7)."""
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb)))
        previous = current
    return 1 - previous[-1] / max(len(a), len(b))


def element_role(element: ElementRecord) -> NodeRole:
    if element.role in ("heading",):
        return NodeRole.heading
    if element.role in ("button", "link"):
        return NodeRole.button
    if element.role in ("textbox", "combobox", "searchbox", "checkbox", "radio"):
        return NodeRole.input
    if element.role == "img":
        return NodeRole.image
    mapped = ROLE_FROM_TAG.get(element.tag)
    if mapped is not None:
        return mapped
    return NodeRole.container if element.childIds else NodeRole.other


def _tokens(*values: str) -> set[str]:
    out: set[str] = set()
    for value in values:
        for token in _SPLIT.split(value.casefold()):
            if len(token) > 2:
                out.add(token)
    return out


def relative(box: Box, container: Box) -> tuple[float, float]:
    """Position as a fraction of the container, so the two sides are comparable at any
    scale (SPEC §7)."""
    width = container.w or 1.0
    height = container.h or 1.0
    return ((box.x - container.x) / width, (box.y - container.y) / height)


@dataclass(frozen=True)
class Pair:
    node: FigmaNode
    element: ElementRecord
    score: float
    signals: dict[str, float]


def signal_scores(
    node: FigmaNode,
    element: ElementRecord,
    *,
    node_container: Box,
    element_container: Box,
) -> dict[str, float]:
    node_text = normalise_text(node.text)
    element_text = normalise_text(element.text) or normalise_text(element.textFull)

    scores = {
        "text": 1.0 if node_text and node_text == element_text else 0.0,
        "textSimilarity": levenshtein_ratio(node_text, element_text)
        if (node_text or element_text)
        else 0.0,
        "position": _position(node, element, node_container, element_container),
        "role": _role(node, element),
        "size": _size(node, element, node_container, element_container),
        "name": _name(node, element),
    }
    return {key: round(value, 4) for key, value in scores.items()}


def _position(
    node: FigmaNode, element: ElementRecord, node_container: Box, element_container: Box
) -> float:
    nx, ny = relative(node.box, node_container)
    ex, ey = relative(element.box, element_container)
    distance = float(((nx - ex) ** 2 + (ny - ey) ** 2) ** 0.5)
    return max(0.0, 1.0 - distance / 1.4142)


def _role(node: FigmaNode, element: ElementRecord) -> float:
    theirs, ours = node.role, element_role(element)
    if theirs == ours:
        return 1.0
    return 0.5 if frozenset({theirs, ours}) in COMPATIBLE else 0.0


def _size(
    node: FigmaNode, element: ElementRecord, node_container: Box, element_container: Box
) -> float:
    def ratio(a: float, b: float) -> float:
        return min(a, b) / max(a, b) if a > 0 and b > 0 else 0.0

    width = ratio(
        node.box.w / (node_container.w or 1.0), element.box.w / (element_container.w or 1.0)
    )
    height = ratio(
        node.box.h / (node_container.h or 1.0), element.box.h / (element_container.h or 1.0)
    )
    return (width + height) / 2


def _name(node: FigmaNode, element: ElementRecord) -> float:
    """Weak but free; helps with icons and images (SPEC §7)."""
    theirs = _tokens(node.name)
    ours = _tokens(" ".join(element.classes), element.testId or "", element.htmlId or "")
    if not theirs or not ours:
        return 0.0
    return len(theirs & ours) / len(theirs | ours)


def combine(scores: dict[str, float], *, has_text: bool) -> float:
    """Weighted sum, renormalised over the signals that apply to this pair.

    SPEC §7's weights put 0.55 into text, which is right — but it also means a pair with
    no text on either side could never clear the 0.55 threshold no matter how perfectly
    it matched on everything else, so no rectangle would ever match a div. Dropping the
    text signals and rescaling the rest keeps the spec's relative weights and makes the
    threshold mean the same thing for both kinds of node.
    """
    weights = dict(WEIGHTS)
    if not has_text:
        for key in TEXT_SIGNALS:
            weights.pop(key)
    total = sum(weights.values())
    return round(sum(scores[key] * weight for key, weight in weights.items()) / total, 4)
