"""Project knowledge — SPEC §10.

Clients change things verbally and the Figma never gets updated. An entry records one
such change so that two things happen at once: the finding it explains stops being
reported, and the change itself becomes an assertion. "Requested change NOT applied: CTA
is still blue" is usually the most valuable line in a report.

An entry is *data*, parsed from free text and confirmed by a human. Nothing here decides
anything; `apply.py` does that, as a pure function over a run artifact.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import Field

from engine.artifact.models import ArtifactModel


class EntryKind(StrEnum):
    override = "override"
    """A property was changed on purpose: `.btn-primary` is green now, not blue."""

    removal = "removal"
    """Something in the design is gone on purpose: the testimonials section is deferred."""

    addition = "addition"
    """Something not in the design is there on purpose: a new banner."""

    ignore = "ignore"
    """A blanket "stop reporting this" with no assertion attached. The weakest kind, and
    the only one that cannot say whether it was applied — it is recorded as such rather
    than pretending to check."""


SCOPE_PREFIXES = ("selector:", "figma:", "page:", "checker:", "text:")
"""A scope names *where* an entry applies. Anything else is unusable, and an entry with
an unusable scope is rejected at parse time rather than silently matching nothing."""


class Entry(ArtifactModel):
    kind: EntryKind
    scope: str
    """One of `selector:.btn`, `figma:Testimonials`, `page:/checkout`,
    `checker:layout.spacing-scale`, `text:Sign in`."""

    property: str | None = None
    """A camelCase computed-style property for an `override`, e.g. `backgroundColor`."""

    expected: str | None = None
    note: str = ""
    assertPresence: bool = True
    """SPEC §10's "double duty". False only where the entry cannot be checked."""

    # Plain methods, not properties: the field is called `property` because that is the
    # name in SPEC §10's example, and the attribute has to match the wire shape.
    def scope_kind(self) -> str:
        return self.scope.split(":", 1)[0] if ":" in self.scope else ""

    def scope_value(self) -> str:
        return self.scope.split(":", 1)[1].strip() if ":" in self.scope else self.scope.strip()

    def describe(self) -> str:
        where = self.scope_value()
        if self.kind is EntryKind.override and self.property:
            return f"{where} {_spaced(self.property)} is {self.expected}"
        if self.kind is EntryKind.removal:
            return f"{where} is removed"
        if self.kind is EntryKind.addition:
            return f"{where} is present"
        return f"{where} is not reported"


def _spaced(camel: str) -> str:
    return "".join(f" {c.lower()}" if c.isupper() else c for c in camel).strip()


class Note(ArtifactModel):
    """One thing someone said, and what it was understood to mean."""

    id: str = ""
    raw: str
    entries: list[Entry] = Field(default_factory=list)
    confirmed: bool = False
    """Never silently applied (SPEC §10). A run only ever sees confirmed notes."""

    createdBy: str | None = None
    createdAt: datetime | None = None
    source: Literal["run-form", "comment", "dismissal"] = "run-form"


class Verdict(StrEnum):
    applied = "applied"
    not_applied = "not-applied"
    unverifiable = "unverifiable"
    """No element matched the scope at all, so the entry can say nothing either way —
    which is different from "not applied" and is reported differently."""


class RequestedChange(ArtifactModel):
    """One entry, checked against what was actually captured."""

    entry: Entry
    noteId: str = ""
    verdict: Verdict
    detail: str = ""
    matched: int = 0
    suppressed: int = 0
    pagePaths: list[str] = Field(default_factory=list)

    def headline(self) -> str:
        described = self.entry.describe()
        if self.verdict is Verdict.applied:
            return f"Requested change confirmed present: {described}"
        if self.verdict is Verdict.not_applied:
            return f"Requested change NOT applied: {described}"
        return f"Requested change could not be checked: {described}"


class KnowledgeFile(ArtifactModel):
    """`knowledge.json` beside the issues, so a re-read of an old run says the same."""

    notes: list[Note] = Field(default_factory=list)
    changes: list[RequestedChange] = Field(default_factory=list)
