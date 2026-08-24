"""Run diffing — SPEC §11.

Every run after the first is New / Still open / Fixed / Regressed, defaulting to New.
Regressed — fixed once, then back — is called out loudly, because those are the ones that
embarrass teams.

A pure function over two issue files. The engine can see what changed between two runs;
it cannot see that something was *once* fixed, because that is history rather than a
measurement, so the caller passes in the fingerprints it knows were fixed.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from engine.artifact.models import ArtifactModel
from engine.issues.models import IssuesFile


class Change(StrEnum):
    new = "new"
    still_open = "still-open"
    fixed = "fixed"
    regressed = "regressed"
    flaky = "flaky"
    """Appeared, vanished, came back. Its own bucket so it cannot be mistaken for either
    a regression or a fix."""


class DiffEntry(ArtifactModel):
    fingerprint: str
    change: Change
    title: str
    severity: str
    checkerId: str
    instanceCount: int = 0
    delta: int = 0
    """Instances gained or lost since the base run. A finding that doubled is worth
    noticing even when its state has not changed."""


class RunDiff(ArtifactModel):
    baseRunId: str = ""
    entries: list[DiffEntry] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = dict.fromkeys((c.value for c in Change), 0)
        for entry in self.entries:
            out[entry.change.value] += 1
        return out

    def of(self, change: Change) -> list[DiffEntry]:
        return [e for e in self.entries if e.change is change]


def diff(
    current: IssuesFile,
    base: IssuesFile | None,
    *,
    previously_fixed: set[str] | None = None,
    flaky: set[str] | None = None,
) -> RunDiff:
    """`previously_fixed` and `flaky` are what the caller's index remembers; a pair of
    artifacts cannot know either."""
    fixed_before = previously_fixed or set()
    intermittent = flaky or set()
    if base is None:
        # A first run has nothing to compare against, and calling everything "new" would
        # be technically true and completely useless.
        return RunDiff()

    before = {i.fingerprint: i for i in base.issues}
    entries: list[DiffEntry] = []

    for issue in current.issues:
        was = before.get(issue.fingerprint)
        if issue.fingerprint in intermittent:
            change = Change.flaky
        elif was is None:
            change = Change.regressed if issue.fingerprint in fixed_before else Change.new
        else:
            change = Change.still_open
        entries.append(
            DiffEntry(
                fingerprint=issue.fingerprint,
                change=change,
                title=issue.title,
                severity=issue.severity.value,
                checkerId=issue.checkerId,
                instanceCount=issue.instanceCount,
                delta=issue.instanceCount - (was.instanceCount if was else 0),
            )
        )

    seen = {i.fingerprint for i in current.issues}
    for issue in base.issues:
        if issue.fingerprint in seen or issue.fingerprint in intermittent:
            continue
        entries.append(
            DiffEntry(
                fingerprint=issue.fingerprint,
                change=Change.fixed,
                title=issue.title,
                severity=issue.severity.value,
                checkerId=issue.checkerId,
                instanceCount=0,
                delta=-issue.instanceCount,
            )
        )

    order = {
        Change.regressed: 0,
        Change.new: 1,
        Change.still_open: 2,
        Change.fixed: 3,
        Change.flaky: 4,
    }
    severities = ["blocker", "critical", "major", "minor", "trivial"]
    entries.sort(
        key=lambda e: (
            order[e.change],
            severities.index(e.severity) if e.severity in severities else len(severities),
            -e.instanceCount,
            e.title,
        )
    )
    return RunDiff(baseRunId=base.runId, entries=entries)
