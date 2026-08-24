"""Shared plumbing for checkers.

Two rules live here so no individual checker has to remember them:
crawl-blocked pages never produce findings, and every finding is built the same way.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from functools import cached_property
from hashlib import sha1

from engine.artifact.context import RunContext
from engine.artifact.geometry import contains
from engine.artifact.models import Box, ElementRecord, LayoutRecord, PageRecord, Viewport
from engine.checkers.base import Checker
from engine.issues.models import Category, Evidence, Finding, Severity, Source

PAGE_KEY = "page"
"""stableKey for a finding about a whole page rather than an element."""

ANY_VIEWPORT = "*"
"""viewport for a finding that is not viewport-specific."""


@dataclass(frozen=True)
class Surface:
    """One page at one viewport — what most checkers actually iterate."""

    page: PageRecord
    viewport: Viewport
    elements: list[ElementRecord]
    layout: LayoutRecord

    @cached_property
    def by_id(self) -> dict[str, ElementRecord]:
        return {e.id: e for e in self.elements}

    @cached_property
    def children(self) -> dict[str | None, list[ElementRecord]]:
        groups: dict[str | None, list[ElementRecord]] = {}
        for element in self.elements:
            groups.setdefault(element.parentId, []).append(element)
        return groups

    @property
    def laid_out(self) -> list[ElementRecord]:
        """The elements a visual checker may reason about.

        A 1px box is the screen-reader-only idiom — a skip link, a heading that names a
        region — and it is not laid out in any sense a layout checker means. Left in, it
        reports itself as clipped text and as an undersized tap target on every site that
        does accessibility properly, which is precisely backwards.
        """
        return [
            e for e in self.elements if e.visible and e.box.w > SR_ONLY_PX and e.box.h > SR_ONLY_PX
        ]


CLIPPING_OVERFLOW = ("hidden", "auto", "scroll")
"""An ancestor with any of these clips what sticks out of it, which is how a scrolling
list keeps its rows inside its own pane."""

MAX_ANCESTOR_WALK = 24


def clipped_away(surface: Surface, element: ElementRecord) -> bool:
    """Is this element scrolled or clipped out of its own container?

    Its box still says where it *would* be, so any checker comparing boxes across
    containers has to ask. Without this, every row below the fold of a scrolling list
    "overlaps" whatever the page draws at those coordinates — a false positive on every
    site with a scrolling pane, which is most of them.
    """
    child = element
    parent = surface.by_id.get(element.parentId or "")
    for _ in range(MAX_ANCESTOR_WALK):
        if parent is None:
            return False
        if parent.styles.overflow in CLIPPING_OVERFLOW and not contains(
            parent.box, child.box, slack=CLIP_SLACK_PX
        ):
            return True
        child, parent = parent, surface.by_id.get(parent.parentId or "")
    return False


CLIP_SLACK_PX = 2.0
"""A row flush with the bottom edge of its pane is inside it, not clipped."""

SR_ONLY_PX = 1.0
"""At or under this on either axis, an element is hidden on purpose."""

OK_STATUS = range(200, 400)


def live_pages(ctx: RunContext) -> Iterator[PageRecord]:
    """Every page a finding may be emitted about.

    Challenged pages are excluded here, once, rather than in fifty checkers — SPEC §5:
    never let a challenge page produce a "blank page" bug. Error pages are excluded for
    the same reason: an unstyled 404 would otherwise report a missing title, an off-scale
    font and a dead end, none of which is the site. `free.page-status` reports the status
    itself, and that is the only finding an error page should ever produce.
    """
    for page in ctx.pages():
        if page.crawlBlocked or page.status not in OK_STATUS:
            continue
        yield page


def surfaces(ctx: RunContext) -> Iterator[Surface]:
    viewports = {v.name: v for v in ctx.viewports}
    for page in live_pages(ctx):
        for name in ctx.viewport_names(page.id):
            viewport = viewports.get(name) or Viewport(name=name, width=0, height=0)
            yield Surface(
                page=page,
                viewport=viewport,
                elements=ctx.elements(page.id, name),
                layout=ctx.layout(page.id, name),
            )


def contiguous_runs(surface: Surface, element_ids: list[str]) -> list[list[ElementRecord]]:
    """Split a repeated group into runs of *adjacent* siblings.

    Three `<p>` elements with a heading and a card grid between them share a signature but
    are not a listing, and measuring the "gaps" between them produces nonsense. A card
    grid, a nav bar and a listing are all contiguous; that is what makes them one thing.
    """
    parent = surface.by_id.get(element_ids[0]) if element_ids else None
    order = surface.by_id.get(parent.parentId or "") if parent else None
    sequence = order.childIds if order else [e.id for e in surface.elements]
    position = {eid: i for i, eid in enumerate(sequence)}
    members = sorted(
        (surface.by_id[i] for i in element_ids if i in surface.by_id and i in position),
        key=lambda e: position[e.id],
    )

    runs: list[list[ElementRecord]] = []
    for element in members:
        if runs and position[element.id] == position[runs[-1][-1].id] + 1:
            runs[-1].append(element)
        else:
            runs.append([element])
    return runs


def judged_by_design(ctx: RunContext, surface: Surface) -> bool:
    """Does group J already measure this surface against a real frame?

    SPEC §6 wires the design tokens in so that a viewport with *no* frame can still be
    judged. Where a frame did match, `figma.*` says "this is 4px from the design node",
    which is strictly better than "this is off the scale" — and saying both is noise.
    """
    mapping = ctx.mapping(surface.page.id, surface.viewport.name)
    return mapping is not None and mapping.confident


def widest_surfaces(ctx: RunContext) -> list[Surface]:
    """One surface per page, at the widest viewport.

    Copy, terminology and spelling are the same at every viewport; checking all three
    would report the same typo three times.
    """
    best: dict[str, Surface] = {}
    for surface in surfaces(ctx):
        current = best.get(surface.page.id)
        if current is None or surface.viewport.width > current.viewport.width:
            best[surface.page.id] = surface
    return [best[key] for key in sorted(best)]


def synthetic_key(*parts: str) -> str:
    """A stable key for a finding with no element behind it."""
    return sha1("\x1f".join(parts).encode()).hexdigest()


def _finding(
    checker: Checker,
    *,
    page: PageRecord,
    viewport: str,
    stable_key: str,
    kind: str,
    title: str,
    description: str = "",
    expected: str | None = None,
    actual: str | None = None,
    severity: Severity | None = None,
    groupAs: str | None = None,
    element_id: str | None = None,
    selector: str | None = None,
    box: Box | None = None,
    data: dict[str, object] | None = None,
    evidence: list[Evidence] | None = None,
) -> Finding:
    return Finding(
        checkerId=checker.id,
        issueKind=kind,
        category=Category(checker.category),
        severity=severity or checker.default_severity,
        title=title,
        description=description,
        expected=expected,
        actual=actual,
        pageId=page.id,
        pagePath=page.path,
        viewport=viewport,
        elementId=element_id,
        stableKey=stable_key,
        selector=selector,
        box=box,
        source=Source.measured,
        groupAs=groupAs,
        evidence=evidence or [],
        data=dict(data or {}),
    )


def element_finding(
    checker: Checker,
    surface: Surface,
    element: ElementRecord,
    *,
    kind: str,
    title: str,
    stable_key: str | None = None,
    **rest: object,
) -> Finding:
    """`stable_key` defaults to the element's own. Override it when the finding is really
    about something the element only happens to display — a site-wide terminology clash,
    say — so that every instance shares one durable identity."""
    return _finding(
        checker,
        page=surface.page,
        viewport=surface.viewport.name,
        stable_key=stable_key or element.stableKey,
        kind=kind,
        title=title,
        element_id=element.id,
        selector=element.selector,
        box=element.box,
        **rest,  # type: ignore[arg-type]
    )


def page_finding(
    checker: Checker,
    page: PageRecord,
    *,
    kind: str,
    title: str,
    viewport: str = ANY_VIEWPORT,
    stable_key: str = PAGE_KEY,
    **rest: object,
) -> Finding:
    return _finding(
        checker,
        page=page,
        viewport=viewport,
        stable_key=stable_key,
        kind=kind,
        title=title,
        **rest,  # type: ignore[arg-type]
    )
