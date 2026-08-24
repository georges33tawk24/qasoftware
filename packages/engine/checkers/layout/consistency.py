"""Group B, decoration that drifts inside a repeated component — SPEC §8.4 B."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import element_finding, surfaces
from engine.issues.models import Category, Finding, Severity

MIN_MEMBERS = 3
"""Two cards disagreeing has no majority to be wrong against."""

PROPERTIES: list[tuple[str, str, Callable[[ElementRecord], str]]] = [
    ("radius", "border radius", lambda e: ", ".join(f"{v:g}" for v in e.styles.borderRadius)),
    ("shadow", "box shadow", lambda e: e.styles.boxShadow),
    ("border-width", "border width", lambda e: ", ".join(f"{v:g}" for v in e.styles.borderWidth)),
    ("border-colour", "border colour", lambda e: e.styles.borderColor),
]


@checker
class RepeatedGroupConsistency:
    id = "layout.group-consistency"
    category = Category.layout
    requires = frozenset({Capability.LAYOUT})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for group in surface.layout.repeatedGroups:
                members = [surface.by_id[i] for i in group.elementIds if i in surface.by_id]
                if len(members) < MIN_MEMBERS:
                    continue
                for slug, label, read in PROPERTIES:
                    counts = Counter(read(member) for member in members)
                    if len(counts) < 2:
                        continue
                    expected, agreed = counts.most_common(1)[0]
                    if agreed < len(members) - agreed:
                        continue  # no majority, so nothing is "the odd one out"
                    for member in members:
                        value = read(member)
                        if value == expected:
                            continue
                        yield element_finding(
                            self,
                            surface,
                            member,
                            kind=f"inconsistent-{slug}",
                            title=f"{label.capitalize()} differs from the rest of the group",
                            description=(
                                f"{agreed} of {len(members)} × {group.signature} use {expected!r}."
                            ),
                            expected=expected,
                            actual=value,
                            data={"signature": group.signature, "property": slug},
                        )
