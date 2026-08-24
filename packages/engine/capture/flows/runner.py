"""Flow execution — SPEC §5 (retry), §8.4 H, §12.3.

Each flow gets its own context so it can carry a video, its own trace, and up to three
attempts. A flow that passes on a retry produces no finding at all: roughly half of flaky
findings vanish on the second attempt, and nothing destroys trust faster than a report
that cries wolf.
"""

from __future__ import annotations

import shutil
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from playwright.async_api import BrowserContext
from playwright.async_api import Error as PlaywrightError

from engine.artifact.models import FlowRecord, FlowStatus, RunConfig, Viewport
from engine.artifact.store import RunPaths
from engine.capture.auth import Persona
from engine.capture.driver import BrowserDriver, ContextOptions
from engine.capture.flows.steps import Flow, FlowAborted, flow_id
from engine.capture.secrets import Redactor

FlowBody = Callable[[Flow], Awaitable[None]]


@dataclass
class FlowSpec:
    """A named journey. `applies` keeps a flow from running where it makes no sense."""

    name: str
    kind: str
    body: FlowBody
    page_id: str | None = None
    data: dict[str, object] = field(default_factory=dict)


@dataclass
class FlowRunner:
    driver: BrowserDriver
    paths: RunPaths
    config: RunConfig
    persona: Persona
    viewport: Viewport
    redactor: Redactor

    async def run(self, spec: FlowSpec) -> FlowRecord:
        identifier = flow_id(spec.name, self.persona.name)
        directory = self.paths.flow_dir(identifier)
        started = datetime.now(UTC)
        attempts = self.config.flowRetries + 1
        record: FlowRecord | None = None

        for attempt in range(1, attempts + 1):
            if directory.exists():
                shutil.rmtree(directory)  # only the surviving attempt is kept
            directory.mkdir(parents=True, exist_ok=True)
            record = await self._attempt(spec, identifier, directory, started, attempt)
            if record.status is FlowStatus.passed:
                break

        assert record is not None
        record.durationMs = int((datetime.now(UTC) - started).total_seconds() * 1000)
        return record

    async def _attempt(
        self,
        spec: FlowSpec,
        identifier: str,
        directory: Path,
        started: datetime,
        attempt: int,
    ) -> FlowRecord:
        record = FlowRecord(
            id=identifier,
            name=spec.name,
            kind=spec.kind,
            persona=self.persona.name,
            pageId=spec.page_id,
            startedAt=started,
            attempts=attempt,
            data=dict(spec.data),
        )
        context = await self.driver.new_context(
            ContextOptions(
                viewport=self.viewport,
                persona=self.persona,
                locale=self.config.locale,
                timezone_id=self.config.timezone,
                extra={"record_video_dir": str(directory / "video")},
            ),
            self.redactor,
        )
        traced = await self._start_trace(context)
        page = await context.new_page()
        flow = Flow(page, directory, redactor=self.redactor)

        try:
            await spec.body(flow)
        except FlowAborted:
            pass  # the failure is already recorded; the rest of the flow is meaningless
        except PlaywrightError as exc:
            flow.fail(
                "flow-error",
                f"The flow could not continue: {str(exc).splitlines()[0]}"[:300],
            )
            record.status = FlowStatus.error
        finally:
            record.steps = flow.steps
            record.failures = flow.failures
            record.data.update(flow.data)
            record.trace = await self._stop_trace(context, directory, traced)
            await page.close()
            await context.close()
            record.video = self._collect_video(directory)

        if record.status is not FlowStatus.error:
            record.status = FlowStatus.failed if flow.failures else FlowStatus.passed
        return record

    async def _start_trace(self, context: BrowserContext) -> bool:
        try:
            await context.tracing.start(screenshots=True, snapshots=True, sources=False)
        except PlaywrightError:
            return False
        return True

    async def _stop_trace(
        self, context: BrowserContext, directory: Path, traced: bool
    ) -> str | None:
        if not traced:
            return None
        try:
            await context.tracing.stop(path=str(directory / "trace.zip"))
        except PlaywrightError:
            return None
        return "trace.zip"

    def _collect_video(self, directory: Path) -> str | None:
        """Playwright names the file itself and only finalises it on context close."""
        videos = sorted((directory / "video").glob("*.webm"))
        if not videos:
            return None
        target = directory / "video.webm"
        shutil.move(str(videos[0]), target)
        shutil.rmtree(directory / "video", ignore_errors=True)
        return "video.webm"


async def run_flows(runner: FlowRunner, specs: list[FlowSpec]) -> list[FlowRecord]:
    """Sequential on purpose: flows share a session and a rate limit, and running them at
    once produces failures that belong to the runner rather than the site."""
    records = []
    for spec in specs:
        records.append(await runner.run(spec))
    return records
