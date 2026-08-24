"""Free text to structured entries — SPEC §10.

The model's only job here is transcription: a person says "the CTA is green now and the
testimonials are gone for this release" and this turns it into entries someone can read
and correct. Nothing is applied until a human confirms it, which is why this returns a
draft `Note` with `confirmed=False` and no code path sets that flag.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

from engine.agents.config import AgentConfig, Tier
from engine.agents.parsing import parse_list
from engine.agents.provider import LLMProvider, ProviderError, Request
from engine.knowledge.models import Entry, EntryKind, Note

SYSTEM = (Path(__file__).parent / "prompt.md").read_text()
MAX_TOKENS = 1200
MAX_ENTRIES = 20
"""A paragraph of notes is a handful of decisions. Anything longer is a model looping."""


def parse_note(
    raw: str,
    provider: LLMProvider,
    config: AgentConfig | None = None,
    *,
    created_by: str | None = None,
    source: Literal["run-form", "comment", "dismissal"] = "run-form",
) -> Note:
    """A draft. `confirmed` stays False — SPEC §10 is explicit that nothing is applied
    until someone has read it back."""
    note = Note(raw=raw, createdBy=created_by, source=source)
    text = (raw or "").strip()
    if not text:
        return note

    config = config or AgentConfig()
    spec = config.for_tier(Tier.cheap, "knowledge")
    request = Request(system=SYSTEM, prompt=text, max_tokens=MAX_TOKENS, label="knowledge:parse")
    try:
        response = provider.complete(request, spec)
    except ProviderError:
        return note

    note.entries = entries_from(parse_list(response.text))
    return note


def entries_from(records: list[dict[str, object]]) -> list[Entry]:
    """Whatever survives validation. A malformed entry is dropped, never guessed at."""
    out: list[Entry] = []
    for record in records[:MAX_ENTRIES]:
        entry = _entry(record)
        if entry is not None:
            out.append(entry)
    return out


def _entry(record: dict[str, object]) -> Entry | None:
    from engine.knowledge.models import SCOPE_PREFIXES

    scope = str(record.get("scope") or "").strip()
    if not scope.startswith(SCOPE_PREFIXES) or not scope.split(":", 1)[1].strip():
        return None
    try:
        kind = EntryKind(str(record.get("kind") or "").strip())
    except ValueError:
        return None
    prop = record.get("property")
    expected = record.get("expected")
    entry = Entry(
        kind=kind,
        scope=scope,
        property=str(prop) if prop else None,
        expected=str(expected) if expected is not None else None,
        note=str(record.get("note") or ""),
        assertPresence=bool(record.get("assertPresence", True)),
    )
    if kind is EntryKind.ignore:
        # An ignore makes no claim about the site, so it cannot be confirmed present. The
        # model is told this; enforcing it here means a stray `true` cannot produce a
        # report line that says something was checked when it was not.
        entry.assertPresence = False
    if kind is EntryKind.override and not (entry.property and entry.expected):
        return None
    return entry
