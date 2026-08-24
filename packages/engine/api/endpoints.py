"""Endpoints derived from the network capture — SPEC §8.4 I.

Pure over the artifact: we already know every endpoint the site called during the crawl,
so there is nothing to discover and nothing to guess.
"""

from __future__ import annotations

import re
from hashlib import sha1
from urllib.parse import urlsplit

from engine.artifact.context import RunContext
from engine.artifact.models import Endpoint
from engine.checkers.support import live_pages

API_TYPES = frozenset({"xhr", "fetch"})
JSON_HINTS = ("application/json", "+json", "application/ld+json")

_NUMERIC = re.compile(r"^\d+$")
_HEX = re.compile(r"^[0-9a-f]{8,}$", re.IGNORECASE)
_UUID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE)


def template(url: str) -> str:
    """`/api/orders/10482` and `/api/orders/10483` are one endpoint."""
    parts = urlsplit(url)
    segments = [
        "{id}" if (_NUMERIC.match(s) or _UUID.match(s) or _HEX.match(s)) else s
        for s in parts.path.split("/")
    ]
    return "/".join(segments) or "/"


def looks_like_api(entry_type: str, content_type: str | None, url: str) -> bool:
    if entry_type in API_TYPES:
        return True
    lowered = (content_type or "").casefold()
    if any(hint in lowered for hint in JSON_HINTS):
        return True
    return "/api/" in url.casefold()


def derive(ctx: RunContext) -> list[Endpoint]:
    found: dict[str, Endpoint] = {}
    for page in live_pages(ctx):
        for entry in ctx.network(page.id):
            content_type = entry.resHeaders.get("content-type")
            if entry.url == page.url or not looks_like_api(entry.type, content_type, entry.url):
                continue
            shape = template(entry.url)
            key = f"{entry.method.upper()} {shape}"
            endpoint = found.get(key)
            if endpoint is None:
                endpoint = Endpoint(
                    id="ep_" + sha1(key.encode()).hexdigest()[:10],
                    method=entry.method.upper(),
                    template=shape,
                    sampleUrl=entry.url,
                    type=entry.type,
                    status=entry.status,
                    requestContentType=entry.reqHeaders.get("content-type"),
                    responseContentType=content_type,
                    hasAuthHeader=any(
                        name in entry.reqHeaders
                        for name in ("authorization", "x-api-key", "x-auth-token")
                    )
                    or "cookie" in entry.reqHeaders,
                )
                found[key] = endpoint
            if page.id not in endpoint.seenOn:
                endpoint.seenOn.append(page.id)
    return [found[key] for key in sorted(found)]
