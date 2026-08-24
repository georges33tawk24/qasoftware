"""Getting the banner out of the way — SPEC §5.

A cookie alert or a launch modal covering the fold poisons everything downstream: every
screenshot, every above-fold layout check, every contrast measurement against a dimmed
overlay, and every grounding image an agent is later shown. It has to go *before* the
first screenshot, not after.

Two passes, in order:

1. **The project's own selectors.** Always tried first, because a person who has looked
   at the site knows better than any heuristic.
2. **A heuristic pass.** Common consent frameworks by id, then any `role="dialog"` with
   a button whose text reads like dismissal.

**The most privacy-preserving option wins.** "Reject all" is preferred over "Accept all"
everywhere: we are a visitor to somebody's client's site and should behave like a careful
one. Accepting is only used when nothing else will close the thing, and it is recorded.

Nothing here is a finding. A banner that will not close is reported by the capture as a
note so the report can say the page was measured with it up, rather than pretending.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

CLICK_TIMEOUT_MS = 2_000
SETTLE_MS = 400
MAX_DISMISSALS = 4
"""A page with more than this many overlays is not a consent problem; it is a page that
wants a human to look at it."""

DECLINE_TEXT = (
    "reject all",
    "decline all",
    "reject non-essential",
    "only necessary",
    "only essential",
    "essential only",
    "necessary only",
    "strictly necessary",
    "refuse all",
    "deny all",
    "reject",
    "decline",
)
"""Most privacy-preserving first. Order matters: these are tried in sequence."""

ACCEPT_TEXT = ("accept all", "accept cookies", "i accept", "accept", "agree", "allow all")
"""Only when nothing above will close it. Recorded when used."""

CLOSE_TEXT = ("close", "dismiss", "got it", "ok", "okay", "continue", "no thanks", "not now")

KNOWN_SELECTORS = (
    # The consent frameworks common enough to be worth naming outright.
    "#onetrust-reject-all-handler",
    "#onetrust-accept-btn-handler",
    ".ot-pc-refuse-all-handler",
    "#CybotCookiebotDialogBodyButtonDecline",
    "#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowallSelection",
    "button#didomi-notice-disagree-button",
    "button#didomi-notice-agree-button",
    ".qc-cmp2-summary-buttons button[mode='secondary']",
    "[data-testid='uc-deny-all-button']",
    "[aria-label='Reject all']",
    "[aria-label='Accept all']",
    # Bootstrap and Angular Material dialogs, which is what most small sites ship.
    ".modal.show .btn-close",
    ".modal.show [data-bs-dismiss='modal']",
    "mat-dialog-container button[mat-dialog-close]",
)

DIALOG_SELECTORS = (
    "[role='dialog']",
    "[role='alertdialog']",
    "dialog[open]",
    "[id*='cookie' i]",
    "[class*='cookie' i]",
    "[id*='consent' i]",
    "[class*='consent' i]",
)


@dataclass
class Dismissal:
    """What was closed and how, so a report can say so."""

    selector: str
    label: str = ""
    accepted: bool = False
    """True when the only thing that would close it was an acceptance."""


@dataclass
class ConsentResult:
    dismissed: list[Dismissal] = field(default_factory=list)
    remaining: list[str] = field(default_factory=list)
    """Overlays still covering the page. The capture records these as a note."""

    def notes(self) -> list[str]:
        out = [
            f"dismissed an overlay via {d.selector}"
            + (" by accepting, since nothing else would close it" if d.accepted else "")
            for d in self.dismissed
        ]
        out += [f"an overlay is still covering the page: {s}" for s in self.remaining]
        return out


async def _click(page: Page, selector: str) -> bool:
    try:
        locator = page.locator(selector).first
        if not await locator.is_visible(timeout=CLICK_TIMEOUT_MS):
            return False
        await locator.click(timeout=CLICK_TIMEOUT_MS)
        await page.wait_for_timeout(SETTLE_MS)
        return True
    except (PlaywrightError, PlaywrightTimeout):
        return False


async def _click_by_text(page: Page, scope: str, phrases: tuple[str, ...]) -> str | None:
    """The first button in `scope` whose text matches, tried in the given order."""
    for phrase in phrases:
        selector = f"{scope} button:visible, {scope} [role='button']:visible, {scope} a:visible"
        try:
            candidates = page.locator(selector)
            count = min(await candidates.count(), 12)
        except (PlaywrightError, PlaywrightTimeout):
            return None
        for index in range(count):
            option = candidates.nth(index)
            with contextlib.suppress(PlaywrightError, PlaywrightTimeout):
                text = (await option.inner_text(timeout=CLICK_TIMEOUT_MS)).strip().casefold()
                if phrase in text and await option.is_visible():
                    await option.click(timeout=CLICK_TIMEOUT_MS)
                    await page.wait_for_timeout(SETTLE_MS)
                    return text[:60]
    return None


async def _covering(page: Page) -> list[str]:
    """Overlays that actually cover part of the fold.

    A hidden consent template is in the DOM of half the web; only one that is painted
    over the page matters.
    """
    found: list[str] = []
    for selector in DIALOG_SELECTORS:
        with contextlib.suppress(PlaywrightError, PlaywrightTimeout):
            covers = await page.evaluate(
                """(selector) => {
                  const height = window.innerHeight, width = window.innerWidth;
                  return Array.from(document.querySelectorAll(selector)).some((el) => {
                    const style = getComputedStyle(el);
                    if (style.display === 'none' || style.visibility === 'hidden') return false;
                    if (parseFloat(style.opacity) === 0) return false;
                    const r = el.getBoundingClientRect();
                    if (r.width < 80 || r.height < 40) return false;
                    if (r.bottom < 0 || r.top > height) return false;
                    if (r.right < 0 || r.left > width) return false;
                    // Fixed and sticky overlays are the ones that follow you down the page.
                    return style.position === 'fixed' || style.position === 'sticky'
                        || r.width * r.height > width * height * 0.12;
                  });
                }""",
                selector,
            )
            if covers:
                found.append(selector)
    return found


async def dismiss(page: Page, selectors: list[str] | None = None) -> ConsentResult:
    """Close what is in the way, preferring to decline. Runs before the first screenshot."""
    result = ConsentResult()

    for selector in selectors or []:
        if await _click(page, selector):
            result.dismissed.append(Dismissal(selector=selector, label="project selector"))

    for _ in range(MAX_DISMISSALS):
        covering = await _covering(page)
        if not covering:
            break

        for known in KNOWN_SELECTORS:
            if await _click(page, known):
                accepted = any(word in known.lower() for word in ("accept", "allow", "agree"))
                result.dismissed.append(Dismissal(selector=known, accepted=accepted))
                break
        else:
            scope = covering[0]
            label = await _click_by_text(page, scope, DECLINE_TEXT)
            accepted = False
            if label is None:
                label = await _click_by_text(page, scope, CLOSE_TEXT)
            if label is None:
                # Last resort. Recorded, because "we accepted cookies on your client's
                # site" is something the person reading the report should know.
                label = await _click_by_text(page, scope, ACCEPT_TEXT)
                accepted = label is not None
            if label is None:
                break
            result.dismissed.append(Dismissal(selector=scope, label=label, accepted=accepted))

    result.remaining = await _covering(page)
    return result
