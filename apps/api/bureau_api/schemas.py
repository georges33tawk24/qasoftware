"""Request and response shapes for the control plane."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ProjectIn(BaseModel):
    name: str
    target: str
    authorisedBy: str | None = None
    figmaFileKey: str | None = None
    figmaTokenRef: str | None = None
    modelTokenRef: str | None = None
    provider: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)


class ProjectOut(BaseModel):
    id: str
    name: str
    target: str
    createdAt: datetime
    authorisedBy: str | None
    figmaFileKey: str | None
    figmaTokenRef: str | None
    modelTokenRef: str | None
    provider: str | None
    config: dict[str, Any]
    runs: int = 0
    openIssues: int = 0
    credentials: dict[str, str] = Field(default_factory=dict)
    """Whether each reference currently resolves — `ok`, `missing`, or why not. The
    *status*, never the value: a user needs to know their token is unset without the API
    ever handing a token back."""


class PersonaIn(BaseModel):
    name: str
    config: dict[str, Any] = Field(default_factory=dict)
    """Selectors and `env:` / `keychain:` references. A credential never comes through
    this endpoint (CLAUDE.md)."""


class PersonaOut(BaseModel):
    id: str
    name: str
    config: dict[str, Any]


class RunIn(BaseModel):
    baseRunId: str | None = None
    triggeredBy: str | None = None
    knowledge: str | None = None
    """The free-text box from SPEC §10. Parsed and confirmed in phase 8; carried with the
    run from here so the shape does not move."""


class RunOut(BaseModel):
    id: str
    projectId: str
    state: str
    queuedAt: datetime
    startedAt: datetime | None
    finishedAt: datetime | None
    pages: int
    issues: int
    counts: dict[str, Any]
    error: str | None
    artifactRunId: str | None
    baseRunId: str | None = None
    reportUrl: str | None = None
    diff: dict[str, int] = {}
    """SPEC §11's New / Still open / Fixed / Regressed counts, empty on a first run."""


class IssueOut(BaseModel):
    id: str
    fingerprint: str
    checkerId: str
    issueKind: str
    category: str
    severity: str
    state: str
    title: str
    instanceCount: int
    firstSeenRunId: str | None
    lastSeenRunId: str | None
    assignee: str | None
    dueDate: datetime | None
    labels: list[str]
    dismissedReason: str | None
    flaky: bool
    payload: dict[str, Any]


class IssueUpdate(BaseModel):
    state: str | None = None
    severity: str | None = None
    assignee: str | None = None
    dueDate: datetime | None = None
    labels: list[str] | None = None
    reason: str | None = None
    by: str | None = None
    intoKnowledge: bool = False
    """A dismissal with a reason is one of SPEC §10's input surfaces. Off by default:
    "this is a duplicate" is not project knowledge."""


class CommentIn(BaseModel):
    author: str
    body: str
    intoKnowledge: bool = False
    """SPEC §13's loop. The comment becomes a knowledge *draft*; §10 still wants a human
    to confirm it before a run uses it."""


class CommentOut(BaseModel):
    id: str
    issueId: str
    author: str
    body: str
    createdAt: datetime
    knowledgeId: str | None


class KnowledgeIn(BaseModel):
    raw: str
    createdBy: str | None = None


class KnowledgeUpdate(BaseModel):
    entries: list[dict[str, Any]] | None = None
    """What the human actually agreed to, which may not be what the model produced."""

    confirm: bool = False
    archived: bool | None = None


class KnowledgeOut(BaseModel):
    id: str
    raw: str
    entries: list[dict[str, Any]]
    confirmed: bool
    source: str
    archived: bool
    createdBy: str | None
    createdAt: datetime


class BoardColumn(BaseModel):
    state: str
    title: str
    issues: list[IssueOut]


class BoardOut(BaseModel):
    projectId: str
    columns: list[BoardColumn]
    assignees: list[str]
    labels: list[str]


# -------------------------------------------------- phase 9: delivery and CI


class ExportTargetIn(BaseModel):
    kind: str
    name: str = ""
    config: dict[str, Any] = {}
    enabled: bool = True


class ExportTargetOut(BaseModel):
    id: str
    projectId: str
    kind: str
    name: str
    config: dict[str, Any]
    enabled: bool
    lastExportedAt: datetime | None


class ExportRunIn(BaseModel):
    issueIds: list[str] | None = None
    """Omit to send everything exportable — new, confirmed and regressed."""


class ExportResultOut(BaseModel):
    fingerprint: str
    remoteKey: str
    url: str
    action: str
    error: str
    attachments: int


class ScheduleIn(BaseModel):
    expression: str
    timezone: str = "UTC"
    enabled: bool = True


class ScheduleOut(BaseModel):
    id: str
    projectId: str
    expression: str
    timezone: str
    enabled: bool
    lastFiredAt: datetime | None
    nextFireAt: datetime | None


class ChannelIn(BaseModel):
    kind: str
    config: dict[str, Any] = {}
    minSeverity: str | None = None
    enabled: bool = True


class ChannelOut(BaseModel):
    id: str
    projectId: str
    kind: str
    config: dict[str, Any]
    minSeverity: str | None
    enabled: bool
    lastSentAt: datetime | None


class CiRunIn(BaseModel):
    """SPEC §15's CI hook. A pipeline knows a URL, not a project id."""

    target: str
    name: str = ""
    baseRunId: str | None = None
    projectId: str | None = None
    config: dict[str, Any] = {}
    authorisedBy: str | None = None
    wait: float = 0.0
    """Seconds to block for. Zero returns as soon as the run is queued."""


class RecordingIn(BaseModel):
    name: str
    persona: str = "anonymous"
    script: str = ""
    steps: list[dict[str, Any]] = []
    enabled: bool = True
    createdBy: str | None = None


class RecordingOut(BaseModel):
    id: str
    projectId: str
    name: str
    persona: str
    steps: list[dict[str, Any]]
    script: str
    enabled: bool
    createdAt: datetime
    createdBy: str | None


class VolatileIn(BaseModel):
    url: str = ""
    """Defaults to the project's target."""

    viewport: str = "desktop_1440"
    loads: int = 2


class VolatileCandidate(BaseModel):
    selector: str
    kind: str
    detail: str


class VolatileOut(BaseModel):
    url: str
    viewport: str
    compared: int
    candidates: list[VolatileCandidate]
    selectors: list[str]
    """Ready to paste into `maskSelectors` — after a human has looked at them. A section
    that genuinely broke between two loads looks exactly like a carousel from here."""
