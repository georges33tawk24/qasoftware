"""Bot-challenge detection — SPEC §5, step 4 of the order of operations.

This runs before anything else touches a page. A challenge page that reaches the
checkers produces a "blank page" bug, a missing-heading bug, a contrast bug and a dozen
more, all of them lies. A report you cannot trust is worse than no report.
"""

from __future__ import annotations

from playwright.async_api import Page

_TEXT_MARKERS = (
    "checking your browser",
    "just a moment",
    "verify you are human",
    "verifying you are human",
    "attention required! | cloudflare",
    "please enable javascript and cookies to continue",
    "additional security check is required",
    "ddos protection by",
    "one more step",
    "access denied",
    "are you a robot",
)

_SELECTORS = (
    "#cf-challenge-running",
    "#challenge-running",
    "#challenge-form",
    "div.cf-browser-verification",
    'iframe[src*="challenges.cloudflare.com"]',
    'iframe[src*="hcaptcha.com"]',
    'iframe[src*="recaptcha/api2"]',
    'iframe[title*="recaptcha" i]',
    "div.g-recaptcha",
    "div.h-captcha",
    "div.cf-turnstile",
)

BLOCKING_STATUSES = frozenset({401, 403, 429, 503})


async def detect(page: Page, status: int | None, headers: dict[str, str]) -> str | None:
    """Return why a page is challenged, or None. Cheap enough to run on every page."""
    lowered = {k.lower(): v.lower() for k, v in headers.items()}
    if "cf-mitigated" in lowered:
        return f"cloudflare mitigation header: {lowered['cf-mitigated']}"
    if status in BLOCKING_STATUSES and "cloudflare" in lowered.get("server", ""):
        return f"cloudflare returned {status}"

    for selector in _SELECTORS:
        if await page.locator(selector).count():
            return f"challenge widget present: {selector}"

    title = (await page.title()).lower()
    body = (await page.evaluate("() => document.body ? document.body.innerText : ''"))[:3000]
    haystack = f"{title}\n{body}".lower()
    for marker in _TEXT_MARKERS:
        if marker in haystack:
            return f"challenge text present: {marker!r}"
    return None


class RunBlocked(RuntimeError):
    """Raised when too much of a run is challenged to be worth reporting on."""


def abort_if_mostly_blocked(blocked: int, total: int, threshold: float = 0.2) -> None:
    if total == 0 or blocked / total <= threshold:
        return
    raise RunBlocked(
        f"{blocked} of {total} pages were blocked by bot protection "
        f"({blocked / total:.0%}, threshold {threshold:.0%}).\n"
        "Ask the dev team for a WAF bypass rule on a secret header, an IP allowlist, or "
        "point the run at staging — that is the only fix that keeps working. "
        "Headed mode with a persistent profile (--driver playwright_headed) clears milder "
        "detection; a stealth driver is the last resort."
    )
