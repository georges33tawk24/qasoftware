from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterator
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from playwright.async_api import async_playwright

from engine.artifact.models import Box, ElementRecord, ElementStyles

MakeElement = Callable[..., ElementRecord]


def styles(**over: Any) -> ElementStyles:
    base: dict[str, Any] = {
        "color": "rgb(17, 17, 17)",
        "backgroundColor": "rgba(0, 0, 0, 0)",
        "fontFamily": "Inter",
        "fontSize": 16.0,
        "fontWeight": 400,
        "lineHeight": 24.0,
    }
    base.update(over)
    return ElementStyles(**base)


@pytest.fixture
def make_element() -> MakeElement:
    """Build an ElementRecord with sensible defaults; override what the test cares about."""

    def _make(**over: Any) -> ElementRecord:
        fields: dict[str, Any] = {
            "id": "el_0001",
            "stableKey": "",
            "selector": "body > main > div.card:nth-child(3)",
            "tag": "div",
            "role": "listitem",
            "text": "Latest news",
            "textFull": "Latest news",
            "box": Box(x=240, y=1180, w=320, h=410),
            "boxViewport": Box(x=240, y=180, w=320, h=410),
            "styles": styles(),
            "resolvedBackground": "rgb(255, 255, 255)",
            "nearestHeading": "Latest news",
        }
        style_over = over.pop("styles", None)
        fields.update(over)
        if style_over is not None:
            fields["styles"] = style_over
        return ElementRecord(**fields)

    return _make


SITE_ROOT = Path(__file__).parent / "fixtures" / "site"


class _QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return


@pytest.fixture(scope="session")
def site_url() -> Iterator[str]:
    """The fixture site over real HTTP. Never the live internet."""
    handler = partial(_QuietHandler, directory=str(SITE_ROOT))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(scope="session")
def browser_ready() -> None:
    """Skip browser tests cleanly when chromium is not installed (`make browsers`)."""

    async def check() -> None:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            await browser.close()

    try:
        asyncio.run(check())
    except Exception as exc:
        pytest.skip(f"chromium unavailable: {exc}")


@pytest.fixture(scope="session")
def broken_site_url() -> Iterator[str]:
    """The same site, behind the fixture server that plants the server-side defects."""
    from tests.serve_broken import serve

    server, url = serve(SITE_ROOT)
    try:
        yield url
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture(autouse=True)
def _no_proxy_on_loopback() -> Iterator[None]:
    """Nothing in this suite talks to anything but 127.0.0.1.

    `urlopen` honours the machine's proxy settings, which is right for a real Jira behind
    a corporate proxy and wrong for a fixture server on loopback — where it fails with a
    DNS error that looks nothing like the cause.
    """
    import urllib.request

    urllib.request.install_opener(urllib.request.build_opener(urllib.request.ProxyHandler({})))
    try:
        yield
    finally:
        urllib.request.install_opener(urllib.request.build_opener())
