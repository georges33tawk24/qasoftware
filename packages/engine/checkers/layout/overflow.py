"""Group B, the things that spill or get cut off — SPEC §8.4 B."""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.geometry import right
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import Surface, element_finding, surfaces
from engine.issues.models import Category, Finding, Severity

SLACK_PX = 2.0
CLIPPED_ABSOLUTE_SLACK = 1.0
HIDDEN_OVERFLOW = frozenset({"hidden", "clip", "auto", "scroll"})


@checker
class HorizontalOverflow:
    id = "layout.horizontal-overflow"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            width = surface.viewport.width
            if width <= 0:
                continue
            body = next((e for e in surface.elements if e.tag == "body"), None)
            culprits = sorted(
                (e for e in surface.laid_out if self._escapes(surface, e, width)),
                key=lambda e: (-right(e.box), e.domDepth),
            )
            if not culprits:
                continue
            widest = culprits[0]
            reach = right(widest.box)
            yield element_finding(
                self,
                surface,
                widest,
                kind="body-horizontal-scroll",
                title=f"Page scrolls sideways at {surface.viewport.name}",
                description=(
                    f"Content reaches {reach:g}px in a {width}px viewport. "
                    f"{len(culprits)} element(s) extend past the right edge."
                ),
                expected=f"content within {width}px",
                actual=f"{reach:g}px",
                groupAs=surface.viewport.name,
                data={
                    "reach": reach,
                    "documentWidth": body.scrollW if body else None,
                    "viewportWidth": width,
                    "widestSelector": widest.selector,
                    "overflowing": len(culprits),
                },
            )

    def _escapes(self, surface: Surface, element: ElementRecord, width: int) -> bool:
        """Past the right edge *and* actually reachable.

        Off-canvas drawers park themselves at 100vw and clipped content is not scrollable,
        so neither is a sideways-scrolling page. Checking the ancestors is what keeps this
        check honest on any site with a slide-in menu.
        """
        if right(element.box) <= width + SLACK_PX:
            return False
        if element.styles.position in ("fixed", "absolute"):
            return False
        current: ElementRecord | None = element
        while current is not None:
            if current is not element and current.styles.overflow in HIDDEN_OVERFLOW:
                return False
            current = surface.by_id.get(current.parentId or "")
        return True


@checker
class ClippedContent:
    id = "layout.clipped"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                styles = element.styles
                if styles.overflow not in HIDDEN_OVERFLOW:
                    continue
                clipped_x = element.scrollW > element.box.w + CLIPPED_ABSOLUTE_SLACK
                if not clipped_x or not element.text:
                    continue
                if styles.textOverflow == "ellipsis" or styles.overflow in ("auto", "scroll"):
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="text-clipped",
                    title="Text is cut off with no ellipsis",
                    description=(
                        f"Content is {element.scrollW:g}px wide in a {element.box.w:g}px box "
                        "with overflow hidden and no text-overflow."
                    ),
                    expected="text fits, wraps, or ends in an ellipsis",
                    actual=f"{element.scrollW - element.box.w:g}px hidden",
                    groupAs="clipped",
                    data={"scrollW": element.scrollW, "boxW": element.box.w, "text": element.text},
                )


@checker
class ContainerOverflow:
    id = "layout.container-overflow"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                parent = surface.by_id.get(element.parentId or "")
                if parent is None or parent.tag in ("body", "html"):
                    continue
                if element.styles.position in ("absolute", "fixed", "sticky"):
                    continue
                if parent.styles.overflow in HIDDEN_OVERFLOW or parent.box.w <= 0:
                    continue
                spill = right(element.box) - right(parent.box)
                if spill <= SLACK_PX:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="overflows-container",
                    title=f"Element sticks {spill:g}px out of its container",
                    expected=f"within {right(parent.box):g}px",
                    actual=f"{right(element.box):g}px",
                    groupAs=parent.selector,
                    data={"spill": spill, "container": parent.selector},
                )


@checker
class ZeroHeightWithPadding:
    id = "layout.zero-height"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.elements:
                padding = element.styles.paddingTop + element.styles.paddingBottom
                # The box is padding + content; `height: 0` still measures as the padding,
                # so the thing to test is what is left for content.
                content_height = element.box.h - padding
                if content_height > 1 or padding <= 0 or element.box.w <= 0:
                    continue
                if any(surface.by_id[c].box.h > 0 for c in element.childIds if c in surface.by_id):
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="zero-height-with-padding",
                    title="Container has padding but no height",
                    description="Usually a collapsed float or an empty slot that was "
                    "meant to hold something.",
                    expected="height greater than 0",
                    actual=f"{content_height:g}px of content in {padding:g}px of padding",
                    groupAs="zero-height",
                )
