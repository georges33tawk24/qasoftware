"""Fingerprinting — SPEC §8.2.

Every dismissal in the product hangs off these two hashes. They must survive re-renders,
content edits and coordinate shifts, and must contain nothing volatile: no coordinates,
no nth-child indices, no measured values, no timestamps, no run id.

Changing a recipe here means bumping `STABLE_KEY_VERSION` and writing a migration.
"""

from __future__ import annotations

import re
from hashlib import sha1
from urllib.parse import urlsplit

from engine.artifact.models import ElementRecord

STABLE_KEY_VERSION = 1

_SEP = "\x1f"
"""Unit separator between parts. The spec writes `a + b`; a separator is the same idea
without letting ("di", "va") collide with ("div", "a")."""

_WHITESPACE = re.compile(r"\s+")
_POSITIONAL = re.compile(
    r":nth-(?:last-)?(?:child|of-type)\([^)]*\)|:(?:first|last|only)-(?:child|of-type)"
)


def normalise_text(text: str | None, limit: int) -> str:
    """Collapse whitespace, strip, casefold, truncate. Casefolding means a copy edit
    from 'Sign In' to 'Sign in' does not orphan a dismissal."""
    return _WHITESPACE.sub(" ", text or "").strip().casefold()[:limit]


def ancestor_shape(selector: str) -> str:
    """The tag.class chain above an element, with positional indices stripped.

    `main > section:nth-of-type(2) > div.card:nth-child(3)` → `main > section`

    The element's own compound selector is dropped: its tag, role, text and testId are
    already in the key, and its own classes are the ones most likely to toggle
    (`is-active`, `card--featured`). Ancestors are the stable part.
    """
    tokens = _WHITESPACE.sub(" ", _POSITIONAL.sub("", selector)).strip().split(" ")
    tokens = [t for t in tokens if t]
    while tokens and tokens[-1] in (">", "+", "~"):
        tokens.pop()
    if tokens:
        tokens.pop()  # the element itself
    while tokens and tokens[-1] in (">", "+", "~"):
        tokens.pop()
    return " ".join(tokens)


def normalise_path(url_or_path: str) -> str:
    """Path only: no scheme, no host, no query, no fragment, no trailing slash."""
    path = urlsplit(url_or_path).path or "/"
    return path.rstrip("/") or "/"


def _sha1(parts: list[str]) -> str:
    # sha1 is an identity function here, not a security control.
    return sha1(_SEP.join(parts).encode()).hexdigest()


def element_stable_key(el: ElementRecord) -> str:
    """SPEC §8.2. Survives DOM churn: no coordinates, no sibling indices."""
    return _sha1(
        [
            el.tag.casefold(),
            (el.role or "").casefold(),
            normalise_text(el.text, 60),
            ancestor_shape(el.selector),
            normalise_text(el.nearestHeading, 40),
            el.testId or "",
        ]
    )


def issue_fingerprint(
    *,
    checker_id: str,
    page_path: str,
    viewport: str,
    stable_key: str,
    issue_kind: str,
) -> str:
    """SPEC §8.2. The *values* are deliberately excluded: a colour that is wrong and
    stays wrong with a different wrong value is the same issue."""
    return _sha1([checker_id, normalise_path(page_path), viewport, stable_key, issue_kind])
