"""Crawl policy — SPEC §5.

BFS from the seed. This module decides *which* URLs; `run.py` decides what happens once
a page is open. Keeping them apart is what makes the policy unit-testable without a
browser.
"""

from __future__ import annotations

import re
import urllib.robotparser
from collections import deque
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit, urlunsplit
from xml.etree import ElementTree

from playwright.async_api import APIRequestContext, Page

_HEX = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
_NUMERIC = re.compile(r"^\d+$")
_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+){2,}$", re.IGNORECASE)


@dataclass(frozen=True)
class Target:
    url: str
    depth: int
    discovered_from: str | None


def normalise(url: str, ignore_query_params: frozenset[str] = frozenset()) -> str:
    """Dedupe key: no fragment, no ignored params, sorted params, no trailing slash."""
    parts = urlsplit(url)
    query = "&".join(
        sorted(
            pair
            for pair in parts.query.split("&")
            if pair and pair.split("=", 1)[0] not in ignore_query_params
        )
    )
    path = parts.path.rstrip("/") or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, query, ""))


def path_shape(url: str) -> str:
    """`/blog/my-post-title` and `/blog/another-one-here` share the shape `/blog/*`."""
    segments = []
    for segment in urlsplit(url).path.strip("/").split("/"):
        if _NUMERIC.match(segment) or _HEX.match(segment) or _SLUG.match(segment):
            segments.append("*")
        else:
            segments.append(segment)
    return "/" + "/".join(segments)


class Robots:
    """robots.txt, respected by default with an explicit override (SPEC §5)."""

    def __init__(self, parser: urllib.robotparser.RobotFileParser | None, user_agent: str) -> None:
        self._parser = parser
        self._user_agent = user_agent

    @classmethod
    async def load(cls, request: APIRequestContext, seed: str, user_agent: str) -> Robots:
        parts = urlsplit(seed)
        url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            response = await request.get(url, timeout=10_000)
            if not response.ok:
                return cls(None, user_agent)
            parser = urllib.robotparser.RobotFileParser()
            parser.parse((await response.text()).splitlines())
            return cls(parser, user_agent)
        except Exception:
            return cls(None, user_agent)

    def allows(self, url: str) -> bool:
        return True if self._parser is None else self._parser.can_fetch(self._user_agent, url)


class UrlPolicy:
    def __init__(
        self,
        seed: str,
        *,
        include: list[str] | None = None,
        exclude: list[str] | None = None,
        same_origin: bool = True,
        ignore_query_params: list[str] | None = None,
        robots: Robots | None = None,
    ) -> None:
        self.seed = seed
        self.origin = urlsplit(seed).netloc.lower()
        self.include = [re.compile(p) for p in include or []]
        self.exclude = [re.compile(p) for p in exclude or []]
        self.same_origin = same_origin
        self.ignore_query_params = frozenset(ignore_query_params or [])
        self.robots = robots

    def normalise(self, url: str) -> str:
        return normalise(url, self.ignore_query_params)

    def allowed(self, url: str) -> str | None:
        """None when allowed, otherwise the reason it was skipped."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return f"scheme {parts.scheme!r}"
        if self.same_origin and parts.netloc.lower() != self.origin:
            return "off-origin"
        if self.exclude and any(p.search(url) for p in self.exclude):
            return "matches an exclude pattern"
        if self.include and not any(p.search(url) for p in self.include):
            return "matches no include pattern"
        if self.robots is not None and not self.robots.allows(url):
            return "disallowed by robots.txt"
        return None


class Frontier:
    """BFS queue with dedupe and templated-page sampling."""

    def __init__(
        self,
        policy: UrlPolicy,
        *,
        max_depth: int = 3,
        max_pages: int = 50,
        template_sample: int = 5,
    ) -> None:
        self.policy = policy
        self.max_depth = max_depth
        self.max_pages = max_pages
        self.template_sample = template_sample
        self.seen: set[str] = set()
        self.skipped: dict[str, str] = {}
        self._shapes: dict[str, int] = {}
        self._queue: deque[Target] = deque()
        self.issued = 0

    def push(self, url: str, depth: int, discovered_from: str | None) -> bool:
        key = self.policy.normalise(url)
        if key in self.seen:
            return False
        if depth > self.max_depth:
            self.skipped[key] = f"deeper than maxDepth={self.max_depth}"
            return False
        reason = self.policy.allowed(key)
        if reason:
            self.seen.add(key)
            self.skipped[key] = reason
            return False
        shape = path_shape(key)
        count = self._shapes.get(shape, 0)
        if count >= self.template_sample and shape.count("*"):
            self.seen.add(key)
            self.skipped[key] = f"templated page {shape} already sampled {count}x"
            return False
        self._shapes[shape] = count + 1
        self.seen.add(key)
        self._queue.append(Target(key, depth, discovered_from))
        return True

    def pop(self) -> Target | None:
        if self.issued >= self.max_pages or not self._queue:
            return None
        self.issued += 1
        return self._queue.popleft()


async def links_on(page: Page) -> list[str]:
    """Anchors plus any client-side router links that expose an href."""
    hrefs: list[str] = await page.evaluate(
        """() => Array.from(document.querySelectorAll('a[href], area[href], [data-href]'))
             .map((a) => a.href || a.getAttribute('data-href'))
             .filter(Boolean)"""
    )
    return hrefs


async def sitemap_urls(request: APIRequestContext, seed: str) -> list[str]:
    """sitemap.xml, one level of sitemapindex deep."""
    parts = urlsplit(seed)
    root = urlunsplit((parts.scheme, parts.netloc, "/sitemap.xml", "", ""))
    found: list[str] = []
    queue = [root]
    seen: set[str] = set()
    while queue:
        url = queue.pop()
        if url in seen:
            continue
        seen.add(url)
        try:
            response = await request.get(url, timeout=10_000)
            if not response.ok:
                continue
            tree = ElementTree.fromstring(await response.text())
        except Exception:
            continue
        tag = tree.tag.rsplit("}", 1)[-1]
        for loc in tree.iter():
            if loc.tag.rsplit("}", 1)[-1] != "loc" or not loc.text:
                continue
            target = urljoin(url, loc.text.strip())
            if tag == "sitemapindex":
                queue.append(target)
            else:
                found.append(target)
    return found
