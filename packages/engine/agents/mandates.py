"""The agent mandates — SPEC §9.2.

Running the same model five times gives you the same blind spots five times. Each mandate
here differs in three ways at once: a different system prompt, a different subset of the
measured facts, and — where the project configures one — a different model family.

`distinct()` asserts the first two, because "these prompts are basically the same" is a
thing that happens quietly over months of edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from engine.artifact.context import RunContext
from engine.checkers.support import Surface
from engine.issues.models import Category

PROMPTS = Path(__file__).parent / "prompts"


@lru_cache(maxsize=32)
def prompt(name: str) -> str:
    return (PROMPTS / f"{name}.md").read_text().strip()


@dataclass(frozen=True)
class Mandate:
    id: str
    title: str
    facts: tuple[str, ...]
    """Which blocks of the measured facts this agent is given, and only these."""

    category: Category = Category.ai
    requires_design: bool = False

    @property
    def system(self) -> str:
        return prompt(self.id)

    def applies(self, ctx: RunContext, surface: Surface) -> bool:
        if not self.requires_design:
            return True
        mapping = ctx.mapping(surface.page.id, surface.viewport.name)
        return mapping is not None and mapping.confident


MANDATES: tuple[Mandate, ...] = (
    Mandate(
        id="layout-critic",
        title="Layout critic",
        facts=("alignmentSets", "spacingHistogram", "repeatedGroups"),
    ),
    Mandate(
        id="typography-critic",
        title="Typography critic",
        facts=("typeInventory", "measure"),
    ),
    Mandate(
        # No geometry at all: an editor who can see the layout starts reviewing the
        # layout, and there is already a critic for that.
        id="copy-critic",
        title="Copy critic",
        facts=("textInventory",),
    ),
    Mandate(
        id="a11y-critic",
        title="Accessibility critic",
        facts=("structure", "linkInventory", "colourInventory"),
    ),
    Mandate(
        id="impatient-customer",
        title="Impatient customer",
        facts=("linkInventory", "siteMap"),
    ),
    Mandate(
        id="brand-critic",
        title="Brand critic",
        facts=("designTokens", "designDeltas", "colourInventory", "typeInventory"),
        requires_design=True,
    ),
)

BY_ID = {mandate.id: mandate for mandate in MANDATES}


def selected(names: list[str]) -> tuple[Mandate, ...]:
    if not names:
        return MANDATES
    unknown = [name for name in names if name not in BY_ID]
    if unknown:
        raise KeyError(f"unknown agent(s) {unknown}; have {sorted(BY_ID)}")
    return tuple(BY_ID[name] for name in names)


def distinct() -> bool:
    """No two mandates may share both a prompt and a set of facts.

    SPEC §9.2: if two agents' prompts could be swapped without anyone noticing, they are
    not differentiated enough — and two agents given the same facts will find the same
    things whatever their prompts say.
    """
    prompts = {mandate.system for mandate in MANDATES}
    facts = {mandate.facts for mandate in MANDATES}
    return len(prompts) == len(MANDATES) and len(facts) == len(MANDATES)
