"""Project knowledge in the control plane — SPEC §10.

Three surfaces feed it: the free-text box on the run form, a developer's comment on the
board, and the reason someone gave when dismissing an issue. All three land here as a
*draft* — parsed into entries, confirmed by nobody. A run only ever sees confirmed rows,
which is the whole point of §10's "never silently accept parsed intent".
"""

from __future__ import annotations

import os
from typing import Literal

from sqlmodel import Session, select

from bureau_api.models import Knowledge, Project
from engine.agents.provider import LLMProvider, ProviderError
from engine.agents.providers import build
from engine.capture.secrets import resolve as resolve_secret
from engine.knowledge.models import Entry, Note
from engine.knowledge.parse import parse_note

Source = Literal["run-form", "comment", "dismissal"]

PROVIDER_KEYS = (
    ("anthropic", "ANTHROPIC_API_KEY"),
    ("google", "GOOGLE_API_KEY"),
    ("openai", "OPENAI_API_KEY"),
)


_override: LLMProvider | None = None


def use(replacement: LLMProvider | None) -> None:
    """Swap the parser's provider. The tests script it; nothing else calls this."""
    global _override
    _override = replacement


def provider(project: Project | None = None) -> LLMProvider | None:
    """This project's provider, else the deployment's, else none.

    With no key the raw text is still stored and still shown for confirmation — someone
    can write the entries by hand. Losing the note entirely because a key is missing
    would be the worst of the available behaviours.
    """
    if _override is not None:
        return _override
    if project is not None and project.modelTokenRef:
        try:
            return build(
                project.provider or "anthropic", api_key=resolve_secret(project.modelTokenRef)
            )
        except Exception:
            pass
    name = os.environ.get("BUREAU_PROVIDER")
    if name:
        try:
            return build(name)
        except (ProviderError, Exception):
            return None
    for candidate, key in PROVIDER_KEYS:
        if os.environ.get(key):
            try:
                return build(candidate)
            except Exception:
                continue
    return None


def draft(
    session: Session,
    project_id: str,
    raw: str,
    *,
    created_by: str | None = None,
    source: Source = "run-form",
) -> Knowledge:
    """Parse free text into a row nobody has confirmed yet."""
    llm = provider(session.get(Project, project_id))
    note = (
        parse_note(raw, llm, created_by=created_by, source=source)
        if llm is not None
        else Note(raw=raw, createdBy=created_by, source=source)
    )
    row = Knowledge(
        projectId=project_id,
        raw=raw,
        entries=[e.model_dump(mode="json") for e in note.entries],
        createdBy=created_by,
        source=source,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def notes_for(session: Session, project_id: str) -> list[Note]:
    """The confirmed changelog, oldest first, as the engine's own shape."""
    rows = session.exec(
        select(Knowledge)
        .where(Knowledge.projectId == project_id)
        .where(Knowledge.confirmed == True)  # noqa: E712 - SQLModel needs the comparison
        .order_by(Knowledge.createdAt)  # type: ignore[arg-type]
    ).all()
    return [as_note(row) for row in rows]


def as_note(row: Knowledge) -> Note:
    return Note(
        id=row.id,
        raw=row.raw,
        entries=[Entry.model_validate(e) for e in (row.entries or [])],
        confirmed=row.confirmed,
        createdBy=row.createdBy,
        createdAt=row.createdAt,
        source=row.source,  # type: ignore[arg-type]  # the column is the same literal set
    )
