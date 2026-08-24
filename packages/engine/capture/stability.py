"""The stability harness — SPEC §5.

This decides whether the whole product is trustworthy. Everything here exists to make
two captures of an unchanged page produce the same bytes.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

_FREEZE_JS = (Path(__file__).parent / "stability.js").read_text()

_DECODE_IMAGES = """
() => Promise.all(
  Array.from(document.images)
    .filter((img) => !img.complete)
    .map((img) => img.decode().catch(() => undefined)),
).then(() => undefined)
"""

_ANIMATIONS_RUNNING = """
() => document.getAnimations
  ? document.getAnimations().filter((a) => a.playState === 'running').length
  : 0
"""


async def wait_for_network_idle(page: Page, timeout_ms: int) -> None:
    """Analytics beacons and open sockets mean some pages never go idle. Falling back to
    `load` is better than failing the page."""
    try:
        await page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except PlaywrightTimeout:
        await page.wait_for_load_state("load", timeout=timeout_ms)


async def scroll_through(page: Page, step_ratio: float = 0.8) -> None:
    """Scroll the whole page to trigger lazy loading, then return to the top."""
    height = await page.evaluate("() => document.body.scrollHeight")
    viewport = page.viewport_size or {"height": 900}
    step = max(1, int(viewport["height"] * step_ratio))
    for offset in range(0, int(height), step):
        await page.evaluate("(y) => window.scrollTo(0, y)", offset)
        await page.wait_for_timeout(80)
    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(120)


async def wait_for_animations(page: Page, timeout_ms: int = 3000) -> None:
    waited = 0
    while waited < timeout_ms:
        if not await page.evaluate(_ANIMATIONS_RUNNING):
            return
        await page.wait_for_timeout(100)
        waited += 100


async def settle(page: Page, *, timeout_ms: int = 30_000, settle_ms: int = 300) -> None:
    """Everything that must be true before a snapshot is taken."""
    await wait_for_network_idle(page, timeout_ms)
    with contextlib.suppress(PlaywrightError):
        # No font loading API in this context is not a reason to fail the page.
        await page.evaluate("() => document.fonts.ready.then(() => undefined)")
    await scroll_through(page)
    await wait_for_network_idle(page, timeout_ms)
    await page.evaluate(_DECODE_IMAGES)
    await wait_for_animations(page)
    await page.wait_for_timeout(settle_ms)


async def freeze(page: Page) -> None:
    """Injected for screenshots only, so the captured DOM stays honest."""
    await page.evaluate(_FREEZE_JS)


def masks(page: Page, selectors: list[str]) -> list[Locator]:
    """Per-project volatile regions — clocks, carousels, ad slots (SPEC §5)."""
    return [page.locator(selector) for selector in selectors]
