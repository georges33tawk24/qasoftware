"""The matching engine — SPEC §7.

The hardest part of the project and the part most likely to embarrass you, so:
top-down and container-scoped, never a flat global match; multi-signal scoring; optimal
one-to-one assignment inside each container; and a confidence threshold below which a
pair is `unmatched` and reported as a *possible* missing or extra element at low
severity, never as a property diff.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from engine.artifact.models import ElementRecord, Viewport
from engine.figma.models import FigmaDocument, FigmaNode, Frame
from engine.matching import signals
from engine.matching.assign import solve
from engine.matching.models import MappingFile, MatchRecord

THRESHOLD = 0.55
"""SPEC §7. A pair below this is unmatched, not a weak match."""

NEIGHBOURHOOD = 0.35
"""Fraction of the viewport a pair may be away from where the anchors predict it. Anchors
kill most false pairings, which is their whole job (SPEC §7 step 3)."""

FAR_COST = 10.0
MAX_DEPTH = 8
MAX_CONTAINER_CHILDREN = 60


@dataclass
class Pins:
    """The escape hatch from SPEC §7: a human pins `layerName → cssSelector` once and it
    is honoured forever after."""

    by_layer: dict[str, str] = field(default_factory=dict)

    def selector_for(self, node: FigmaNode) -> str | None:
        return self.by_layer.get(node.name)


def significant(element: ElementRecord) -> bool:
    """Does this element correspond to something a designer drew?

    The DOM has three wrappers for every box in Figma. Matching child-to-child would fail
    on every real site; skipping past transparent wrappers to the things that actually
    render is what makes a container-scoped walk possible at all.
    """
    if not element.visible or element.box.w <= 0 or element.box.h <= 0:
        return False
    styles = element.styles
    return bool(
        element.text
        or element.image
        or element.clickable
        or styles.backgroundColor != "rgba(0, 0, 0, 0)"
        or any(width > 0 for width in styles.borderWidth)
    )


def visual_children(
    element: ElementRecord, index: dict[str, ElementRecord], *, limit: int = MAX_CONTAINER_CHILDREN
) -> list[ElementRecord]:
    """The nearest significant descendants on each branch."""
    out: list[ElementRecord] = []
    stack = [index[c] for c in element.childIds if c in index]
    while stack and len(out) < limit:
        current = stack.pop(0)
        if significant(current):
            out.append(current)
        else:
            stack.extend(index[c] for c in current.childIds if c in index)
    return out


def node_children(node: FigmaNode, document: FigmaDocument) -> list[FigmaNode]:
    return [
        document.nodes[i]
        for i in node.childIds[:MAX_CONTAINER_CHILDREN]
        if i in document.nodes and document.nodes[i].visible
    ]


@dataclass
class _Context:
    frame: Frame
    viewport: Viewport
    scale: float
    offset: tuple[float, float] = (0.0, 0.0)

    def predict(self, node: FigmaNode) -> tuple[float, float]:
        """Where a node should land on the live page, in live pixels."""
        return (
            (node.box.x - self.frame.box.x) * self.scale + self.offset[0],
            (node.box.y - self.frame.box.y) * self.scale + self.offset[1],
        )


def _anchor_pairs(
    document: FigmaDocument, frame: Frame, elements: list[ElementRecord]
) -> list[tuple[FigmaNode, ElementRecord]]:
    """Text that appears exactly once on each side. These are as certain as it gets."""
    node_text: dict[str, list[FigmaNode]] = {}
    for node in document.nodes_in(frame.id):
        text = signals.normalise_text(node.text)
        if node.visible and len(text) >= 3:
            node_text.setdefault(text, []).append(node)

    element_text: dict[str, list[ElementRecord]] = {}
    for element in elements:
        text = signals.normalise_text(element.text)
        if significant(element) and len(text) >= 3:
            element_text.setdefault(text, []).append(element)

    return [
        (nodes[0], element_text[text][0])
        for text, nodes in node_text.items()
        if len(nodes) == 1 and len(element_text.get(text, [])) == 1
    ]


def _offset(
    anchors: list[tuple[FigmaNode, ElementRecord]], frame: Frame, scale: float
) -> tuple[float, float]:
    if not anchors:
        return (0.0, 0.0)
    dx = median([e.box.x - (n.box.x - frame.box.x) * scale for n, e in anchors])
    dy = median([e.box.y - (n.box.y - frame.box.y) * scale for n, e in anchors])
    return (round(dx, 2), round(dy, 2))


def match_surface(
    document: FigmaDocument,
    frame: Frame,
    elements: list[ElementRecord],
    viewport: Viewport,
    *,
    page_id: str,
    pins: Pins | None = None,
    threshold: float = THRESHOLD,
) -> MappingFile:
    pins = pins or Pins()
    index = {e.id: e for e in elements}
    scale = (viewport.width / frame.box.w) if frame.box.w else 1.0

    anchors = _anchor_pairs(document, frame, elements)
    context = _Context(
        frame=frame, viewport=viewport, scale=scale, offset=_offset(anchors, frame, scale)
    )

    mapping = MappingFile(
        pageId=page_id,
        viewport=viewport.name,
        frameId=frame.id,
        frameName=frame.name,
        scale=round(scale, 4),
        threshold=threshold,
        anchors=len(anchors),
        offsetX=context.offset[0],
        offsetY=context.offset[1],
    )

    forced: dict[str, str] = {n.id: e.id for n, e in anchors}
    for node in document.nodes_in(frame.id):
        selector = pins.selector_for(node)
        if selector:
            pinned = next((e for e in elements if e.selector == selector), None)
            if pinned is not None:
                forced[node.id] = pinned.id

    root_element = next((e for e in elements if e.parentId is None), None)
    root_node = document.nodes.get(frame.id)
    if root_element is None or root_node is None:
        return mapping

    matched: dict[str, str] = {}
    seen_elements: set[str] = set()
    queue: list[tuple[FigmaNode, ElementRecord, int]] = [(root_node, root_element, 0)]

    while queue:
        node, element, depth = queue.pop(0)
        if depth >= MAX_DEPTH:
            continue
        children = node_children(node, document)
        candidates = visual_children(element, index)
        if not candidates and element.text:
            # The design splits what the DOM keeps together: a button is a filled frame
            # with a label node inside it, but the page has one <a> carrying both. Letting
            # the element stand in for its own label is what stops that becoming a
            # phantom "missing element" on every button on the site.
            candidates = [element]
        if not children or not candidates:
            continue

        assigned = _assign(children, candidates, context, forced, threshold, node, element)
        for record, pair in assigned:
            mapping.matches.append(record)
            if pair is not None:
                child_node, child_element = pair
                matched[child_node.id] = child_element.id
                seen_elements.add(child_element.id)
                queue.append((child_node, child_element, depth + 1))

    reported = {m.figmaNodeId for m in mapping.matches if m.figmaNodeId}
    for node in document.nodes_in(frame.id):
        if node.id in reported or node.id == frame.id or not node.visible:
            continue
        if node.box.w <= 0 or node.box.h <= 0:
            continue
        mapping.matches.append(
            MatchRecord(
                figmaNodeId=node.id,
                unmatched=True,
                method="unmatched",
                nodeName=node.name,
                nodeText=node.text or None,
                rejectedBecause="no container pairing reached this node",
            )
        )

    mapping.matched = len(matched)
    mapping.unmatchedNodes = sum(1 for m in mapping.matches if m.unmatched and m.figmaNodeId)
    mapping.unmatchedElements = sum(
        1 for m in mapping.matches if m.unmatched and m.elementId and not m.figmaNodeId
    )
    return mapping


def _assign(
    children: list[FigmaNode],
    candidates: list[ElementRecord],
    context: _Context,
    forced: dict[str, str],
    threshold: float,
    container_node: FigmaNode,
    container_element: ElementRecord,
) -> list[tuple[MatchRecord, tuple[FigmaNode, ElementRecord] | None]]:
    node_box = container_node.box
    element_box = container_element.box
    limit = context.viewport.width * NEIGHBOURHOOD

    table: list[list[float]] = []
    scores: list[list[dict[str, float]]] = []
    for node in children:
        costs: list[float] = []
        row_scores: list[dict[str, float]] = []
        predicted = context.predict(node)
        for element in candidates:
            signal = signals.signal_scores(
                node, element, node_container=node_box, element_container=element_box
            )
            has_text = bool(
                signals.normalise_text(node.text) or signals.normalise_text(element.text)
            )
            score = signals.combine(signal, has_text=has_text)
            if forced.get(node.id) == element.id:
                score = 1.0
            elif node.id in forced or element.id in forced.values():
                score = 0.0  # an anchored node belongs to its anchor, not to this one
            else:
                distance = (
                    (predicted[0] - element.box.x) ** 2 + (predicted[1] - element.box.y) ** 2
                ) ** 0.5
                if distance > limit:
                    score = 0.0
            costs.append(1.0 - score if score > 0 else FAR_COST)
            row_scores.append(signal)
        table.append(costs)
        scores.append(row_scores)

    out: list[tuple[MatchRecord, tuple[FigmaNode, ElementRecord] | None]] = []
    taken: set[int] = set()
    paired_columns: set[int] = set()
    for row, column in solve(table):
        node, element = children[row], candidates[column]
        cost = table[row][column]
        score = round(1.0 - cost, 4) if cost < FAR_COST else 0.0
        if element.id == container_element.id:
            # The design split what the DOM keeps together, so this node describes the
            # element's *text* and nothing about its box.
            method = "absorbed"
        else:
            method = "anchor" if forced.get(node.id) == element.id else "assignment"
        record = MatchRecord(
            figmaNodeId=node.id,
            elementId=element.id if score >= threshold else None,
            score=score,
            method=method if score >= threshold else "unmatched",
            unmatched=score < threshold,
            signals=scores[row][column],
            nodeName=node.name,
            nodeText=node.text or None,
            selector=element.selector if score >= threshold else None,
            containerNodeId=container_node.id,
            containerElementId=container_element.id,
            rejectedBecause=(
                None if score >= threshold else f"best score {score} is under {threshold}"
            ),
        )
        out.append((record, (node, element) if score >= threshold else None))
        taken.add(row)
        if score >= threshold:
            paired_columns.add(column)

    for index, node in enumerate(children):
        if index in taken:
            continue
        out.append(
            (
                MatchRecord(
                    figmaNodeId=node.id,
                    unmatched=True,
                    method="unmatched",
                    nodeName=node.name,
                    nodeText=node.text or None,
                    containerNodeId=container_node.id,
                    containerElementId=container_element.id,
                    rejectedBecause="no element left to assign in this container",
                ),
                None,
            )
        )

    # Elements the matcher considered inside a container it understood, and could not
    # pair. This is the "on the page, absent from the design" case, and knowing the
    # container was understood is what makes it worth reporting at all.
    for index, element in enumerate(candidates):
        if index in paired_columns or element.id == container_element.id:
            continue
        out.append(
            (
                MatchRecord(
                    elementId=element.id,
                    unmatched=True,
                    method="unmatched",
                    selector=element.selector,
                    containerNodeId=container_node.id,
                    containerElementId=container_element.id,
                    rejectedBecause="no design node left to assign in this container",
                ),
                None,
            )
        )
    return out
