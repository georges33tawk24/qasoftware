"""CDP-only captures: coverage and the accessibility tree (SPEC §4).

Playwright Python exposes neither, and both are needed by checkers that may not open a
browser themselves. Chromium-only; other drivers simply produce an artifact without
these capabilities and the dependent checkers self-skip.
"""

from __future__ import annotations

from typing import Any

from playwright.async_api import CDPSession, Page
from playwright.async_api import Error as PlaywrightError


async def open_session(page: Page) -> CDPSession | None:
    try:
        return await page.context.new_cdp_session(page)
    except PlaywrightError:
        return None  # not a Chromium-based driver


async def disable_cache(session: CDPSession) -> None:
    """A cache hit is reported with a zero transfer size, so the second page of a crawl
    would look weightless. Page weight is one of the things this tool measures, so the
    cache goes. Response headers are still captured, so the cache-header checks are
    unaffected."""
    await session.send("Network.enable")
    await session.send("Network.setCacheDisabled", {"cacheDisabled": True})


async def start_coverage(session: CDPSession) -> None:
    await session.send("Profiler.enable")
    await session.send("Profiler.startPreciseCoverage", {"callCount": False, "detailed": True})
    await session.send("DOM.enable")
    await session.send("CSS.enable")
    await session.send("CSS.startRuleUsageTracking")


def _used_bytes(ranges: list[dict[str, Any]]) -> tuple[int, int]:
    """V8 ranges nest: an inner uncovered range overrides its covered parent.

    Applying outermost-first to a byte map is the whole algorithm.
    """
    if not ranges:
        return 0, 0
    total = max(r["endOffset"] for r in ranges)
    covered = bytearray(total)
    for span in sorted(ranges, key=lambda r: (r["startOffset"], -r["endOffset"])):
        value = 1 if span["count"] > 0 else 0
        covered[span["startOffset"] : span["endOffset"]] = bytes([value]) * (
            span["endOffset"] - span["startOffset"]
        )
    return sum(covered), total


async def stop_coverage(session: CDPSession) -> dict[str, Any]:
    js_result = await session.send("Profiler.takePreciseCoverage")
    await session.send("Profiler.stopPreciseCoverage")
    css_result = await session.send("CSS.stopRuleUsageTracking")

    js_used = js_total = 0
    js_by_url: dict[str, dict[str, int]] = {}
    for script in js_result.get("result", []):
        url = str(script.get("url") or "")
        # The profiler reports every script in the isolate, which includes the driver's
        # own instrumentation. Only scripts the page actually fetched are the page's.
        if not url.startswith(("http://", "https://")):
            continue
        ranges = [r for fn in script.get("functions", []) for r in fn.get("ranges", [])]
        used, total = _used_bytes(ranges)
        if not total:
            continue
        js_used += used
        js_total += total
        entry = js_by_url.setdefault(url, {"usedBytes": 0, "totalBytes": 0})
        entry["usedBytes"] += used
        entry["totalBytes"] += total

    css_used = css_total = 0
    for rule in css_result.get("ruleUsage", []):
        span = int(rule["endOffset"] - rule["startOffset"])
        css_total += span
        if rule["used"]:
            css_used += span

    return {
        "js": {"usedBytes": js_used, "totalBytes": js_total, "byUrl": js_by_url},
        "css": {"usedBytes": css_used, "totalBytes": css_total},
    }


async def accessibility_tree(session: CDPSession) -> dict[str, Any]:
    await session.send("Accessibility.enable")
    tree: dict[str, Any] = await session.send("Accessibility.getFullAXTree")
    return tree
