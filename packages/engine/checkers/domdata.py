"""Facts pulled out of the captured `dom.html`.

Parsing a stored string is still a pure function over the artifact, so this stays inside
the checker layer rather than becoming another capture step.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser


@dataclass
class HeadFacts:
    lang: str | None = None
    charset: str | None = None
    titles: list[str] = field(default_factory=list)
    descriptions: list[str] = field(default_factory=list)
    canonicals: list[str] = field(default_factory=list)
    robots: list[str] = field(default_factory=list)
    icons: list[str] = field(default_factory=list)
    manifest: str | None = None
    viewportMeta: str | None = None
    blockingStyles: list[str] = field(default_factory=list)
    blockingScripts: list[str] = field(default_factory=list)
    fontPreloads: list[str] = field(default_factory=list)

    @property
    def title(self) -> str | None:
        return self.titles[0].strip() if self.titles else None

    @property
    def description(self) -> str | None:
        return self.descriptions[0].strip() if self.descriptions else None

    @property
    def canonical(self) -> str | None:
        return self.canonicals[0] if self.canonicals else None


class _Head(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.facts = HeadFacts()
        self._in_head = False
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k.lower(): (v or "") for k, v in attrs}
        if tag == "html":
            self.facts.lang = attr.get("lang") or None
        elif tag == "head":
            self._in_head = True
        elif tag == "body":
            self._in_head = False
        elif tag == "title":
            self._in_title = True
            self.facts.titles.append("")
        elif tag == "meta":
            name = attr.get("name", "").lower()
            if "charset" in attr:
                self.facts.charset = attr["charset"]
            elif name == "description":
                self.facts.descriptions.append(attr.get("content", ""))
            elif name == "robots":
                self.facts.robots.append(attr.get("content", ""))
            elif name == "viewport":
                self.facts.viewportMeta = attr.get("content", "")
        elif tag == "link":
            rel = attr.get("rel", "").lower().split()
            href = attr.get("href", "")
            if "canonical" in rel:
                self.facts.canonicals.append(href)
            elif "icon" in rel or "apple-touch-icon" in rel:
                self.facts.icons.append(href)
            elif "manifest" in rel:
                self.facts.manifest = href
            elif "stylesheet" in rel and self._in_head and attr.get("media", "all") != "print":
                self.facts.blockingStyles.append(href)
            elif "preload" in rel and attr.get("as") == "font":
                self.facts.fontPreloads.append(href)
        elif (
            tag == "script"
            and self._in_head
            and attr.get("src")
            and "defer" not in attr
            and "async" not in attr
            and attr.get("type", "") not in ("module", "application/json", "application/ld+json")
        ):
            self.facts.blockingScripts.append(attr["src"])

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "head":
            self._in_head = False

    def handle_data(self, data: str) -> None:
        if self._in_title and self.facts.titles:
            self.facts.titles[-1] += data


def head_facts(html: str | None) -> HeadFacts:
    if not html:
        return HeadFacts()
    parser = _Head()
    parser.feed(html)
    parser.close()
    return parser.facts
