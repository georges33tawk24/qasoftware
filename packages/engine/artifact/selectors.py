"""Matching a CSS selector against a captured element.

Not a CSS engine, and not trying to be. The artifact stores each element's tag, classes,
id, text and the selector the capture computed for it; this reads the small subset of CSS
that people actually type into a config file — `.card`, `#hero`, `button.primary` — and
falls back to comparing against the recorded selector for anything more elaborate.

Shared by project knowledge (SPEC §10) and volatile masking (§5), because a selector
means the same thing to both and two implementations would drift.
"""

from __future__ import annotations

import re

from engine.artifact.models import ElementRecord

SIMPLE = re.compile(r"^(?P<tag>[a-zA-Z][\w-]*)?(?P<rest>(?:[.#][\w-]+)*)$")
TOKEN = re.compile(r"[.#][\w-]+")


def matches(selector: str, element: ElementRecord) -> bool:
    """Does this element match? Unreadable selectors match on the recorded string."""
    value = selector.strip()
    if not value:
        return False
    parsed = SIMPLE.match(value)
    if parsed is None:
        # `main > .card:nth-of-type(2)`, `[data-role=x]`, `:hover` — compared against the
        # selector the capture computed, which is exact enough to be useful and never
        # matches by accident.
        return value in (element.selector or "")
    tag = (parsed.group("tag") or "").lower()
    if tag and element.tag.lower() != tag:
        return False
    for token in TOKEN.findall(parsed.group("rest") or ""):
        name = token[1:]
        if token[0] == "." and name not in element.classes:
            return False
        if token[0] == "#" and element.htmlId != name:
            return False
    return bool(tag or parsed.group("rest"))


def any_matches(selectors: list[str], element: ElementRecord) -> bool:
    return any(matches(selector, element) for selector in selectors)
