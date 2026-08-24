"""Group F, what changes between viewports — SPEC §8.4 F.

Everything else in the catalogue already runs at every viewport. These are the findings
that only exist by *comparing* viewports.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import Surface, element_finding, live_pages, surfaces
from engine.issues.models import Category, Finding, Severity

MIN_CONTENT_CHARS = 25
"""Long enough to be content rather than a label. Short strings vanish between
breakpoints for good reasons all the time."""

MOBILE_MAX_WIDTH = 500
NAV_LANDMARKS = frozenset({"nav", "navigation", "banner", "header", "contentinfo", "footer"})


@checker
class ContentParity:
    id = "responsive.content-parity"
    category = Category.responsive
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            by_viewport = {s.viewport.name: s for s in surfaces(ctx) if s.page.id == page.id}
            if len(by_viewport) < 2:
                continue
            widest = max(by_viewport.values(), key=lambda s: s.viewport.width)
            for surface in by_viewport.values():
                if surface.viewport.name == widest.viewport.name:
                    continue
                present = {e.stableKey for e in surface.elements if e.visible}
                for element in _content(widest):
                    if element.stableKey in present:
                        continue
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="content-missing-at-viewport",
                        title=f"Content on {widest.viewport.name} is absent at "
                        f"{surface.viewport.name}",
                        description="Hiding content at a breakpoint is a decision; losing "
                        "it is a defect. This one is not in the DOM at all.",
                        expected=f"present at {surface.viewport.name}",
                        actual="missing",
                        data={
                            "text": element.text[:120],
                            "wideViewport": widest.viewport.name,
                        },
                    )


def _content(surface: Surface) -> Iterable[ElementRecord]:
    """Body copy, not chrome. A nav collapsed behind a hamburger is not missing content,
    and reporting it that way is how this checker would earn a reputation for noise."""
    for element in surface.laid_out:
        if len(element.text.strip()) < MIN_CONTENT_CHARS:
            continue
        if element.nearestLandmark in NAV_LANDMARKS:
            continue
        if element.childIds:
            continue  # only leaf text, or every wrapper reports the same string
        yield element


@checker
class MobileTables:
    id = "responsive.table-overflow"
    category = Category.responsive
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            if surface.viewport.width > MOBILE_MAX_WIDTH or surface.viewport.width <= 0:
                continue
            for element in surface.laid_out:
                if element.tag != "table":
                    continue
                width = max(element.box.w, element.scrollW)
                if width <= surface.viewport.width + 2:
                    continue
                if self._has_scroll_container(surface, element):
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="table-overflows-mobile",
                    title=f"Table is {width:g}px wide in a {surface.viewport.width}px viewport",
                    description="With no scroll container and no stacked layout, the "
                    "right-hand columns are unreachable.",
                    expected="a scroll container, or a stacked layout",
                    actual=f"{width:g}px of table in {surface.viewport.width}px",
                    data={"tableWidth": width, "viewport": surface.viewport.width},
                )

    def _has_scroll_container(self, surface: Surface, element: ElementRecord) -> bool:
        current = surface.by_id.get(element.parentId or "")
        depth = 0
        while current is not None and depth < 4:
            if current.styles.overflow in ("auto", "scroll"):
                return True
            current = surface.by_id.get(current.parentId or "")
            depth += 1
        return False
