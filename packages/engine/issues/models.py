"""The neutral issue format — SPEC §8.2, §8.3, §11, §12.1.

One format. Checkers emit `Finding`s, grouping turns them into `Issue`s, exporters map
`Issue`s. Nothing downstream of a checker knows which checker produced a thing.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from engine.artifact.models import Box
from engine.issues.fingerprint import STABLE_KEY_VERSION, issue_fingerprint


class Severity(StrEnum):
    blocker = "blocker"
    critical = "critical"
    major = "major"
    minor = "minor"
    trivial = "trivial"

    @property
    def rank(self) -> int:
        """0 is worst. Sort ascending to get SPEC §11 order."""
        return _SEVERITY_ORDER.index(self)


_SEVERITY_ORDER = [
    Severity.blocker,
    Severity.critical,
    Severity.major,
    Severity.minor,
    Severity.trivial,
]

AI_SEVERITY_CEILING = Severity.major
"""SPEC §8.3: the AI layer never raises severity above `major` on its own."""


class Category(StrEnum):
    """SPEC §8.4 groups, one per checker package."""

    free = "free"
    layout = "layout"
    typography = "typography"
    content = "content"
    a11y = "a11y"
    responsive = "responsive"
    performance = "performance"
    functional = "functional"
    api = "api"
    figma = "figma"
    ai = "ai"


class Status(StrEnum):
    """SPEC §11 lifecycle. `dismissed` is forever, on every future run."""

    new = "new"
    confirmed = "confirmed"
    fixed = "fixed"
    regressed = "regressed"
    dismissed = "dismissed"
    wont_fix = "wont_fix"


class Source(StrEnum):
    """The badge in the report. `verified` means an AI candidate the verifier confirmed."""

    measured = "measured"
    ai = "ai"
    verified = "verified"


class EvidenceKind(StrEnum):
    screenshot = "screenshot"
    crop = "crop"
    side_by_side = "side_by_side"
    trace = "trace"
    video = "video"
    steps = "steps"


class IssueModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Evidence(IssueModel):
    kind: EvidenceKind
    path: str
    """Relative to the run directory, POSIX separators."""
    caption: str | None = None
    box: Box | None = None


class Instance(IssueModel):
    """One occurrence. Ten cards with the same defect are ten instances of one issue."""

    fingerprint: str
    """SPEC §8.2, per occurrence. This is the durable key: a dismissal recorded against
    it survives regrouping, because grouping can change when a site changes."""

    pageId: str
    pagePath: str
    viewport: str
    elementId: str | None = None
    stableKey: str
    selector: str | None = None
    box: Box | None = None
    actual: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)


class Finding(IssueModel):
    """What a checker yields. Pre-grouping, one per occurrence."""

    checkerId: str
    issueKind: str
    category: Category
    severity: Severity
    title: str
    description: str = ""
    expected: str | None = None
    actual: str | None = None

    pageId: str
    pagePath: str
    viewport: str
    elementId: str | None = None
    stableKey: str
    selector: str | None = None
    box: Box | None = None

    groupAs: str | None = None
    """Overrides (expected, actual) as the grouping key. Measurement checkers need this:
    ten tap targets are ten instances of one issue, but their measured sizes all differ,
    so grouping on `actual` would produce ten issues (SPEC §11)."""

    source: Source = Source.measured
    confidence: float | None = None
    agent: str | None = None
    evidence: list[Evidence] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)
    """The measurements behind the finding. The report shows these; the AI layer is
    grounded on them."""

    # Derived, deliberately not serialised: a persisted copy could disagree with the
    # fields it came from, and `extra="forbid"` would then refuse to read it back.
    @property
    def fingerprint(self) -> str:
        return issue_fingerprint(
            checker_id=self.checkerId,
            page_path=self.pagePath,
            viewport=self.viewport,
            stable_key=self.stableKey,
            issue_kind=self.issueKind,
        )

    def as_instance(self) -> Instance:
        return Instance(
            fingerprint=self.fingerprint,
            pageId=self.pageId,
            pagePath=self.pagePath,
            viewport=self.viewport,
            elementId=self.elementId,
            stableKey=self.stableKey,
            selector=self.selector,
            box=self.box,
            actual=self.actual,
            evidence=list(self.evidence),
        )


class Issue(IssueModel):
    """A group of findings that share (checkerId, issueKind, expected, actual)."""

    id: str
    fingerprint: str
    """The *group* key: checker, kind, expected, actual and scope. Instances carry the
    per-occurrence fingerprint from SPEC §8.2, which is what dismissals hang off."""

    stableKeyV: int = STABLE_KEY_VERSION

    checkerId: str
    issueKind: str
    category: Category
    severity: Severity
    defaultSeverity: Severity
    status: Status = Status.new
    source: Source = Source.measured
    confidence: float | None = None
    agent: str | None = None

    title: str
    description: str = ""
    expected: str | None = None
    actual: str | None = None

    instances: list[Instance] = Field(default_factory=list)
    evidence: list[Evidence] = Field(default_factory=list)
    data: dict[str, Any] = Field(default_factory=dict)

    firstSeenRunId: str | None = None
    lastSeenRunId: str | None = None
    createdAt: datetime | None = None

    @property
    def instanceCount(self) -> int:
        return len(self.instances)

    @property
    def pagePaths(self) -> list[str]:
        return sorted({i.pagePath for i in self.instances})


class IssuesFile(IssueModel):
    """`issues.json` — the neutral record every exporter and report reads.

    `checkersSkipped` is not bookkeeping: SPEC §12.1's appendix has to show what *was*
    checked for anyone to trust what was not flagged.
    """

    runId: str
    generatedAt: datetime
    checkersRan: list[str] = Field(default_factory=list)
    checkersSkipped: dict[str, str] = Field(default_factory=dict)
    issues: list[Issue] = Field(default_factory=list)
