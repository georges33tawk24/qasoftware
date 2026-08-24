"""HTTP probes run at capture time — SPEC §8.4 A.

Checkers never touch the network, so anything that needs a request happens here and
lands in `probes.json`. Link checking, not-found handling, and a light well-known-path
probe are all in this file for exactly that reason.
"""

from __future__ import annotations

import asyncio
from urllib.parse import urlsplit, urlunsplit

from playwright.async_api import APIRequestContext
from playwright.async_api import Error as PlaywrightError

from engine.artifact.models import LinkProbe, PathProbe, ProbeReport

EXPOSED_PATHS = (
    "/.env",
    "/.git/config",
    "/.DS_Store",
    "/server-status",
    "/debug",
)
"""A light probe, not a scan. These are the ones that are catastrophic when public and
cost one request each to rule out."""

NOT_FOUND_PROBE = "/bureau-not-found-probe"
"""Deterministic, so two runs agree. A 200 here means the site's 404 handling is broken,
which quietly ruins every other broken-link check."""

PROBE_TIMEOUT_MS = 10_000
PROBE_CONCURRENCY = 8
BODY_SAMPLE = 300


async def _status(request: APIRequestContext, url: str) -> tuple[int, str | None]:
    """HEAD first; a good few servers answer 405 to HEAD and 200 to GET."""
    try:
        response = await request.head(url, timeout=PROBE_TIMEOUT_MS, max_redirects=5)
        if response.status in (405, 501):
            response = await request.get(url, timeout=PROBE_TIMEOUT_MS, max_redirects=5)
        return response.status, None
    except PlaywrightError as exc:
        return 0, exc.message.splitlines()[0][:200]


async def probe_links(
    request: APIRequestContext, links: dict[str, set[str]], origin: str
) -> list[LinkProbe]:
    """Resolve every discovered href once per run, not once per page."""
    semaphore = asyncio.Semaphore(PROBE_CONCURRENCY)

    async def one(url: str) -> LinkProbe:
        async with semaphore:
            status, error = await _status(request, url)
        return LinkProbe(
            url=url,
            status=status,
            internal=urlsplit(url).netloc.lower() == origin,
            error=error,
            foundOn=sorted(links[url]),
        )

    return sorted(await asyncio.gather(*(one(url) for url in sorted(links))), key=lambda p: p.url)


async def probe_paths(request: APIRequestContext, seed: str) -> list[PathProbe]:
    parts = urlsplit(seed)

    def absolute(path: str) -> str:
        return urlunsplit((parts.scheme, parts.netloc, path, "", ""))

    results: list[PathProbe] = []

    status, _ = await _status(request, absolute(NOT_FOUND_PROBE))
    results.append(PathProbe(path=NOT_FOUND_PROBE, status=status, kind="not-found-handling"))

    for path in EXPOSED_PATHS:
        url = absolute(path)
        status, _ = await _status(request, url)
        sample: str | None = None
        if status == 200:
            try:
                response = await request.get(url, timeout=PROBE_TIMEOUT_MS)
                sample = (await response.text())[:BODY_SAMPLE]
            except PlaywrightError:
                sample = None
        results.append(PathProbe(path=path, status=status, kind="exposed-path", bodySample=sample))
    return results


async def run(request: APIRequestContext, seed: str, links: dict[str, set[str]]) -> ProbeReport:
    origin = urlsplit(seed).netloc.lower()
    return ProbeReport(
        links=await probe_links(request, links, origin),
        paths=await probe_paths(request, seed),
    )
