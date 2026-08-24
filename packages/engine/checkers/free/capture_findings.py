"""Group A, the findings capture hands you for free — SPEC §8.4 A."""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import urlsplit

from engine.artifact.context import Capability, RunContext
from engine.checkers.base import checker
from engine.checkers.support import (
    element_finding,
    live_pages,
    page_finding,
    surfaces,
    synthetic_key,
)
from engine.issues.models import Category, Finding, Severity

_VOLATILE = re.compile(r"\b\d[\d.]*\b|https?://\S+|'[^']*'|\"[^\"]*\"")

REJECTION_MARKERS = ("uncaught (in promise)", "unhandled promise rejection", "unhandledrejection")


def stable_message(text: str) -> str:
    """Two runs report the same error with different line numbers and ids. The identity
    of the error is the shape of the message, not the numbers in it."""
    return _VOLATILE.sub("*", text).strip()[:200]


@checker
class ConsoleErrors:
    id = "free.console"
    category = Category.free
    requires = frozenset({Capability.CONSOLE})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            seen: set[tuple[str, str]] = set()
            for message in ctx.console(page.id):
                if message.level != "error":
                    continue
                lowered = message.text.lower()
                kind = (
                    "unhandled-rejection"
                    if any(marker in lowered for marker in REJECTION_MARKERS)
                    else "console-error"
                )
                shape = stable_message(message.text)
                if (kind, shape) in seen:
                    continue
                seen.add((kind, shape))
                yield page_finding(
                    self,
                    page,
                    kind=kind,
                    title=(
                        "Unhandled promise rejection"
                        if kind == "unhandled-rejection"
                        else "JavaScript error in the console"
                    ),
                    description=(
                        "The browser reported this while the page loaded. Whatever the "
                        "script was doing did not finish."
                    ),
                    expected="no errors in the console",
                    actual=shape,
                    stable_key=synthetic_key(self.id, kind, shape),
                    data={"text": message.text, "url": message.url, "line": message.line},
                )


@checker
class FailedRequests:
    id = "free.subresource"
    category = Category.free
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            seen: set[str] = set()
            for entry in ctx.network(page.id):
                if entry.url == page.url:
                    continue  # the document's own status is on the page record
                broken = entry.failure is not None or entry.status >= 400
                if not broken or entry.url in seen:
                    continue
                seen.add(entry.url)
                yield page_finding(
                    self,
                    page,
                    kind="request-failed" if entry.failure else "request-error-status",
                    title=f"{entry.type} request failed: {_short(entry.url)}",
                    description="A resource this page asked for did not arrive.",
                    expected="2xx or 3xx",
                    actual=entry.failure or str(entry.status),
                    severity=Severity.major
                    if entry.type in ("script", "stylesheet", "document", "fetch", "xhr")
                    else Severity.minor,
                    stable_key=synthetic_key(self.id, entry.url),
                    data={"url": entry.url, "status": entry.status, "type": entry.type},
                )


@checker
class MixedContent:
    id = "free.mixed-content"
    category = Category.free
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            if urlsplit(page.url).scheme != "https":
                continue
            for entry in ctx.network(page.id):
                if urlsplit(entry.url).scheme != "http":
                    continue
                yield page_finding(
                    self,
                    page,
                    kind="mixed-content",
                    title=f"Insecure resource on an HTTPS page: {_short(entry.url)}",
                    description="Browsers block or downgrade these, so the page is not "
                    "getting what it asked for.",
                    expected="https",
                    actual="http",
                    stable_key=synthetic_key(self.id, entry.url),
                    data={"url": entry.url, "type": entry.type},
                )


@checker
class BrokenImages:
    id = "free.broken-image"
    category = Category.free
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.elements:
                image = element.image
                if element.tag != "img" or image is None:
                    continue
                if image.loaded and image.naturalW > 0:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="broken-image",
                    title="Image did not load",
                    description="naturalWidth is 0, so the browser has nothing to draw.",
                    expected="the image renders",
                    actual="naturalWidth = 0",
                    data={"src": image.src, "alt": image.alt},
                )


def _short(url: str, limit: int = 60) -> str:
    """The path, not the basename: `/api/me` says where to look, `me` says nothing."""
    parts = urlsplit(url)
    path = parts.path or url
    return path if len(path) <= limit else "…" + path[-(limit - 1) :]
