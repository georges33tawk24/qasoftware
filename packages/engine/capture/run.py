"""The capture run — SPEC §3 stage 1, §4, §5.

Capture once, check many. This produces the run artifact and nothing else: no issue is
ever created here. Stages 1-3 produce data; only 4-7 produce issues.
"""

from __future__ import annotations

import contextlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha1
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import BrowserContext, Page, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeout

from engine import branding
from engine.artifact import store
from engine.artifact.models import (
    ElementRecord,
    Metrics,
    NetworkEntry,
    PageArtifact,
    PageRecord,
    PageSecurity,
    ProbeReport,
    RunConfig,
    RunManifest,
    RunStatus,
    Viewport,
)
from engine.artifact.store import RunPaths
from engine.capture import axe, cdp, challenge, consent, probe, snapshot, stability
from engine.capture.auth import ANONYMOUS, Persona, ensure_authenticated
from engine.capture.crawler import (
    Frontier,
    Robots,
    Target,
    UrlPolicy,
    links_on,
    normalise,
    sitemap_urls,
)
from engine.capture.driver import ContextOptions, get_driver
from engine.capture.layout import derive
from engine.capture.recorder import PageRecorder
from engine.capture.secrets import Redactor

ROBOTS_AGENT = f"{branding.PRODUCT_NAME}Bot"
_SLUG = re.compile(r"[^a-z0-9]+")


@dataclass
class CaptureResult:
    paths: RunPaths
    manifest: RunManifest
    problems: list[str] = field(default_factory=list)
    blocked: int = 0


def page_id(url: str, persona: str) -> str:
    """Deterministic and readable: the same page in two runs gets the same directory."""
    slug = _SLUG.sub("_", urlsplit(url).path.strip("/").lower()).strip("_")[:24] or "home"
    digest = sha1(f"{persona}|{url}".encode()).hexdigest()[:6]
    prefix = "" if persona == ANONYMOUS.name else f"{persona}_"
    return f"p_{prefix}{slug}_{digest}"


