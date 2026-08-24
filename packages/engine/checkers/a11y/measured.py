"""Group E, the accessibility facts that are arithmetic — SPEC §8.4 E.

axe covers the rule-shaped ones. These are the measurements it does not make.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.geometry import contains
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import Surface, element_finding, surfaces
from engine.issues.models import Category, Finding, Severity

MIN_TAP_TARGET_PX = 44.0
"""WCAG 2.5.5 / 2.5.8. Under this and thumbs miss."""

MOBILE_MAX_WIDTH = 500

MAX_ANCESTORS = 12
"""Deep enough for any real wrapper chain, shallow enough that a cycle cannot hang us."""


@checker
class TapTargets:
    id = "a11y.tap-target"
    category = Category.a11y
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            if surface.viewport.width > MOBILE_MAX_WIDTH:
                continue
            for element in surface.laid_out:
                if not element.clickable:
                    continue
                # WCAG exempts a link sitting inside a sentence; sizing it up would break
                # the paragraph it lives in.
                parent = surface.by_id.get(element.parentId or "")
                if element.role == "link" and parent is not None and parent.text:
                    continue
                if _inside_a_target(surface, element):
                    continue
                width, height = element.box.w, element.box.h
                if width >= MIN_TAP_TARGET_PX and height >= MIN_TAP_TARGET_PX:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="tap-target-too-small",
                    title=f"Tap target smaller than {MIN_TAP_TARGET_PX:g}×{MIN_TAP_TARGET_PX:g}px",
                    description=f"Anything under {MIN_TAP_TARGET_PX:g}px square is hard to "
                    "hit on a phone.",
                    expected=f"at least {MIN_TAP_TARGET_PX:g}×{MIN_TAP_TARGET_PX:g}px",
                    actual=f"{width:g}×{height:g}px",
                    groupAs=surface.viewport.name,
                    data={"width": width, "height": height, "text": element.text[:60]},
                )


def _inside_a_target(surface: Surface, element: ElementRecord) -> bool:
    """Is this thing part of a bigger target rather than a target itself?

    `clickable` is inherited in practice — `cursor: pointer` on a button applies to every
    span inside it — so without this every icon and label in a control is reported as its
    own undersized tap target. What the thumb actually hits is the ancestor.
    """
    seen = 0
    parent = surface.by_id.get(element.parentId or "")
    while parent is not None and seen < MAX_ANCESTORS:
        if parent.clickable and contains(parent.box, element.box):
            return True
        parent = surface.by_id.get(parent.parentId or "")
        seen += 1
    return False
