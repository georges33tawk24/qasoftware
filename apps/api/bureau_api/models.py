"""The control plane's tables — SPEC §17.

Deliberately a thin index *over* the run artifact, not a second copy of the truth. Every
measurement lives in `runs/{run_id}/` and stays there; these rows exist so the board can
be queried, the lifecycle can outlive a run, and a dismissal can be permanent.

The one exception is `Issue.payload`: the neutral issue record is copied in as JSONB so a
list of issues can be rendered without opening five artifacts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from sqlalchemy import Column, Index
from sqlalchemy.types import JSON
from sqlmodel import Field, SQLModel


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:12]}"


def now() -> datetime:
    return datetime.now(UTC)


def json_field(**kwargs: Any) -> Any:
    """JSONB on Postgres, JSON on SQLite — the same column either way."""
    return Field(default_factory=dict, sa_column=Column(JSON), **kwargs)


def json_list(**kwargs: Any) -> Any:
    return Field(default_factory=list, sa_column=Column(JSON), **kwargs)


class RunState(StrEnum):
    queued = "queued"
    running = "running"
    complete = "complete"
    failed = "failed"
    aborted = "aborted"


class Project(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("prj"), primary_key=True)
    name: str
    target: str
    createdAt: datetime = Field(default_factory=now)
    authorisedBy: str | None = None
    """SPEC scope discipline: no `authorisedBy`, no probing. Recorded per project so it
    is a decision someone made once, not a flag someone passed once."""

    figmaFileKey: str | None = None
    figmaTokenRef: str | None = None
    """A *reference* — `env:ACME_FIGMA_TOKEN` or `keychain:figma/acme` — resolved at run
    time and never stored. Per project, because the file belongs to the client, not to
    whoever runs this: two projects mean two design files and two tokens."""

    modelTokenRef: str | None = None
    """Optional per-project override for the model key. Unset means the deployment's own
    key, which is the usual arrangement — whoever runs Bureau pays for the models."""

    provider: str | None = None
    """`anthropic`, `google` or `openai`. Unset means whichever key the deployment has."""

    config: dict[str, Any] = json_field()
    figmaFrames: dict[str, Any] = json_field()
    archived: bool = False


class Persona(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("per"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    name: str
    config: dict[str, Any] = json_field()
    """Selectors and secret *references* only. A credential never reaches this table
    (CLAUDE.md)."""

    createdAt: datetime = Field(default_factory=now)


class Run(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("run"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    state: RunState = Field(default=RunState.queued, index=True)
    artifactRunId: str | None = None
    artifactPath: str | None = None
    baseRunId: str | None = None
    queuedAt: datetime = Field(default_factory=now)
    startedAt: datetime | None = None
    finishedAt: datetime | None = None
    pages: int = 0
    issues: int = 0
    counts: dict[str, Any] = json_field()
    notes: dict[str, Any] = json_field()
    error: str | None = None
    triggeredBy: str | None = None


class IssueState(StrEnum):
    new = "new"
    confirmed = "confirmed"
    fixed = "fixed"
    regressed = "regressed"
    dismissed = "dismissed"
    wont_fix = "wont_fix"


class Issue(SQLModel, table=True):
    """One issue's life across runs, keyed by its fingerprint within a project."""

    __table_args__ = (Index("ix_issue_project_fingerprint", "projectId", "fingerprint"),)

    id: str = Field(default_factory=lambda: _id("iss"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    fingerprint: str = Field(index=True)
    checkerId: str = Field(index=True)
    issueKind: str
    category: str = Field(index=True)
    severity: str = Field(index=True)
    defaultSeverity: str = ""
    state: IssueState = Field(default=IssueState.new, index=True)
    title: str
    firstSeenRunId: str | None = None
    lastSeenRunId: str | None = None
    firstSeenAt: datetime = Field(default_factory=now)
    lastSeenAt: datetime = Field(default_factory=now)
    instanceCount: int = 0
    payload: dict[str, Any] = json_field()
    presentLastRun: bool = True
    flips: int = 0
    """How many times this has changed between present and absent across runs."""

    flaky: bool = Field(default=False, index=True)
    """SPEC §10 hardening: it appeared, went away, and came back. Grouped apart from
    everything else and never reported as a regression, because a finding that comes and
    goes on an unchanged site is telling you about the checker or the site's own
    variance, not about a change someone made."""

    assignee: str | None = None
    dueDate: datetime | None = None
    labels: list[str] = json_list()
    dismissedAt: datetime | None = None
    dismissedBy: str | None = None
    dismissedReason: str | None = None
    remoteKeys: dict[str, Any] = json_field()
    """Where this issue lives in someone else's tracker, so a second export updates
    rather than duplicates (SPEC §14)."""


class Comment(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("cmt"), primary_key=True)
    issueId: str = Field(foreign_key="issue.id", index=True)
    author: str
    body: str
    createdAt: datetime = Field(default_factory=now)
    knowledgeId: str | None = None
    """SPEC §13: a developer's "ignore this, the client changed it" becomes a knowledge
    draft so the next run already knows — once someone confirms it."""


class Knowledge(SQLModel, table=True):
    id: str = Field(default_factory=lambda: _id("kn"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    raw: str
    entries: list[dict[str, Any]] = json_list()
    confirmed: bool = False
    """Never silently applied: a model turns free text into entries and a human confirms
    them before the run starts (SPEC §10)."""

    createdBy: str | None = None
    createdAt: datetime = Field(default_factory=now)
    source: str = "run-form"
    """Which of SPEC §10's three input surfaces this came from: the run form, a board
    comment, or the reason attached to a dismissal."""

    archived: bool = False


class ExportTarget(SQLModel, table=True):
    """Where a project's issues get pushed — SPEC §14.

    `config` holds the field mapping and the *name* of the environment variable holding
    the token. Never the token (CLAUDE.md).
    """

    id: str = Field(default_factory=lambda: _id("exp"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    kind: str
    name: str = ""
    config: dict[str, Any] = json_field()
    enabled: bool = True
    createdAt: datetime = Field(default_factory=now)
    lastExportedAt: datetime | None = None


class Schedule(SQLModel, table=True):
    """A crontab expression and a timezone — SPEC §15."""

    id: str = Field(default_factory=lambda: _id("sch"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    expression: str
    timezone: str = "UTC"
    enabled: bool = True
    lastFiredAt: datetime | None = None
    nextFireAt: datetime | None = None
    createdAt: datetime = Field(default_factory=now)


class NotifyChannel(SQLModel, table=True):
    """Slack, email or webhook — SPEC §15. Fires only on new or regressed."""

    id: str = Field(default_factory=lambda: _id("chn"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    kind: str
    config: dict[str, Any] = json_field()
    minSeverity: str | None = None
    """`None` means anything new. Otherwise the least severe thing worth interrupting
    someone for."""

    enabled: bool = True
    createdAt: datetime = Field(default_factory=now)
    lastSentAt: datetime | None = None


class Recording(SQLModel, table=True):
    """A journey someone clicked through once — SPEC §15's record-a-flow."""

    id: str = Field(default_factory=lambda: _id("rec"), primary_key=True)
    projectId: str = Field(foreign_key="project.id", index=True)
    name: str
    persona: str = "anonymous"
    steps: list[dict[str, Any]] = json_list()
    script: str = ""
    """The generated Playwright script, kept so a person can read what will run."""

    enabled: bool = True
    createdAt: datetime = Field(default_factory=now)
    createdBy: str | None = None
