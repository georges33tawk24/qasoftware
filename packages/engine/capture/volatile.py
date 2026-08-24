"""Finding the parts of a page that will never hold still — SPEC §5.

A timestamp, a carousel, a rotating testimonial, an A/B variant: they change between two
identical loads, so they produce a finding on every run and destroy the determinism
guarantee that makes the rest of the product trustworthy. Masking them by hand means
somebody has to notice first, which they will not.

So: load the same page twice and diff the element records. Anything that moved, resized,
appeared or vanished with nothing else changing is *nominated* — never masked
automatically. The user confirms, the same way project knowledge works, because a
section that genuinely broke between two loads looks identical to a carousel from here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlsplit

from engine.artifact.models import ElementRecord, RunConfig, Viewport
from engine.capture.driver import BrowserDriver, ContextOptions, get_driver
from engine.capture.secrets import Redactor
from engine.capture.snapshot import capture_elements
from engine.capture.stability import settle
from engine.visual import MOVED_PX

MIN_SIGNATURE_PARTS = 1
SAMPLE_TEXT = 60


@dataclass
class Candidate:
    """One thing that would not hold still, and why we think so."""

    selector: str
    kind: str
    """`moved`, `resized`, `text`, `appeared` or `vanished`."""

    detail: str = ""
    stableKey: str = ""

    def suggestion(self) -> str:
        """A selector a person can read and edit, not a captured nth-of-type chain."""
        return self.selector


@dataclass
class VolatileReport:
    url: str
    viewport: str
    candidates: list[Candidate] = field(default_factory=list)
    compared: int = 0
    """How many elements were seen in both loads. Zero means the page did not settle and
    the answer is not trustworthy."""

    def selectors(self) -> list[str]:
        seen: dict[str, None] = {}
        for candidate in self.candidates:
            seen.setdefault(candidate.suggestion(), None)
        return list(seen)


def _short(element: ElementRecord) -> str:
    """The shortest selector that still identifies this element to a human.

    A class is what someone would write in a mask list; the captured `nth-of-type` chain
    is precise and useless to read, so it is only the fallback.
    """
    if element.htmlId:
        return f"#{element.htmlId}"
    meaningful = [c for c in element.classes if not c.isdigit() and len(c) > 2]
    if meaningful:
        return f"{element.tag.lower()}." + ".".join(sorted(meaningful)[:2])
    return element.selector or element.tag.lower()


CASCADE_SLACK_PX = 2.0
"""An ad slot that loads a pixel taller pushes everything below it down by the same
amount. Within this much of its container's own shift, a child has not moved — the
container has, and nominating both is how one volatile region becomes four hundred."""


def _inherited(
    element: ElementRecord,
    shift: tuple[float, float],
    shifts: dict[str, tuple[float, float]],
    by_id: dict[str, ElementRecord],
) -> bool:
    """Did this move only because something above it did?

    CLAUDE.md says the same thing about design deltas: measure within the container, so
    one shifted section cannot cascade into a finding on everything it holds.
    """
    parent = by_id.get(element.parentId or "")
    seen = 0
    while parent is not None and seen < 24:
        above = shifts.get(parent.stableKey)
        if above is not None:
            return (
                abs(shift[0] - above[0]) <= CASCADE_SLACK_PX
                and abs(shift[1] - above[1]) <= CASCADE_SLACK_PX
            )
        parent = by_id.get(parent.parentId or "")
        seen += 1
    return False


def compare(first: list[ElementRecord], second: list[ElementRecord]) -> list[Candidate]:
    """What differs between two loads of the same unchanged page."""
    before = {e.stableKey: e for e in first if e.stableKey}
    after = {e.stableKey: e for e in second if e.stableKey}
    by_id = {e.id: e for e in second}
    shifts = {
        key: (element.box.x - before[key].box.x, element.box.y - before[key].box.y)
        for key, element in after.items()
        if key in before
    }
    out: list[Candidate] = []

    for key, element in after.items():
        was = before.get(key)
        if was is None:
            out.append(Candidate(_short(element), "appeared", "not on the first load", key))
            continue
        shift = shifts[key]
        moved = max(abs(shift[0]), abs(shift[1]))
        resized = max(abs(element.box.w - was.box.w), abs(element.box.h - was.box.h))
        text_now = (element.textFull or element.text or "")[:SAMPLE_TEXT]
        text_was = (was.textFull or was.text or "")[:SAMPLE_TEXT]
        if text_now != text_was:
            out.append(Candidate(_short(element), "text", f"{text_was!r} → {text_now!r}", key))
        elif moved > MOVED_PX and not _inherited(element, shift, shifts, by_id):
            out.append(Candidate(_short(element), "moved", f"by {moved:.0f}px", key))
        elif resized > MOVED_PX:
            out.append(Candidate(_short(element), "resized", f"by {resized:.0f}px", key))

    for key, element in before.items():
        if key not in after:
            out.append(Candidate(_short(element), "vanished", "not on the second load", key))

    return rollup(out, {**{e.id: e for e in first}, **by_id})


ROLLUP_MIN = 3
"""An ancestor holding this many volatile descendants *is* the volatile region. Below it,
the children are worth naming individually."""


def rollup(candidates: list[Candidate], by_id: dict[str, ElementRecord]) -> list[Candidate]:
    """Report the container, not the four hundred things inside it.

    An ad slot whose internals differ on every load produces a candidate per span. All of
    them are true and the list is useless: what a person needs is `ins.adsbygoogle`, once.
    So each candidate is attributed to the highest ancestor that holds enough of them to
    be the region itself.
    """
    by_key = {e.stableKey: e for e in by_id.values() if e.stableKey}
    found = {c.stableKey for c in candidates if c.stableKey}

    covers: dict[str, int] = {}
    chains: dict[str, list[str]] = {}
    for candidate in candidates:
        element = by_key.get(candidate.stableKey)
        chain: list[str] = []
        parent = by_id.get(element.parentId or "") if element else None
        seen = 0
        while parent is not None and seen < 24:
            if parent.stableKey:
                chain.append(parent.stableKey)
                covers[parent.stableKey] = covers.get(parent.stableKey, 0) + 1
            parent = by_id.get(parent.parentId or "")
            seen += 1
        chains[candidate.stableKey] = chain

    out: dict[str, Candidate] = {}
    for candidate in candidates:
        absorbing = [
            key for key in chains.get(candidate.stableKey, []) if covers.get(key, 0) >= ROLLUP_MIN
        ]
        if absorbing:
            # The *highest* one: the outermost thing that is entirely volatile.
            top = absorbing[-1]
            element = by_key.get(top)
            if element is not None:
                out.setdefault(
                    top,
                    Candidate(
                        _short(element),
                        "region",
                        f"{covers[top]} things inside it change between loads",
                        top,
                    ),
                )
                continue
        if candidate.stableKey not in found or candidate.stableKey not in out:
            out.setdefault(candidate.stableKey or candidate.selector, candidate)

    return sorted(out.values(), key=lambda c: (c.kind, c.selector))


async def sample(
    url: str,
    *,
    config: RunConfig | None = None,
    viewport: Viewport | None = None,
    driver: BrowserDriver | None = None,
    loads: int = 2,
) -> VolatileReport:
    """Load the page `loads` times in a fresh context each time, and diff.

    A fresh context each time on purpose: a carousel that only rotates on a warm cache,
    or an A/B variant pinned by a cookie, would sit still in a reused one and never be
    nominated.
    """
    from engine.capture import consent

    config = config or RunConfig()
    viewport = viewport or max(config.viewports, key=lambda v: v.width)
    report = VolatileReport(url=url, viewport=viewport.name)

    owned = driver is None
    driver = driver or get_driver(config.driver)
    if owned:
        await driver.launch()

    redactor = Redactor()
    snapshots: list[list[ElementRecord]] = []
    try:
        for _ in range(max(2, loads)):
            context = await driver.new_context(ContextOptions(viewport=viewport), redactor)
            page = await driver.new_page(context)
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=config.pageTimeoutMs)
                await consent.dismiss(page, config.consentSelectors)
                await settle(page, timeout_ms=config.pageTimeoutMs, settle_ms=config.settleMs)
                snapshots.append(await capture_elements(page, max_elements=config.maxElements))
            finally:
                await page.close()
                await context.close()
    finally:
        if owned:
            await driver.close()

    keys = [{e.stableKey for e in shot if e.stableKey} for shot in snapshots]
    report.compared = len(set.intersection(*keys)) if keys else 0

    # Only what differed on *every* pair. One rotation of a carousel between loads one and
    # two is enough; a single element that happened to move once is noise.
    common: dict[str, Candidate] = {}
    for index in range(len(snapshots) - 1):
        found = {
            c.stableKey or c.selector: c for c in compare(snapshots[index], snapshots[index + 1])
        }
        common = found if index == 0 else {k: v for k, v in common.items() if k in found}
    report.candidates = sorted(common.values(), key=lambda c: (c.kind, c.selector))
    return report


def host_of(url: str) -> str:
    return urlsplit(url).netloc