def checkers_sha() -> str | None:
    """The git sha of the checker suite, so a run says what code produced it."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[3],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    # `git rev-parse HEAD` echoes "HEAD" to stdout on failure, so the exit code is the
    # only honest signal here.
    return result.stdout.strip() if result.returncode == 0 else None


def redirect_chain(response: Response) -> list[str]:
    chain: list[str] = []
    request = response.request
    while (previous := request.redirected_from) is not None:
        chain.append(previous.url)
        request = previous
    return list(reversed(chain))


def attach_image_bytes(elements: list[ElementRecord], network: list[NetworkEntry]) -> None:
    """Transfer size and format per image, joined from the network capture.

    Group G's per-asset budget is arithmetic over these two numbers; the checker must not
    have to go and fetch anything.
    """
    by_url: dict[str, NetworkEntry] = {}
    for entry in network:
        # A memory-cache hit is reported with a zero transfer size. Take the request that
        # actually crossed the wire, or a per-asset byte budget reads every image as free.
        best = by_url.get(entry.url)
        if best is None or entry.size.transferBytes > best.size.transferBytes:
            by_url[entry.url] = entry

    for element in elements:
        if element.image is None:
            continue
        served = by_url.get(element.image.src)
        if served is None:
            continue
        element.image.bytes = served.size.transferBytes
        content_type = served.resHeaders.get("content-type", "")
        if content_type.startswith("image/"):
            element.image.format = content_type.split("/", 1)[1].split(";")[0]


def _probeable(href: str) -> str | None:
    """An absolute http(s) URL with the fragment dropped, or None."""
    parts = urlsplit(href)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    return urlunsplit((parts.scheme, parts.netloc, parts.path or "/", parts.query, ""))


@dataclass
class _PageOutcome:
    artifact: PageArtifact
    links: list[str]
    duplicate: bool = False
    notes: list[str] = field(default_factory=list)
    """What the capture had to do to this page — an overlay dismissed, one still up.
    Surfaced as a run problem so a report can say the page was measured with a banner on
    it rather than quietly measuring the banner."""

    """The URL redirected somewhere already captured. Keeping it would report every
    finding on the destination twice and invent a duplicate-title issue."""


async def _open(context: BrowserContext, viewport: Viewport) -> Page:
    page = await context.new_page()
    await page.set_viewport_size({"width": viewport.width, "height": viewport.height})
    return page


async def _sample_vitals(page: Page, config: RunConfig, *, url: str) -> Metrics:
    """Load the page a few more times and take the median.

    One load measures a moment, not a page. Between two runs of an unchanged site LCP
    moved 850ms here and CLS moved 0.09 — enough to carry findings back and forth across
    their budgets and make a liar of SPEC §20. The extra loads are the price; the dial is
    `vitalsSamples`, and 1 buys the old behaviour back.
    """
    samples = [await snapshot.read_vitals(page)]
    for _ in range(max(0, config.vitalsSamples - 1)):
        try:
            await page.goto(url, wait_until="load", timeout=config.pageTimeoutMs)
            await stability.settle(page, timeout_ms=config.pageTimeoutMs, settle_ms=config.settleMs)
        except PlaywrightError:
            break  # one bad reload is not worth losing the samples already taken
        samples.append(await snapshot.read_vitals(page))
    return snapshot.summarise_vitals(samples)


async def _capture_page(
    target: Target,
    persona: Persona,
    contexts: dict[str, BrowserContext],
    config: RunConfig,
    paths: RunPaths,
    redactor: Redactor,
    captured: set[str],
) -> _PageOutcome:
    pid = page_id(target.url, persona.name)
    viewports = sorted(config.viewports, key=lambda v: -v.width)
    primary = viewports[0]

    record = PageRecord(
        id=pid,
        url=target.url,
        path=urlsplit(target.url).path or "/",
        status=0,
        depth=target.depth,
        discoveredFrom=target.discovered_from,
        persona=persona.name,
    )
    artifact = PageArtifact(page=record)
    links: list[str] = []
    notes: list[str] = []

    for viewport in viewports:
        is_primary = viewport.name == primary.name
        page = await _open(contexts[viewport.name], viewport)
        recorder = PageRecorder(redactor)
        session = None
        try:
            session = await cdp.open_session(page)
            if session is not None:
                await cdp.disable_cache(session)
            if is_primary:
                recorder.attach(page)
                if session is not None:
                    await cdp.start_coverage(session)

            try:
                response = await page.goto(
                    target.url, wait_until="domcontentloaded", timeout=config.pageTimeoutMs
                )
            except PlaywrightTimeout:
                record.crawlBlocked = f"navigation timed out after {config.pageTimeoutMs}ms"
                return _PageOutcome(artifact, [])

            if is_primary:
                record.status = response.status if response else 0
                record.redirectChain = redirect_chain(response) if response else []
                # The page is where it ended up, not where we asked for.
                final = page.url
                if normalise(final) in captured:
                    return _PageOutcome(artifact, [], duplicate=True)
                record.url = final
                record.path = urlsplit(final).path or "/"
                headers = await response.all_headers() if response else {}
                reason = await challenge.detect(page, record.status, headers)
                if reason:
                    record.crawlBlocked = reason
                    return _PageOutcome(artifact, [])
                record.title = await page.title()
                details = await response.security_details() if response else None
                if details:
                    record.security = PageSecurity(
                        protocol=details.get("protocol"),
                        issuer=details.get("issuer"),
                        subjectName=details.get("subjectName"),
                        validFrom=details.get("validFrom"),
                        validTo=details.get("validTo"),
                    )

            # Before settle, and long before the first screenshot: a banner covering the
            # fold poisons every measurement taken after it (SPEC §5).
            overlays = await consent.dismiss(page, config.consentSelectors)
            notes.extend(f"{record.path} @ {viewport.name}: {n}" for n in overlays.notes())

            await stability.settle(page, timeout_ms=config.pageTimeoutMs, settle_ms=config.settleMs)

            elements = await snapshot.capture_elements(page, max_elements=config.maxElements)
            await snapshot.capture_occlusion(page, elements)
            artifact.elements[viewport.name] = elements
            artifact.layout[viewport.name] = derive(pid, viewport.name, elements)

            if is_primary:
                links = await links_on(page)
                record.domNodeCount = await page.evaluate(
                    "() => document.querySelectorAll('*').length"
                )
                artifact.dom = await page.content()  # before freeze, so the DOM stays honest

            await stability.freeze(page)
            await snapshot.capture_screenshots(
                page,
                full=paths.full_png(pid, viewport.name),
                fold=paths.fold_png(pid, viewport.name),
                mask=stability.masks(page, config.maskSelectors),
            )

            if is_primary:
                # After the screenshots: a full-page capture can pull in lazy assets, and
                # a network log that stops early under-reports page weight.
                artifact.vitals = await _sample_vitals(page, config, url=record.url)
                if session is not None:
                    # Before axe: injecting its bundle would add 580KB of unused
                    # JavaScript to the page's own coverage numbers.
                    artifact.coverage = await cdp.stop_coverage(session)
                    artifact.a11y = await cdp.accessibility_tree(session)
                artifact.axe = await axe.run(page)
                await recorder.drain()
                artifact.console = recorder.console
                artifact.network = recorder.network
        finally:
            await page.close()

    for elements in artifact.elements.values():
        attach_image_bytes(elements, artifact.network)
    return _PageOutcome(artifact, links, notes=notes)


async def capture(
    url: str,
    out_dir: Path,
    *,
    config: RunConfig | None = None,
    personas: list[Persona] | None = None,
    project_id: str | None = None,
    on_page: Callable[[PageRecord], None] | None = None,
) -> CaptureResult:
    config = config or RunConfig()
    personas = personas or [ANONYMOUS]
    started = datetime.now(UTC)
    run_id = f"run_{started:%Y%m%dT%H%M%S_%f}"
    paths = RunPaths(Path(out_dir) / run_id)
    redactor = Redactor()

    manifest = RunManifest(
        runId=run_id,
        target=url,
        status=RunStatus.running,
        startedAt=started,
        checkersSha=checkers_sha(),
        projectId=project_id,
        config=config,
    )

    driver = get_driver(config.driver)
    await driver.launch()
    visited = 0
    blocked = 0
    overlay_notes: list[str] = []
    captured: set[str] = set()
    discovered: dict[str, set[str]] = {}
    probes = ProbeReport()
    try:
        for persona in personas:
            contexts = {
                viewport.name: await driver.new_context(
                    ContextOptions(
                        viewport=viewport,
                        persona=persona,
                        locale=config.locale,
                        timezone_id=config.timezone,
                        colour_scheme=config.colourScheme,
                    ),
                    redactor,
                )
                for viewport in config.viewports
            }
            primary = max(config.viewports, key=lambda v: v.width)

            auth_page = await _open(contexts[primary.name], primary)
            try:
                await ensure_authenticated(auth_page, persona, redactor)
            finally:
                await auth_page.close()

            request = contexts[primary.name].request
            robots = await Robots.load(request, url, ROBOTS_AGENT) if config.respectRobots else None
            policy = UrlPolicy(
                url,
                include=config.include,
                exclude=config.exclude,
                same_origin=config.sameOriginOnly,
                ignore_query_params=config.ignoreQueryParams,
                robots=robots,
            )
            frontier = Frontier(
                policy,
                max_depth=config.maxDepth,
                max_pages=config.maxPages,
                template_sample=config.templateSample,
            )
            frontier.push(url, 0, None)
            for found in await sitemap_urls(request, url):
                frontier.push(found, 1, "sitemap.xml")

            while (target := frontier.pop()) is not None:
                outcome = await _capture_page(
                    target, persona, contexts, config, paths, redactor, captured
                )
                if outcome.duplicate:
                    continue
                visited += 1
                if outcome.artifact.page.crawlBlocked:
                    blocked += 1
                captured.add(normalise(outcome.artifact.page.url))
                overlay_notes.extend(outcome.notes)
                store.write_page(paths, outcome.artifact)
                manifest.pageIds.append(outcome.artifact.page.id)
                if on_page is not None:
                    on_page(outcome.artifact.page)
                challenge.abort_if_mostly_blocked(blocked, visited)
                for href in outcome.links:
                    frontier.push(href, target.depth + 1, target.url)
                    link = _probeable(href)
                    if link and len(discovered) < config.maxLinkProbes:
                        discovered.setdefault(link, set()).add(outcome.artifact.page.id)

            probes = await probe.run(request, url, discovered)

            for context in dict.fromkeys(contexts.values()):
                with contextlib.suppress(PlaywrightError):
                    await context.close()
    finally:
        await driver.close()

    finished = datetime.now(UTC)
    manifest.status = RunStatus.complete
    manifest.finishedAt = finished
    manifest.durationMs = int((finished - started).total_seconds() * 1000)
    store.write_run_manifest(paths, manifest)
    store.write_probes(paths, probes)

    return CaptureResult(
        paths=paths,
        manifest=manifest,
        problems=[*overlay_notes, *store.validate(paths.root)],
        blocked=blocked,
    )
