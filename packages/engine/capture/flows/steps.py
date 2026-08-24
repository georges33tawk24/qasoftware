"""The step-logging wrapper — SPEC §12.3.

Every action goes through this. Reproduction steps are never written by hand; they fall
out of the log, and that is the whole point. A flow that reaches for `flow.page` directly
is a flow whose failure will arrive with a gap in its instructions.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypeVar

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as PlaywrightTimeout

from engine.artifact.models import FlowFailure, Step, StepStatus
from engine.capture.secrets import Redactor

T = TypeVar("T")

DEFAULT_TIMEOUT = 10_000
SCREENSHOT_TIMEOUT = 5_000
_SAFE = re.compile(r"[^a-z0-9]+")


class FlowAborted(RuntimeError):
    """Raised when a step fails in a way that makes the rest of the flow meaningless."""

    def __init__(self, failure: FlowFailure) -> None:
        super().__init__(failure.message)
        self.failure = failure


class Flow:
    """A page, plus a log of everything done to it."""

    def __init__(
        self,
        page: Page,
        directory: Path,
        *,
        redactor: Redactor | None = None,
        media_prefix: str = "",
    ) -> None:
        self.page = page
        self.directory = directory
        self.redactor = redactor or Redactor()
        self.media_prefix = media_prefix
        self.steps: list[Step] = []
        self.failures: list[FlowFailure] = []
        self.data: dict[str, Any] = {}

    # ------------------------------------------------------------------ logging

    async def step(self, description: str, fn: Callable[[], Awaitable[T]]) -> T:
        """Log, screenshot, then act. The screenshot is taken *before* the action so a
        failure shows the state the action was attempted from."""
        number = len(self.steps) + 1
        started = datetime.now(UTC)
        record = Step(
            n=number,
            text=self.redactor.text(description) or description,
            ts=started,
            url=self.redactor.url(self.page.url),
            screenshot=await self._screenshot(number),
        )
        self.steps.append(record)
        try:
            result = await fn()
        except (PlaywrightError, AssertionError) as exc:
            record.status = StepStatus.failed
            record.error = (self.redactor.text(str(exc).splitlines()[0]) or "")[:300]
            record.durationMs = _elapsed(started)
            raise
        record.durationMs = _elapsed(started)
        return result

    async def note(self, description: str) -> None:
        """A step that observes rather than acts — still numbered, still screenshotted."""
        await self.step(description, _noop)

    def fail(
        self,
        kind: str,
        message: str,
        *,
        expected: str | None = None,
        actual: str | None = None,
        abort: bool = False,
        **data: Any,
    ) -> None:
        failure = FlowFailure(
            kind=kind,
            message=message,
            step=len(self.steps) or None,
            expected=expected,
            actual=actual,
            data=data,
        )
        self.failures.append(failure)
        if abort:
            raise FlowAborted(failure)

    # ------------------------------------------------------------------ actions

    async def goto(self, url: str, *, wait: str = "domcontentloaded") -> None:
        await self.step(
            f"Open {self.redactor.url(url)}",
            lambda: self.page.goto(url, wait_until=wait, timeout=DEFAULT_TIMEOUT),  # type: ignore[arg-type]
        )

    async def click(self, selector: str, *, described: str | None = None) -> None:
        await self.step(
            described or f"Click {selector}",
            lambda: self.page.click(selector, timeout=DEFAULT_TIMEOUT),
        )

    async def fill(self, selector: str, value: str, *, described: str | None = None) -> None:
        """The description never contains the value unless the caller says so — a filled
        password must not end up in a step list."""
        await self.step(
            described or f"Type into {selector}",
            lambda: self.page.fill(selector, value, timeout=DEFAULT_TIMEOUT),
        )

    async def press(self, selector: str, key: str) -> None:
        await self.step(
            f"Press {key} in {selector}",
            lambda: self.page.press(selector, key, timeout=DEFAULT_TIMEOUT),
        )

    async def submit(self, selector: str, *, described: str | None = None) -> None:
        await self.step(
            described or "Submit the form",
            lambda: self.page.click(selector, timeout=DEFAULT_TIMEOUT),
        )

    async def reload(self) -> None:
        await self.step("Reload the page", lambda: self.page.reload(wait_until="domcontentloaded"))

    async def back(self) -> None:
        await self.step("Go back", lambda: self.page.go_back(wait_until="domcontentloaded"))

    async def settle(self, ms: int = 400) -> None:
        await self.step("Wait for the page to settle", lambda: self.page.wait_for_timeout(ms))

    # ----------------------------------------------------------------- questions

    async def visible(self, selector: str, *, timeout: int = 2000) -> bool:
        try:
            await self.page.wait_for_selector(selector, state="visible", timeout=timeout)
        except PlaywrightTimeout:
            return False
        return True

    async def text_of(self, selector: str) -> str:
        try:
            return (await self.page.inner_text(selector, timeout=2000)).strip()
        except PlaywrightError:
            return ""

    async def body_text(self) -> str:
        try:
            return await self.page.inner_text("body", timeout=2000)
        except PlaywrightError:
            return ""

    async def expect_visible(self, selector: str, description: str, **fail: Any) -> bool:
        await self.note(f"Expect {description}")
        if await self.visible(selector):
            return True
        self.fail(
            fail.pop("kind", "expectation-failed"),
            f"{description} did not appear",
            expected=description,
            actual=f"{selector} is not visible",
            **fail,
        )
        return False

    async def expect_text(self, needle: str, description: str, **fail: Any) -> bool:
        await self.note(f"Expect {description}")
        if needle.casefold() in (await self.body_text()).casefold():
            return True
        self.fail(
            fail.pop("kind", "expectation-failed"),
            f"{description} was not on the page",
            expected=needle,
            actual="not present",
            **fail,
        )
        return False

    # ------------------------------------------------------------------ private

    async def _screenshot(self, number: int) -> str | None:
        name = f"step_{number:02d}.png"
        try:
            await self.page.screenshot(path=str(self.directory / name), timeout=SCREENSHOT_TIMEOUT)
        except PlaywrightError:
            return None
        return f"{self.media_prefix}{name}" if self.media_prefix else name


async def _noop() -> None:
    return None


def _elapsed(started: datetime) -> float:
    return round((datetime.now(UTC) - started).total_seconds() * 1000, 1)


def flow_id(name: str, persona: str) -> str:
    slug = _SAFE.sub("-", f"{persona}-{name}".casefold()).strip("-")
    return slug[:60] or "flow"
