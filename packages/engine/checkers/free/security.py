"""Group A, the transport and header findings — SPEC §8.4 A.

Configuration checking, not exploitation: every one of these reads something the site
already told us during a normal page load.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from urllib.parse import urlsplit

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import NetworkEntry, PageRecord
from engine.checkers import resolution
from engine.checkers.base import checker
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Finding, Severity

REQUIRED_HEADERS = {
    "content-security-policy": ("Content-Security-Policy", Severity.minor),
    "x-content-type-options": ("X-Content-Type-Options", Severity.minor),
    "x-frame-options": ("X-Frame-Options", Severity.minor),
    "referrer-policy": ("Referrer-Policy", Severity.trivial),
}

CERT_EXPIRY_WARNING_DAYS = 30


def document_response(ctx: RunContext, page: PageRecord) -> NetworkEntry | None:
    for entry in ctx.network(page.id):
        if entry.url == page.url or (entry.type == "document" and entry.status < 400):
            return entry
    return None


@checker
class SecurityHeaders:
    id = "free.security-headers"
    category = Category.free
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            response = document_response(ctx, page)
            if response is None:
                continue
            for header, (label, severity) in REQUIRED_HEADERS.items():
                if header in response.resHeaders:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind=f"missing-header-{header}",
                    title=f"{label} header is not set",
                    expected=f"{label} present",
                    actual="absent",
                    severity=severity,
                    stable_key=synthetic_key(self.id, header),
                )
            if urlsplit(page.url).scheme == "https" and (
                "strict-transport-security" not in response.resHeaders
            ):
                yield page_finding(
                    self,
                    page,
                    kind="missing-hsts",
                    title="No Strict-Transport-Security header",
                    expected="HSTS present on an HTTPS response",
                    actual="absent",
                    stable_key=synthetic_key(self.id, "hsts"),
                )


@checker
class CookieFlags:
    id = "free.cookie-flags"
    category = Category.free
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            for entry in ctx.network(page.id):
                raw = entry.resHeaders.get("set-cookie")
                # The value is redacted at capture; the flags are what this reads.
                if not raw:
                    continue
                lowered = raw.lower()
                for flag, kind in (
                    ("secure", "cookie-not-secure"),
                    ("httponly", "cookie-not-httponly"),
                    ("samesite", "cookie-no-samesite"),
                ):
                    if flag in lowered:
                        continue
                    yield page_finding(
                        self,
                        page,
                        kind=kind,
                        title=f"Cookie set without {flag.capitalize()}",
                        expected=f"{flag} on every cookie",
                        actual="missing",
                        severity=Severity.minor if flag == "samesite" else Severity.major,
                        stable_key=synthetic_key(self.id, kind, entry.url),
                        data={"url": entry.url},
                    )


@checker
class Certificate:
    id = "free.certificate"
    category = Category.free
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        # "Now" is the run's own start time. A checker that reads the clock is not a pure
        # function of the artifact and would give a different answer on a re-check.
        now = ctx.manifest.startedAt
        for page in live_pages(ctx):
            if urlsplit(page.url).scheme != "https":
                if page.depth == 0:
                    yield page_finding(
                        self,
                        page,
                        kind="not-https",
                        title="Site is served over plain HTTP",
                        expected="https",
                        actual="http",
                    )
                continue
            security = page.security
            if security is None or security.validTo is None:
                continue
            expires = datetime.fromtimestamp(security.validTo, tz=UTC)
            days = (expires - now).total_seconds() / 86400
            if days < 0:
                yield page_finding(
                    self,
                    page,
                    kind="certificate-expired",
                    title="TLS certificate has expired",
                    expected="a valid certificate",
                    actual=expires.date().isoformat(),
                    severity=Severity.blocker,
                    stable_key=synthetic_key(self.id, "expiry"),
                )
            elif days < CERT_EXPIRY_WARNING_DAYS:
                yield page_finding(
                    self,
                    page,
                    kind="certificate-expiring",
                    title=f"TLS certificate expires in {int(days)} days",
                    expected=f"more than {CERT_EXPIRY_WARNING_DAYS} days left",
                    actual=expires.date().isoformat(),
                    severity=Severity.major,
                    stable_key=synthetic_key(self.id, "expiry"),
                )


@checker
class SourceMaps:
    id = "free.source-maps"
    category = Category.free
    requires = frozenset({Capability.NETWORK})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            seen: set[str] = set()
            for entry in ctx.network(page.id):
                if not entry.sourceMapUrl or entry.url in seen:
                    continue
                if entry.sourceMapUrl.startswith("data:"):
                    continue
                seen.add(entry.url)
                yield page_finding(
                    self,
                    page,
                    kind="source-map-exposed",
                    title="Script ships a source map reference",
                    description="Source maps hand out the original source, comments and "
                    "all. Fine on staging, rarely intended in production.",
                    expected="no sourceMappingURL in production bundles",
                    actual=entry.sourceMapUrl[:120],
                    stable_key=synthetic_key(self.id, entry.url),
                    data={"script": entry.url, "map": entry.sourceMapUrl},
                )


SIGNATURES = {
    "/.git/config": ("[core]", "repositoryformatversion"),
    "/.git/HEAD": ("ref:",),
    "/.env": ("=",),
    "/.DS_Store": ("Bud1",),
    "/config.json": ("{",),
    "/backup.sql": ("CREATE TABLE", "INSERT INTO", "-- MySQL", "-- PostgreSQL"),
    "/.htaccess": ("RewriteEngine", "Order ", "Deny ", "<Files"),
}
"""What the real file starts with. A single-page app answers 200 with its own HTML for
every path on the site, so `critical` has to be earned by the body and not the status."""


def _looks_like(path: str, sample: str | None) -> bool:
    if not sample:
        return False
    lowered = sample[:400].lower()
    if "<html" in lowered or "<!doctype" in lowered:
        return False
    markers = SIGNATURES.get(path)
    if markers is None:
        return True
    return any(marker.lower() in sample.lower() for marker in markers)


@checker
class ExposedPaths:
    id = "free.exposed-paths"
    category = Category.free
    requires = frozenset({Capability.PROBES})
    default_severity = Severity.critical

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        probes = ctx.probes()
        pages = list(live_pages(ctx))
        if probes is None or not pages:
            return
        seed = min(pages, key=lambda p: p.depth)
        missing = next(
            (p.bodyHash for p in probes.paths if p.kind == "not-found-handling" and p.bodyHash),
            None,
        )
        for probe in probes.paths:
            if probe.kind != "exposed-path" or probe.status != 200:
                continue
            if missing and probe.bodyHash == missing:
                # The app shell, wearing a 200. Not a file.
                continue
            looks_right = _looks_like(probe.path, probe.bodySample)
            yield page_finding(
                self,
                seed,
                kind="exposed-path",
                title=f"{probe.path} is publicly readable",
                description=(
                    "Served with a 200 to an anonymous request, and the body looks like "
                    "the file it was asked for."
                    if looks_right
                    else "Served with a 200 to an anonymous request."
                ),
                expected="404 or 403",
                actual="200",
                severity=self.default_severity if looks_right else Severity.minor,
                stable_key=synthetic_key(self.id, probe.path),
                data={
                    "path": probe.path,
                    "sample": probe.bodySample,
                    resolution.BODY_HASH: probe.bodyHash,
                    resolution.EXISTENCE_BASIS: "content" if looks_right else "status",
                },
            )
