"""Group G, performance — SPEC §8.4 G.

All arithmetic over what capture already measured: vitals from PerformanceObserver,
transfer sizes and timings from CDP, byte coverage from the profiler.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import PageRecord
from engine.checkers.base import checker
from engine.checkers.domdata import head_facts
from engine.checkers.support import (
    element_finding,
    live_pages,
    page_finding,
    surfaces,
    synthetic_key,
)
from engine.issues.models import Category, Finding, Severity

VITAL_BUDGETS = {
    "lcp": (2500.0, "ms", "Largest Contentful Paint"),
    "cls": (0.1, "", "Cumulative Layout Shift"),
    "tbt": (200.0, "ms", "Total Blocking Time"),
    "ttfb": (800.0, "ms", "Time to First Byte"),
    "inp": (200.0, "ms", "Interaction to Next Paint"),
}

PAGE_WEIGHT_BUDGET = 2_500_000
IMAGE_BUDGET = 300_000
"""SPEC §8.4 G's stated default."""

DOM_NODE_BUDGET = 1500
UNUSED_CSS_RATIO = 0.5
UNUSED_CSS_MIN_BYTES = 50_000
UNUSED_JS_RATIO = 0.6
UNUSED_JS_MIN_BYTES = 200_000
CACHE_MIN_SECONDS = 86_400
STATIC_TYPES = frozenset({"image", "script", "stylesheet", "font"})
LEGACY_IMAGE_FORMATS = frozenset({"jpeg", "jpg", "png"})
MODERN_FORMAT_MIN_BYTES = 50_000
MAX_BLOCKING_STYLES = 2


@checker
class WebVitals:
    id = "performance.vitals"
    category = Category.performance
    requires = frozenset({Capability.VITALS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            vitals = ctx.vitals(page.id)
            if vitals is None:
                continue
            for name, (budget, unit, label) in VITAL_BUDGETS.items():
                value = getattr(vitals, name)
                if value is None or value <= budget:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind=f"vital-{name}",
                    title=f"{label} is {value:g}{unit}",
                    description="Measured on an unthrottled connection, so a real visitor "
                    "sees this or worse.",
                    expected=f"{budget:g}{unit} or less",
                    actual=f"{value:g}{unit}",
                    stable_key=synthetic_key(self.id, name),
                    data={"metric": name, "value": value, "budget": budget},
                )


@checker
class PageWeight:
    id = "performance.page-weight"
    category = Category.performance
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            entries = ctx.network(page.id)
            total = sum(entry.size.transferBytes for entry in entries)
            if total > PAGE_WEIGHT_BUDGET:
                yield page_finding(
                    self,
                    page,
                    kind="page-weight",
                    title=f"Page weighs {total / 1_000_000:.1f}MB",
                    expected=f"under {PAGE_WEIGHT_BUDGET / 1_000_000:.1f}MB",
                    actual=f"{total / 1_000_000:.1f}MB over {len(entries)} requests",
                    stable_key=synthetic_key(self.id, "total"),
                    data={"bytes": total, "requests": len(entries)},
                )
            for entry in entries:
                if entry.type != "image" or entry.size.transferBytes <= IMAGE_BUDGET:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind="image-over-budget",
                    title=f"Image is {entry.size.transferBytes / 1000:.0f}KB",
                    expected=f"under {IMAGE_BUDGET // 1000}KB",
                    actual=f"{entry.size.transferBytes / 1000:.0f}KB",
                    stable_key=synthetic_key(self.id, entry.url),
                    data={"url": entry.url, "bytes": entry.size.transferBytes},
                )


@checker
class ImageDelivery:
    id = "performance.image-delivery"
    category = Category.performance
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            fold = surface.viewport.height or 900
            for element in surface.laid_out:
                image = element.image
                if image is None or element.tag != "img":
                    continue
                if (
                    image.format
                    and image.format.lower() in LEGACY_IMAGE_FORMATS
                    and (image.bytes or 0) > MODERN_FORMAT_MIN_BYTES
                ):
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="legacy-image-format",
                        title=f"{(image.bytes or 0) / 1000:.0f}KB {image.format.upper()} that "
                        "would be smaller as WebP or AVIF",
                        expected="webp or avif",
                        actual=image.format,
                        data={"src": image.src, "bytes": image.bytes},
                    )
                if element.box.y > fold and (image.loading or "eager") != "lazy":
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="below-fold-not-lazy",
                        title="Below-the-fold image loads eagerly",
                        description="It competes with content the visitor can actually see.",
                        expected='loading="lazy"',
                        actual=image.loading or "eager",
                        data={"src": image.src, "y": element.box.y, "fold": fold},
                    )


