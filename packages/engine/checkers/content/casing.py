"""Group D, casing that drifts across a set of controls — SPEC §8.4 D."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import ElementRecord
from engine.checkers.base import checker
from engine.checkers.support import Surface, element_finding, widest_surfaces
from engine.issues.models import Category, Finding, Severity

MIN_MEMBERS = 3
CONTROL_ROLES = frozenset({"link", "button", "menuitem", "tab"})


def casing_of(text: str) -> str | None:
    words = [w for w in text.split() if any(c.isalpha() for c in w)]
    if not words:
        return None
    letters = "".join(words)
    if letters.isupper():
        return "UPPERCASE"
    if letters.islower():
        return "lowercase"
    # Short connecting words are lower case in title case too, so judge on the rest.
    significant = [w for w in words if len(w) > 3]
    if significant and all(w[0].isupper() for w in significant):
        return "Title Case"
    if words[0][0].isupper():
        return "Sentence case"
    return None


@checker
class ControlCasing:
    id = "content.casing"
    category = Category.content
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in widest_surfaces(ctx):
            for parent_id, members in _control_groups(surface).items():
                if len(members) < MIN_MEMBERS:
                    continue
                styles = {m.id: casing_of(m.text) for m in members}
                counts = Counter(s for s in styles.values() if s)
                if len(counts) < 2:
                    continue
                expected, agreed = counts.most_common(1)[0]
                # A plurality, not a majority: a four-item nav with two offenders still
                # has one house style, and requiring 3-of-4 would miss every small nav.
                if agreed < 2:
                    continue
                for member in members:
                    style = styles.get(member.id)
                    if style is None or style == expected:
                        continue
                    yield element_finding(
                        self,
                        surface,
                        member,
                        kind="inconsistent-casing",
                        title=f"{style} among controls that are otherwise {expected}",
                        description=f"{agreed} of {sum(counts.values())} controls in this "
                        "group share a casing style.",
                        expected=expected,
                        actual=f"{style}: {member.text.strip()[:60]}",
                        data={"group": parent_id, "styles": dict(counts)},
                    )


def _control_groups(surface: Surface) -> dict[str, list[ElementRecord]]:
    """Group controls by the container they visually belong to.

    Grouping on the direct parent puts every `<li><a>` in a group of one, which is most
    navigation menus ever written. Walking past single-child wrappers finds the `<ul>`
    that actually holds the set.
    """
    groups: dict[str, list[ElementRecord]] = {}
    for element in surface.laid_out:
        if not element.text.strip():
            continue
        if element.role not in CONTROL_ROLES and not element.clickable:
            continue
        groups.setdefault(_container(surface, element), []).append(element)
    return groups


def _container(surface: Surface, element: ElementRecord) -> str:
    current = surface.by_id.get(element.parentId or "")
    depth = 0
    while current is not None and len(current.childIds) <= 1 and depth < 3:
        parent = surface.by_id.get(current.parentId or "")
        if parent is None:
            break
        current = parent
        depth += 1
    return current.id if current else "root"
