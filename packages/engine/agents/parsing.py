"""Defensive JSON parsing — SPEC §9.3.

Models are asked for strict JSON and mostly give it. The failures are boringly
predictable — a fenced block, a sentence of preamble, a trailing comma — so they are
handled here rather than costing a retry each. What cannot be recovered is dropped: a
candidate we cannot read is a candidate we cannot ground.
"""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"^\s*```(?:json|jsonc)?\s*|\s*```\s*$", re.IGNORECASE | re.MULTILINE)
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def strip_fences(text: str) -> str:
    return _FENCE.sub("", text).strip()


def _span(text: str) -> str | None:
    """The outermost JSON value in a response with prose around it."""
    starts = [i for i in (text.find("["), text.find("{")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    closer = "]" if text[start] == "[" else "}"
    end = text.rfind(closer)
    return text[start : end + 1] if end > start else None


def parse(text: str) -> Any | None:
    """Return the parsed value, or None if this response cannot be read at all."""
    cleaned = strip_fences(text or "")
    for candidate in (cleaned, _span(cleaned)):
        if not candidate:
            continue
        for attempt in (candidate, _TRAILING_COMMA.sub(r"\1", candidate)):
            try:
                return json.loads(attempt)
            except json.JSONDecodeError:
                continue
    return None


def parse_list(text: str) -> list[dict[str, Any]]:
    """A list of objects, whatever shape the model wrapped it in."""
    value = parse(text)
    if isinstance(value, dict):
        for key in ("candidates", "findings", "issues", "results", "items"):
            if isinstance(value.get(key), list):
                value = value[key]
                break
        else:
            value = [value]
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def parse_object(text: str) -> dict[str, Any] | None:
    value = parse(text)
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return value[0]
    return value if isinstance(value, dict) else None
