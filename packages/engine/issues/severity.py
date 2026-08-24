"""Automatic severity — SPEC §8.3.

Assigned automatically, always editable by a human, and never raised above `major` by
the AI layer on its own.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from engine.issues.models import _SEVERITY_ORDER, Severity

SENSITIVE_PATH = re.compile(
    r"/(checkout|cart|basket|payment|pay|billing|invoice|login|log-in|signin|sign-in"
    r"|signup|sign-up|register|account|auth|password|reset)(/|$|\.)",
    re.IGNORECASE,
)
"""Money and identity. A shadow variance here still matters more than one on a blog."""

WIDESPREAD_PAGES = 5
"""SPEC §8.3: the same finding on this many pages is a systemic problem, not a one-off."""

ESCALATION_CEILING = Severity.critical
"""Escalation may raise a severity but never to `blocker`.

`blocker` means a core journey is impossible, and no arithmetic over page counts and URL
patterns knows that. A tap target that is 2px too small on five pages of a checkout is
not the same thing as a login that does not work — but both rules fire, and without this
they compound into the same answer. A checker that knows the journey is broken sets
`blocker` itself.
"""


def bump(severity: Severity, steps: int = 1) -> Severity:
    index = max(0, _SEVERITY_ORDER.index(severity) - steps)
    return _SEVERITY_ORDER[index]


def on_sensitive_path(paths: Iterable[str]) -> bool:
    return any(SENSITIVE_PATH.search(path) for path in paths)


def escalate(severity: Severity, *, paths: Iterable[str]) -> Severity:
    """The two rules from §8.3, applied once at grouping time."""
    unique = sorted(set(paths))
    steps = 0
    if len(unique) >= WIDESPREAD_PAGES:
        steps += 1
    if on_sensitive_path(unique):
        steps += 1
    if not steps:
        return severity
    raised = bump(severity, steps)
    if raised.rank < ESCALATION_CEILING.rank <= severity.rank:
        return ESCALATION_CEILING
    return raised
