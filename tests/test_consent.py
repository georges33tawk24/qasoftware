"""Consent overlays — SPEC §5.

The banner has to be gone before the first screenshot, and *how* it went matters: we
decline on somebody else's client's site unless nothing else will close the thing.
"""

from __future__ import annotations

import asyncio

import pytest
from playwright.async_api import async_playwright

from engine.capture.consent import ConsentResult, dismiss

pytestmark = pytest.mark.browser

BANNER = """
<body style="margin:0;height:2000px">
  <main><h1>The page underneath</h1></main>
  <div id="cmp" role="dialog"
       style="position:fixed;bottom:0;left:0;width:100%;height:140px;background:#eee">
    <p>We value your privacy.</p>
    {buttons}
  </div>
</body>
"""
GONE = "document.getElementById('cmp').remove()"


def run(buttons: str, selectors: list[str] | None = None) -> ConsentResult:
    async def go() -> ConsentResult:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            await page.set_content(BANNER.format(buttons=buttons))
            try:
                return await dismiss(page, selectors)
            finally:
                await browser.close()

    return asyncio.run(go())


def test_declining_is_preferred_over_accepting(browser_ready: None) -> None:
    result = run(
        f'<button onclick="{GONE}">Accept all</button><button onclick="{GONE}">Reject all</button>'
    )
    assert result.remaining == []
    assert [d.accepted for d in result.dismissed] == [False]
    assert "reject all" in result.dismissed[0].label


def test_accepting_is_the_last_resort_and_is_recorded(browser_ready: None) -> None:
    result = run(f'<button onclick="{GONE}">Accept all</button>')
    assert result.remaining == []
    assert [d.accepted for d in result.dismissed] == [True]
    assert "by accepting" in result.notes()[0]


def test_a_project_selector_is_tried_first(browser_ready: None) -> None:
    result = run(f'<button id="mine" onclick="{GONE}">Accept all</button>', selectors=["#mine"])
    assert [d.label for d in result.dismissed] == ["project selector"]
    assert result.remaining == []


def test_an_overlay_that_will_not_close_is_reported_not_ignored(browser_ready: None) -> None:
    """Measured with the banner up beats pretending it was not there."""
    result = run("<button>Manage preferences</button>")
    assert result.dismissed == []
    assert result.remaining
    assert "still covering the page" in result.notes()[0]
