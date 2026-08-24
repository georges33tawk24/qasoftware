"""Group A, the head-of-document findings — SPEC §8.4 A."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from urllib.parse import urlsplit

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import PageRecord
from engine.checkers.base import checker
from engine.checkers.domdata import HeadFacts, head_facts
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Finding, Severity


def _facts(ctx: RunContext) -> Iterable[tuple[PageRecord, HeadFacts]]:
    for page in live_pages(ctx):
        yield page, head_facts(ctx.dom(page.id))


@checker
class Titles:
    id = "free.title"
    category = Category.free
    requires = frozenset({Capability.DOM})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        by_title: dict[str, list[PageRecord]] = defaultdict(list)
        for page, facts in _facts(ctx):
            title = facts.title
            if not title:
                yield page_finding(
                    self,
                    page,
                    kind="missing-title",
                    title="Page has no <title>",
                    description="The tab, the search result and the bookmark all use it.",
                    expected="a unique, descriptive title",
                    actual="empty" if facts.titles else "no <title> element",
                )
                continue
            if len(facts.titles) > 1:
                yield page_finding(
                    self,
                    page,
                    kind="multiple-titles",
                    title="Page has more than one <title>",
                    expected="exactly one",
                    actual=f"{len(facts.titles)}",
                    severity=Severity.minor,
                )
            by_title[title].append(page)

        for title, pages in sorted(by_title.items()):
            if len(pages) < 2:
                continue
            for page in pages:
                yield page_finding(
                    self,
                    page,
                    kind="duplicate-title",
                    title="Title is shared with another page",
                    description=f"{len(pages)} pages use this exact title.",
                    expected="a unique title per page",
                    actual=title,
                    severity=Severity.minor,
                    stable_key=synthetic_key(self.id, title),
                    data={"title": title, "pages": [p.path for p in pages]},
                )


@checker
class MetaDescription:
    id = "free.meta-description"
    category = Category.free
    requires = frozenset({Capability.DOM})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        by_description: dict[str, list[PageRecord]] = defaultdict(list)
        for page, facts in _facts(ctx):
            description = facts.description
            if not description:
                yield page_finding(
                    self,
                    page,
                    kind="missing-meta-description",
                    title="No meta description",
                    expected="a meta description",
                    actual="missing" if not facts.descriptions else "empty",
                )
                continue
            by_description[description].append(page)

        for description, pages in sorted(by_description.items()):
            if len(pages) < 2:
                continue
            for page in pages:
                yield page_finding(
                    self,
                    page,
                    kind="duplicate-meta-description",
                    title="Meta description is shared with another page",
                    expected="a unique description per page",
                    actual=description[:120],
                    stable_key=synthetic_key(self.id, description),
                    data={"pages": [p.path for p in pages]},
                )


@checker
class Canonical:
    id = "free.canonical"
    category = Category.free
    requires = frozenset({Capability.DOM})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page, facts in _facts(ctx):
            if not facts.canonicals:
                yield page_finding(
                    self,
                    page,
                    kind="missing-canonical",
                    title="No canonical link",
                    expected="a canonical URL",
                    actual="missing",
                )
                continue
            if len(facts.canonicals) > 1:
                yield page_finding(
                    self,
                    page,
                    kind="multiple-canonicals",
                    title="More than one canonical link",
                    expected="exactly one",
                    actual=f"{len(facts.canonicals)}",
                )
                continue
            target = urlsplit(facts.canonicals[0]).path.rstrip("/") or "/"
            here = page.path.rstrip("/") or "/"
            if target != here:
                yield page_finding(
                    self,
                    page,
                    kind="canonical-points-elsewhere",
                    title="Canonical points at a different page",
                    description="Deliberate for paginated or filtered views; a mistake "
                    "everywhere else.",
                    expected=here,
                    actual=target,
                )


@checker
class Indexability:
    id = "free.noindex"
    category = Category.free
    requires = frozenset({Capability.DOM})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page, facts in _facts(ctx):
            directives = " ".join(facts.robots).lower()
            header = ""
            for entry in ctx.network(page.id) if ctx.has(Capability.NETWORK) else []:
                if entry.url == page.url:
                    header = entry.resHeaders.get("x-robots-tag", "").lower()
                    break
            if "noindex" in directives or "noindex" in header:
                yield page_finding(
                    self,
                    page,
                    kind="noindex-on-production",
                    title="Page is marked noindex",
                    description="Usually a staging directive that shipped.",
                    expected="indexable",
                    actual=(directives or header).strip(),
                )


@checker
class Favicon:
    id = "free.favicon"
    category = Category.free
    requires = frozenset({Capability.DOM, Capability.NETWORK})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        # ponytail: a missing web app manifest is only a defect for a PWA, and flagging
        # every brochure site for one is exactly the noise that gets a tool muted.
        for page, facts in _facts(ctx):
            if facts.icons:
                continue
            served = any(
                "favicon" in entry.url and entry.status == 200 for entry in ctx.network(page.id)
            )
            if not served:
                yield page_finding(
                    self,
                    page,
                    kind="missing-favicon",
                    title="No favicon",
                    expected="a favicon link or /favicon.ico",
                    actual="neither",
                )


@checker
class ViewportMeta:
    id = "free.viewport-meta"
    category = Category.free
    requires = frozenset({Capability.DOM})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page, facts in _facts(ctx):
            content = (facts.viewportMeta or "").lower()
            if not content:
                yield page_finding(
                    self,
                    page,
                    kind="missing-viewport-meta",
                    title="No viewport meta tag",
                    description="Without it a phone renders the desktop layout at 980px "
                    "and zooms out.",
                    expected="width=device-width",
                    actual="missing",
                )
                continue
            squashed = content.replace(" ", "")
            if "user-scalable=no" in squashed or "maximum-scale=1" in squashed:
                yield page_finding(
                    self,
                    page,
                    kind="zoom-disabled",
                    title="Viewport meta disables zoom",
                    description="WCAG 1.4.4: people need to be able to zoom.",
                    expected="zoom allowed",
                    actual=content,
                )