@checker
class Coverage:
    id = "performance.coverage"
    category = Category.performance
    requires = frozenset({Capability.COVERAGE})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            coverage = ctx.coverage(page.id)
            if not coverage:
                continue
            for kind, ratio_budget, min_bytes, label in (
                ("css", UNUSED_CSS_RATIO, UNUSED_CSS_MIN_BYTES, "CSS"),
                ("js", UNUSED_JS_RATIO, UNUSED_JS_MIN_BYTES, "JavaScript"),
            ):
                section = coverage.get(kind) or {}
                total = int(section.get("totalBytes") or 0)
                used = int(section.get("usedBytes") or 0)
                if total < min_bytes:
                    continue
                unused = 1 - (used / total)
                if unused <= ratio_budget:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind=f"unused-{kind}",
                    title=f"{unused:.0%} of the {label} on this page is never used",
                    expected=f"under {ratio_budget:.0%} unused",
                    actual=f"{unused:.0%} of {total / 1000:.0f}KB",
                    stable_key=synthetic_key(self.id, kind),
                    data={"totalBytes": total, "usedBytes": used, "unusedRatio": unused},
                )


@checker
class DomSize:
    id = "performance.dom-size"
    category = Category.performance
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            if page.domNodeCount <= DOM_NODE_BUDGET:
                continue
            yield page_finding(
                self,
                page,
                kind="dom-too-large",
                title=f"{page.domNodeCount} elements in the document",
                description="Every style recalculation walks all of them.",
                expected=f"under {DOM_NODE_BUDGET}",
                actual=str(page.domNodeCount),
                stable_key=synthetic_key(self.id, "count"),
                data={"nodes": page.domNodeCount},
            )


@checker
class CacheHeaders:
    id = "performance.cache-headers"
    category = Category.performance
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            seen: set[str] = set()
            for entry in ctx.network(page.id):
                if entry.type not in STATIC_TYPES or entry.status != 200 or entry.url in seen:
                    continue
                seen.add(entry.url)
                cache_control = entry.resHeaders.get("cache-control", "")
                if _max_age(cache_control) >= CACHE_MIN_SECONDS:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind="static-asset-not-cached",
                    title=f"Static {entry.type} is served without a long cache lifetime",
                    expected=f"max-age of at least {CACHE_MIN_SECONDS}",
                    actual=cache_control or "no Cache-Control header",
                    stable_key=synthetic_key(self.id, entry.url),
                    data={"url": entry.url, "cacheControl": cache_control},
                )


@checker
class RenderBlocking:
    id = "performance.render-blocking"
    category = Category.performance
    requires = frozenset({Capability.DOM})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            facts = head_facts(ctx.dom(page.id))
            if facts.blockingScripts:
                yield self._finding(
                    page,
                    "render-blocking-script",
                    f"{len(facts.blockingScripts)} script(s) block first paint",
                    "every script in <head> is deferred, async or moved",
                    ", ".join(facts.blockingScripts[:3]),
                    facts.blockingScripts,
                )
            if len(facts.blockingStyles) > MAX_BLOCKING_STYLES:
                yield self._finding(
                    page,
                    "render-blocking-stylesheet",
                    f"{len(facts.blockingStyles)} stylesheets block first paint",
                    f"at most {MAX_BLOCKING_STYLES}",
                    str(len(facts.blockingStyles)),
                    facts.blockingStyles,
                )

    def _finding(
        self, page: PageRecord, kind: str, title: str, expected: str, actual: str, urls: list[str]
    ) -> Finding:
        return page_finding(
            self,
            page,
            kind=kind,
            title=title,
            expected=expected,
            actual=actual,
            stable_key=synthetic_key(self.id, kind),
            data={"urls": urls},
        )


def _max_age(cache_control: str) -> int:
    for part in cache_control.split(","):
        name, _, value = part.strip().partition("=")
        if name.lower() == "max-age" and value.strip().isdigit():
            return int(value.strip())
    return 0
