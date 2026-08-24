"""Group D, content and copy — SPEC §8.4 D."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import ElementRecord, PageRecord
from engine.checkers.base import checker
from engine.checkers.support import (
    Surface,
    contiguous_runs,
    element_finding,
    live_pages,
    page_finding,
    surfaces,
    synthetic_key,
    widest_surfaces,
)
from engine.issues.models import Category, Finding, Severity

PLACEHOLDER = re.compile(
    r"\b(lorem ipsum|dolor sit amet|consectetur adipiscing|todo|tbd|fixme|xxx+|asdf+"
    r"|test test|placeholder text|coming soon\.\.\.|foo ?bar)\b",
    re.IGNORECASE,
)

I18N_KEY = re.compile(r"^[a-z][a-z0-9]*(?:[._][a-z0-9]+){2,}$")
"""`home.hero.title`. Three segments, no spaces — a key that escaped the translation
layer, not a sentence."""

MOJIBAKE = re.compile("â€|Ã[\u0080-\u00bf]|Â[\u00a0-\u00bf]|\ufffd")
"""UTF-8 bytes decoded as Latin-1. `’` (E2 80 99) comes out as `â€™`, `é` as `Ã©`, and a
non-breaking space as `Â `. U+FFFD is the decoder giving up outright."""
UNESCAPED_ENTITY = re.compile(r"&(?:amp|lt|gt|quot|nbsp|#\d+|#x[0-9a-f]+);", re.IGNORECASE)

TERMS = [
    ("sign in", "log in"),
    ("sign up", "register"),
    ("basket", "cart"),
    ("cancel", "dismiss"),
]

DATE_FORMATS = {
    "iso": re.compile(r"\b\d{4}-\d{2}-\d{2}\b"),
    "slashed": re.compile(r"\b\d{1,2}/\d{1,2}/\d{2,4}\b"),
    "long": re.compile(
        r"\b\d{1,2} (?:January|February|March|April|May|June|July|August|September|October"
        r"|November|December) \d{4}\b"
    ),
}

BUTTON_ROLES = frozenset({"button", "link"})
MIN_SAMPLES = 3


@checker
class PlaceholderText:
    id = "content.placeholder"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in widest_surfaces(ctx):
            for element in surface.laid_out:
                match = PLACEHOLDER.search(element.text)
                if not match:
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="placeholder-text",
                    title=f"Placeholder copy on the page: {match.group(0)!r}",
                    expected="real copy",
                    actual=element.text[:120],
                    data={"marker": match.group(0)},
                )


@checker
class RawI18nKeys:
    id = "content.i18n-key"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in widest_surfaces(ctx):
            for element in surface.laid_out:
                text = element.text.strip()
                if not text or " " in text or not I18N_KEY.match(text):
                    continue
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="raw-i18n-key",
                    title=f"Untranslated key on the page: {text}",
                    description="The translation layer did not resolve this string.",
                    expected="translated copy",
                    actual=text,
                )


@checker
class Encoding:
    id = "content.encoding"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in widest_surfaces(ctx):
            for element in surface.laid_out:
                text = element.text
                if not text:
                    continue
                if MOJIBAKE.search(text):
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="mojibake",
                        title="Text contains mis-decoded characters",
                        description="UTF-8 read as Latin-1 somewhere in the pipeline.",
                        expected="correctly decoded text",
                        actual=text[:120],
                    )
                elif UNESCAPED_ENTITY.search(text):
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="visible-html-entity",
                        title="An HTML entity is showing as literal text",
                        description="Double-escaped on the way to the page.",
                        expected="the decoded character",
                        actual=text[:120],
                        severity=Severity.minor,
                    )


@checker
class EmptyCards:
    id = "content.empty-card"
    category = Category.content
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for group in surface.layout.repeatedGroups:
                for members in contiguous_runs(surface, group.elementIds):
                    if len(members) < MIN_SAMPLES:
                        continue
                    filled = [m for m in members if m.textFull.strip() or _has_image(surface, m)]
                    if len(filled) == len(members) or not filled:
                        continue
                    for member in members:
                        if member in filled:
                            continue
                        yield element_finding(
                            self,
                            surface,
                            member,
                            kind="empty-repeated-item",
                            title="One item in a repeated group is empty",
                            description=f"{len(filled)} of {len(members)} × {group.signature} "
                            "have content.",
                            expected="content, like its siblings",
                            actual="no text and no image",
                            groupAs=group.signature,
                            data={"signature": group.signature},
                        )


@checker
class DuplicateListings:
    id = "content.duplicate-listing"
    category = Category.content
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for group in surface.layout.repeatedGroups:
                for members in contiguous_runs(surface, group.elementIds):
                    if len(members) < MIN_SAMPLES:
                        continue
                    # SPEC §8.4 D: match on title, href and image src independently, because a
                    # listing can repeat an item while varying one of the three.
                    for label in ("title", "link", "image"):
                        values = [_signature_of(surface, m, label) for m in members]
                        counts = Counter(v for v in values if v)
                        for value, count in counts.items():
                            if count < 2:
                                continue
                            for member, has in zip(members, values, strict=True):
                                if has != value:
                                    continue
                                yield element_finding(
                                    self,
                                    surface,
                                    member,
                                    kind=f"duplicate-listing-{label}",
                                    title=f"Listing repeats the same {label} {count} times",
                                    expected="distinct items",
                                    actual=value[:120],
                                    groupAs=f"{group.signature}:{label}",
                                    data={"signature": group.signature, "matchedOn": label},
                                )


@checker
class Terminology:
    id = "content.terminology"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        sightings: dict[str, list[tuple[Surface, ElementRecord]]] = defaultdict(list)
        for surface in widest_surfaces(ctx):
            for element in surface.laid_out:
                if not element.clickable and element.role not in BUTTON_ROLES:
                    continue
                text = element.text.strip().lower()
                if text:
                    sightings[text].append((surface, element))

        for first, second in TERMS:
            if first not in sightings or second not in sightings:
                continue
            for surface, element in sightings[first] + sightings[second]:
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="inconsistent-terminology",
                    title=f"This site uses both {first!r} and {second!r}",
                    description="Pick one. People read the difference as two features.",
                    expected=f"{first!r} or {second!r} throughout",
                    actual=element.text.strip(),
                    groupAs=f"{first}|{second}",
                    stable_key=synthetic_key(self.id, first, second),
                    data={"terms": [first, second]},
                )


@checker
class DateFormats:
    id = "content.date-format"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        found: dict[str, list[tuple[Surface, ElementRecord]]] = defaultdict(list)
        for surface in widest_surfaces(ctx):
            for element in surface.laid_out:
                for style, pattern in DATE_FORMATS.items():
                    if pattern.search(element.text):
                        found[style].append((surface, element))
        if len(found) < 2:
            return
        styles = ", ".join(sorted(found))
        for style in sorted(found):
            for surface, element in found[style]:
                yield element_finding(
                    self,
                    surface,
                    element,
                    kind="mixed-date-formats",
                    title=f"This site shows dates in {len(found)} different formats",
                    expected="one date format",
                    actual=f"{style}: {element.text.strip()[:60]}",
                    stable_key=synthetic_key(self.id, styles),
                    data={"formats": sorted(found)},
                )


@checker
class DeadEnds:
    id = "content.dead-end"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        by_page: dict[str, PageRecord] = {p.id: p for p in live_pages(ctx)}
        for page_id, page in by_page.items():
            names = ctx.viewport_names(page_id)
            if not names:
                continue
            elements = ctx.elements(page_id, names[0])
            outbound = {
                e.link.resolved
                for e in elements
                if e.link and e.visible and not e.link.href.startswith("#")
            }
            if outbound - {page.url}:
                continue
            yield page_finding(
                self,
                page,
                kind="dead-end-page",
                title="Page has no links out",
                description="Whatever brought a visitor here, there is nowhere to go next.",
                expected="at least one outbound link",
                actual="none",
            )


def _descendants(surface: Surface, element: ElementRecord) -> Iterable[ElementRecord]:
    stack = list(element.childIds)
    while stack:
        child = surface.by_id.get(stack.pop())
        if child is None:
            continue
        stack.extend(child.childIds)
        yield child


def _has_image(surface: Surface, element: ElementRecord) -> bool:
    return element.image is not None or any(d.image for d in _descendants(surface, element))


def _signature_of(surface: Surface, element: ElementRecord, label: str) -> str:
    if label == "title":
        return element.textFull.strip()[:120]
    if label == "link":
        return _first_href(surface, element)
    return _first_image(surface, element)


def _first_href(surface: Surface, element: ElementRecord) -> str:
    for candidate in [element, *_descendants(surface, element)]:
        if candidate.link:
            return candidate.link.resolved
    return ""


def _first_image(surface: Surface, element: ElementRecord) -> str:
    for candidate in [element, *_descendants(surface, element)]:
        if candidate.image:
            return candidate.image.src
    return ""
