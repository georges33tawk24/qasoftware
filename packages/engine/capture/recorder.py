"""Console and network capture — SPEC §4, catalogue group A.

Attaches to a page and drains everything the browser tells us. Bodies are hashed and
sampled rather than stored whole: the artifact has to stay a reasonable size and must
never become a place secrets accumulate.
"""

from __future__ import annotations

import asyncio
import contextlib
import re
from datetime import UTC, datetime
from hashlib import sha256

from playwright.async_api import ConsoleMessage as PlaywrightConsoleMessage
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page, Request

from engine.artifact.models import ConsoleMessage, NetworkEntry, NetworkSize, NetworkTiming
from engine.capture.secrets import Redactor

BODY_SAMPLE_CHARS = 512
BODY_TYPES = frozenset({"document", "xhr", "fetch", "script", "stylesheet"})
_SOURCE_MAP = re.compile(rb"//[#@]\s*sourceMappingURL=(\S+)")


class PageRecorder:
    """One recorder per page. `drain()` waits for in-flight body reads before returning."""

    def __init__(self, redactor: Redactor) -> None:
        self.redactor = redactor
        self.console: list[ConsoleMessage] = []
        self.network: list[NetworkEntry] = []
        self._tasks: set[asyncio.Task[None]] = set()

    def attach(self, page: Page) -> None:
        page.on("console", self._on_console)
        page.on("pageerror", self._on_page_error)
        # `requestfinished`, not `response`: transfer sizes are only known once the body
        # has actually arrived, and a per-asset byte budget is one of the checks.
        page.on("requestfinished", self._on_request_finished)
        page.on("requestfailed", self._on_request_failed)

    # ---------------------------------------------------------------------- console

    def _on_console(self, message: PlaywrightConsoleMessage) -> None:
        location = message.location
        self.console.append(
            ConsoleMessage(
                level=message.type,
                text=self.redactor.text(message.text) or "",
                url=self.redactor.url(location["url"]) if location.get("url") else None,
                line=location.get("lineNumber"),
                ts=datetime.now(UTC),
            )
        )

    def _on_page_error(self, error: PlaywrightError) -> None:
        """Uncaught exceptions and unhandled promise rejections."""
        self.console.append(
            ConsoleMessage(
                level="error",
                text=self.redactor.text(error.message) or "",
                stack=self.redactor.text(error.stack),
                ts=datetime.now(UTC),
            )
        )

    # ---------------------------------------------------------------------- network

    def _spawn(self, request: Request, *, failed: bool) -> None:
        task = asyncio.create_task(self._record(request, failed=failed))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _on_request_finished(self, request: Request) -> None:
        self._spawn(request, failed=False)

    def _on_request_failed(self, request: Request) -> None:
        self._spawn(request, failed=True)

    async def _record(self, request: Request, *, failed: bool) -> None:
        try:
            response = await request.response()
            timing = request.timing
            sizes = await request.sizes()
            req_headers = await request.all_headers()
            res_headers = await response.all_headers() if response else {}
        except PlaywrightError:
            return  # the page navigated away mid-flight

        body_hash: str | None = None
        body_sample: str | None = None
        decoded_bytes: int | None = None
        source_map: str | None = None
        if response is not None and request.resource_type in BODY_TYPES:
            with contextlib.suppress(PlaywrightError, UnicodeDecodeError):
                body = await response.body()
                decoded_bytes = len(body)
                body_hash = sha256(body).hexdigest()
                if request.resource_type == "script":
                    # The comment lives at the end of the file, past any body sample.
                    found = _SOURCE_MAP.search(body[-2048:])
                    source_map = found.group(1).decode("utf-8", "replace") if found else None
                body_sample = self.redactor.text(
                    body[: BODY_SAMPLE_CHARS * 4].decode("utf-8", "replace")[:BODY_SAMPLE_CHARS]
                )

        ttfb = timing["responseStart"] - timing["requestStart"]
        self.network.append(
            NetworkEntry(
                url=self.redactor.url(request.url),
                method=request.method,
                status=response.status if response else 0,
                type=request.resource_type,
                reqHeaders=self.redactor.headers(req_headers),
                resHeaders=self.redactor.headers(res_headers),
                reqBody=self.redactor.text(request.post_data),
                resBodyHash=body_hash,
                resBodySample=body_sample,
                timing=NetworkTiming(
                    startMs=round(timing["startTime"], 2),
                    ttfbMs=round(ttfb, 2) if ttfb >= 0 else None,
                    durationMs=round(max(0.0, timing["responseEnd"] - timing["requestStart"]), 2),
                ),
                size=NetworkSize(
                    transferBytes=max(0, sizes["responseBodySize"] + sizes["responseHeadersSize"]),
                    resourceBytes=decoded_bytes,
                ),
                initiator=request.resource_type,
                sourceMapUrl=source_map,
                failure=request.failure if failed else None,
            )
        )

    async def drain(self) -> None:
        if self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)
        self.network.sort(key=lambda entry: (entry.timing.startMs, entry.url))
