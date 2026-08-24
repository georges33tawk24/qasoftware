"""Group A, the link findings — SPEC §8.4 A.

Every status here was resolved at capture time into `probes.json`. A checker never makes
a request (CLAUDE.md).
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers.base import checker
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Finding, Severity

MAX_REDIRECT_HOPS = 2


UNVERIFIABLE_STATUS = frozenset({401, 403, 405, 429, 999})
"""What a bot gets, not what a visitor gets.

LinkedIn answers 999 to anything automated and Cloudflare answers 403; reporting either
as a broken link sends someone to check a link that works perfectly in a browser. Said
out loud as unverifiable rather than dropped, because a real 403 is also possible.
"""


@checker
class BrokenLinks:
    id = "free.broken-link"
    category = Category.free
    requires = frozenset({Capability.PROBES})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        probes = ctx.probes()
        if probes is None:
            return
        pages = {page.id: page for page in live_pages(ctx)}
        for link in probes.links:
            if link.status and link.status < 400:
                continue
            unverifiable = not link.internal and link.status in UNVERIFIABLE_STATUS
            if unverifiable:
                kind, title = (
                    "unverifiable-external-link",
                    "Link to another site could not be checked",
                )
                detail = (
                    f"{link.url} answered {link.status} to an automated request, which is "
                    "what bot protection looks like. It may well work in a browser."
                )
            else:
                kind = "broken-internal-link" if link.internal else "broken-external-link"
                title = "Broken link" if link.internal else "Broken link to another site"
                detail = f"{link.url} — linked from {{path}}."
            for page_id in link.foundOn:
                page = pages.get(page_id)
                if page is None:
                    continue
                yield page_finding(
                    self,
                    page,
                    kind=kind,
                    title=title,
                    description=detail.format(path=page.path),
                    expected="2xx or 3xx",
                    actual=f"{link.error or link.status} — {link.url}",
                    # An external site being down is not this team's emergency, and one we
                    # were not allowed to check is not a defect at all.
                    severity=(
                        Severity.trivial
                        if unverifiable
                        else Severity.major
                        if link.internal
                        else Severity.minor
                    ),
                    stable_key=synthetic_key(self.id, link.url),
                    data={"url": link.url, "status": link.status, "error": link.error},
                )


@checker
class Redirects:
    id = "free.redirects"
    category = Category.free
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            chain = page.redirectChain
            if not chain:
                continue
            if page.url in chain:
                yield page_finding(
                    self,
                    page,
                    kind="redirect-loop",
                    title="Redirect chain returns to where it started",
                    expected="a chain that terminates somewhere else",
                    actual=" → ".join([*chain, page.url]),
                    severity=Severity.major,
                )
            elif len(chain) > MAX_REDIRECT_HOPS:
                yield page_finding(
                    self,
                    page,
                    kind="long-redirect-chain",
                    title=f"{len(chain)} redirects before the page loads",
                    description="Every hop is a round trip the visitor waits for.",
                    expected=f"at most {MAX_REDIRECT_HOPS}",
                    actual=str(len(chain)),
                    data={"chain": [*chain, page.url]},
                )


@checker
class NotFoundHandling:
    id = "free.not-found"
    category = Category.free
    requires = frozenset({Capability.PROBES})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        probes = ctx.probes()
        pages = list(live_pages(ctx))
        if probes is None or not pages:
            return
        seed = min(pages, key=lambda p: p.depth)
        for probe in probes.paths:
            if probe.kind != "not-found-handling" or probe.status != 200:
                continue
            yield page_finding(
                self,
                seed,
                kind="soft-404",
                title="A missing page returns 200",
                description="Search engines index the error page, and every broken-link "
                "check on this site is now blind.",
                expected="404",
                actual="200",
                stable_key=synthetic_key(self.id, probe.path),
            )


@checker
class PageStatus:
    id = "free.page-status"
    category = Category.free
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        # Deliberately reads `ctx.pages()` rather than `live_pages`: this is the one
        # checker whose whole job is the pages every other checker skips.
        for page in ctx.pages():
            if page.crawlBlocked or 200 <= page.status < 400:
                continue
            yield page_finding(
                self,
                page,
                kind="page-error-status",
                title=f"Page returned {page.status}",
                description="Reached by the crawler and did not serve content.",
                expected="2xx",
                actual=str(page.status),
                severity=Severity.blocker if page.status >= 500 else Severity.major,
                data={"status": page.status, "discoveredFrom": page.discoveredFrom},
            )
