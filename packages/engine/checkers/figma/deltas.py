"""Catalogue group J — the design comparison, SPEC §8.4 J.

Every delta is arithmetic between a matched pair, converted to live pixels, against the
per-project tolerances in SPEC §7. Nothing here is a judgement call; the judgement calls
belong to the agents in §9.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers import colour
from engine.checkers.base import checker
from engine.checkers.figma.support import Pair, boxed, matched_surfaces, px, tolerances
from engine.checkers.support import element_finding, page_finding, surfaces, synthetic_key
from engine.figma.models import NodeRole
from engine.issues.models import Category, Finding, Severity

TEXT_TYPES = frozenset({"TEXT"})


def _local_offset(pair: Pair, axis: str) -> tuple[float, float] | None:
    """Where the element sits inside its container, versus where the node does.

    Comparing absolute positions makes one shifted section report a delta on every
    descendant it contains. Comparing the offset within the matched container puts the
    finding where the mistake actually is.
    """
    if pair.containerNode is None or pair.containerElement is None:
        return None
    if axis == "x":
        design = (pair.node.box.x - pair.containerNode.box.x) * pair.scale
        live = pair.element.box.x - pair.containerElement.box.x
    else:
        design = (pair.node.box.y - pair.containerNode.box.y) * pair.scale
        live = pair.element.box.y - pair.containerElement.box.y
    return round(design, 2), round(live, 2)


@checker
class Geometry:
    id = "figma.geometry"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        tol = tolerances(ctx)
        for match in matched_surfaces(ctx):
            for pair in boxed(match):
                yield from self._position(pair, tol.positionPx)
                yield from self._size(pair, tol.sizePx, tol.sizeRatio)

    def _position(self, pair: Pair, tolerance: float) -> Iterable[Finding]:
        for axis, label in (("x", "left"), ("y", "top")):
            offsets = _local_offset(pair, axis)
            if offsets is None:
                continue
            design, live = offsets
            drift = round(live - design, 2)
            if abs(drift) <= tolerance:
                continue
            yield element_finding(
                self,
                pair.surface,
                pair.element,
                kind=f"design-position-{axis}",
                title=f"{label.capitalize()} edge is {abs(drift):g}px from the design",
                description="Measured inside "
                f"{pair.containerNode.name if pair.containerNode else 'the frame'}.",
                expected=px(design),
                actual=px(live),
                groupAs=f"position-{axis}",
                data={
                    "axis": axis,
                    "designPx": design,
                    "livePx": live,
                    "deltaPx": drift,
                    "layer": pair.node.name,
                },
            )

    def _size(self, pair: Pair, tolerance: float, ratio: float) -> Iterable[Finding]:
        for axis, design_value, live_value in (
            ("w", pair.live(pair.node.box.w), pair.element.box.w),
            ("h", pair.live(pair.node.box.h), pair.element.box.h),
        ):
            delta = round(live_value - design_value, 2)
            allowed = max(tolerance, design_value * ratio)
            if abs(delta) <= allowed:
                continue
            yield element_finding(
                self,
                pair.surface,
                pair.element,
                kind=f"design-size-{axis}",
                title=("Width" if axis == "w" else "Height")
                + f" is {abs(delta):g}px from the design",
                expected=px(round(design_value, 2)),
                actual=px(round(live_value, 2)),
                groupAs=f"size-{axis}",
                data={
                    "designPx": round(design_value, 2),
                    "livePx": live_value,
                    "deltaPx": delta,
                    "layer": pair.node.name,
                },
            )


@checker
class Colour:
    id = "figma.colour"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        threshold = tolerances(ctx).colourDeltaE
        for match in matched_surfaces(ctx):
            for pair in match.pairs:
                node, element = pair.node, pair.element
                if node.type in TEXT_TYPES:
                    yield from self._compare(
                        pair, "text", node.fill, element.styles.color, threshold
                    )
                else:
                    yield from self._compare(
                        pair, "background", node.fill, element.styles.backgroundColor, threshold
                    )
                if node.stroke and any(w > 0 for w in element.styles.borderWidth):
                    yield from self._compare(
                        pair, "border", node.stroke, element.styles.borderColor, threshold
                    )

    def _compare(
        self, pair: Pair, what: str, design: str | None, live: str, threshold: float
    ) -> Iterable[Finding]:
        if not design or not live:
            return
        delta = colour.distance(design, live)
        if delta is None or delta <= threshold:
            return
        yield element_finding(
            self,
            pair.surface,
            pair.element,
            kind=f"design-{what}-colour",
            title=f"{what.capitalize()} colour is ΔE {delta:.1f} from the design",
            expected=design,
            actual=live,
            groupAs=f"{what}-colour",
            data={"deltaE": round(delta, 2), "layer": pair.node.name},
        )


@checker
class Typography:
    id = "figma.typography"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        tol = tolerances(ctx)
        for match in matched_surfaces(ctx):
            for pair in match.pairs:
                style = pair.node.style
                if style is None or pair.node.type not in TEXT_TYPES:
                    continue
                live = pair.element.styles
                design_size = pair.live(style.fontSize)
                if style.fontSize and abs(live.fontSize - design_size) > tol.fontSizePx:
                    yield self._finding(
                        pair, "font-size", "Font size", px(round(design_size, 2)), px(live.fontSize)
                    )
                if style.fontWeight and live.fontWeight != style.fontWeight:
                    yield self._finding(
                        pair,
                        "font-weight",
                        "Font weight",
                        str(style.fontWeight),
                        str(live.fontWeight),
                    )
                design_family = style.fontFamily.strip()
                live_family = live.fontFamily.split(",")[0].strip().strip("\"'")
                if design_family and live_family.casefold() != design_family.casefold():
                    yield self._finding(
                        pair, "font-family", "Font family", design_family, live_family
                    )
                if style.lineHeightPx and live.lineHeight:
                    design_leading = pair.live(style.lineHeightPx)
                    if abs(live.lineHeight - design_leading) > tol.lineHeightPx:
                        yield self._finding(
                            pair,
                            "line-height",
                            "Line height",
                            px(round(design_leading, 2)),
                            px(live.lineHeight),
                        )
                design_tracking = pair.live(style.letterSpacing)
                if abs(live.letterSpacing - design_tracking) > tol.letterSpacingPx:
                    yield self._finding(
                        pair,
                        "letter-spacing",
                        "Letter spacing",
                        px(round(design_tracking, 2)),
                        px(live.letterSpacing),
                    )

    def _finding(self, pair: Pair, kind: str, label: str, expected: str, actual: str) -> Finding:
        return element_finding(
            self,
            pair.surface,
            pair.element,
            kind=f"design-{kind}",
            title=f"{label} differs from the design",
            expected=expected,
            actual=actual,
            groupAs=kind,
            data={"layer": pair.node.name},
        )


@checker
class Spacing:
    id = "figma.spacing"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    EDGES = ("Top", "Right", "Bottom", "Left")

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        tolerance = tolerances(ctx).spacingPx
        for match in matched_surfaces(ctx):
            for pair in boxed(match):
                node, styles = pair.node, pair.element.styles
                if node.itemSpacing:
                    design = pair.live(node.itemSpacing)
                    if abs(styles.gap - design) > tolerance:
                        yield element_finding(
                            self,
                            pair.surface,
                            pair.element,
                            kind="design-gap",
                            title=f"Gap is {abs(styles.gap - design):g}px from the design",
                            expected=px(round(design, 2)),
                            actual=px(styles.gap),
                            groupAs="gap",
                            data={"layer": node.name},
                        )
                live_padding = [
                    styles.paddingTop,
                    styles.paddingRight,
                    styles.paddingBottom,
                    styles.paddingLeft,
                ]
                for edge, design_value, live_value in zip(
                    self.EDGES, node.padding, live_padding, strict=True
                ):
                    if not design_value and not live_value:
                        continue
                    design = pair.live(design_value)
                    if abs(live_value - design) <= tolerance:
                        continue
                    yield element_finding(
                        self,
                        pair.surface,
                        pair.element,
                        kind=f"design-padding-{edge.lower()}",
                        title=f"{edge} padding is {abs(live_value - design):g}px from the design",
                        expected=px(round(design, 2)),
                        actual=px(live_value),
                        groupAs=f"padding-{edge.lower()}",
                        data={"layer": node.name},
                    )


@checker
class Decoration:
    id = "figma.decoration"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        tol = tolerances(ctx)
        for match in matched_surfaces(ctx):
            for pair in boxed(match):
                node, styles = pair.node, pair.element.styles
                design_radius = pair.live(node.cornerRadius[0])
                if abs(styles.borderRadius[0] - design_radius) > tol.radiusPx:
                    yield element_finding(
                        self,
                        pair.surface,
                        pair.element,
                        kind="design-radius",
                        title="Corner radius differs from the design",
                        expected=px(round(design_radius, 2)),
                        actual=px(styles.borderRadius[0]),
                        groupAs="radius",
                        data={"layer": node.name},
                    )
                design_border = pair.live(node.strokeWeight)
                if abs(styles.borderWidth[0] - design_border) > tol.borderWidthPx:
                    yield element_finding(
                        self,
                        pair.surface,
                        pair.element,
                        kind="design-border-width",
                        title="Border width differs from the design",
                        expected=px(round(design_border, 2)),
                        actual=px(styles.borderWidth[0]),
                        groupAs="border-width",
                        data={"layer": node.name},
                    )
                if abs(styles.opacity - node.opacity) > tol.opacity:
                    yield element_finding(
                        self,
                        pair.surface,
                        pair.element,
                        kind="design-opacity",
                        title="Opacity differs from the design",
                        expected=f"{node.opacity:g}",
                        actual=f"{styles.opacity:g}",
                        groupAs="opacity",
                        data={"layer": node.name},
                    )
                # Shadows are compared on presence, not on string equality: two shadow
                # definitions that render identically rarely serialise identically.
                design_shadow = node.shadow != "none"
                live_shadow = styles.boxShadow != "none"
                if design_shadow != live_shadow:
                    yield element_finding(
                        self,
                        pair.surface,
                        pair.element,
                        kind="design-shadow",
                        title="Shadow is in the design but not on the page"
                        if design_shadow
                        else "Shadow is on the page but not in the design",
                        expected=node.shadow,
                        actual=styles.boxShadow,
                        groupAs="shadow",
                        data={"layer": node.name},
                    )


@checker
class Content:
    id = "figma.content"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        from engine.matching.signals import normalise_text

        for match in matched_surfaces(ctx):
            for pair in match.pairs:
                node, element = pair.node, pair.element
                # `characters`, not `text`: a container's label text exists to make
                # matching possible and would otherwise report its child's copy twice.
                design = normalise_text(node.characters)
                live = normalise_text(element.text) or normalise_text(element.textFull)
                if design and live and design != live:
                    yield element_finding(
                        self,
                        pair.surface,
                        element,
                        kind="design-text-content",
                        title="Copy does not match the design",
                        expected=node.characters,
                        actual=element.text or element.textFull,
                        groupAs="text-content",
                        data={"layer": node.name},
                    )
                if node.role is NodeRole.image and element.image is None and not element.text:
                    yield element_finding(
                        self,
                        pair.surface,
                        element,
                        kind="design-image-missing",
                        title="The design places an image here and the page has none",
                        expected="an image",
                        actual=element.tag,
                        severity=Severity.minor,
                        groupAs="image",
                        data={"layer": node.name},
                    )


@checker
class Presence:
    id = "figma.presence"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        document = ctx.figma()
        if document is None:
            return
        for match in matched_surfaces(ctx):
            for record in match.mapping.matches:
                if not record.unmatched or not record.figmaNodeId:
                    continue
                node = document.nodes.get(record.figmaNodeId)
                if node is None or node.id == match.mapping.frameId or not node.text:
                    continue
                # SPEC §7: never a property diff. A pair we could not make confidently is
                # a *possible* missing element and nothing more.
                yield page_finding(
                    self,
                    match.surface.page,
                    viewport=match.surface.viewport.name,
                    kind="possible-missing-element",
                    title=f"Possibly missing: {node.name}",
                    description=(
                        "In the design and not matched on the page. The matcher could not "
                        f"pair it ({record.rejectedBecause or 'no candidate'}), so this is a "
                        "lead rather than a measurement."
                    ),
                    expected=node.text or node.name,
                    actual="not found on the page",
                    severity=Severity.minor,
                    groupAs="missing",
                    stable_key=synthetic_key(self.id, node.name, node.text),
                    box=None,
                    data={"layer": node.name, "score": record.score, "nodeId": node.id},
                )

            index = match.surface.by_id
            for record in match.mapping.matches:
                if not record.unmatched or record.figmaNodeId or not record.elementId:
                    continue
                element = index.get(record.elementId)
                if element is None or not element.text:
                    continue
                yield element_finding(
                    self,
                    match.surface,
                    element,
                    kind="possible-extra-element",
                    title="Possibly not in the design",
                    description="On the page and not matched to any design node.",
                    expected="a matching design node",
                    actual=element.text[:80],
                    severity=Severity.minor,
                    groupAs="extra",
                )


@checker
class NoMatch:
    id = "figma.no-match"
    category = Category.figma
    requires = frozenset({Capability.FIGMA, Capability.MAPPING})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        """The failure mode, reported once and loudly.

        Pointing the tool at the wrong Figma file must produce this, not four hundred
        plausible-looking deltas.
        """
        for surface in surfaces(ctx):
            mapping = ctx.mapping(surface.page.id, surface.viewport.name)
            if mapping is None or mapping.confident:
                continue
            total = mapping.matched + mapping.unmatchedNodes
            yield page_finding(
                self,
                surface.page,
                viewport=surface.viewport.name,
                kind="design-could-not-match",
                title=f"Could not match {mapping.frameName!r} to this page",
                description=(
                    f"{mapping.matched} of {total} design nodes paired, with "
                    f"{mapping.anchors} text anchors. That is too little agreement to "
                    "measure against, so no design deltas were reported for this page. "
                    "Check the frame is mapped to the right route, or pin a few layers."
                ),
                expected="a frame that matches this page",
                actual=f"{mapping.matched}/{total} matched",
                stable_key=synthetic_key(self.id, mapping.frameId),
                data={
                    "frameId": mapping.frameId,
                    "frameName": mapping.frameName,
                    "matched": mapping.matched,
                    "unmatchedNodes": mapping.unmatchedNodes,
                    "anchors": mapping.anchors,
                },
            )
