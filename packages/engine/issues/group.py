"""Grouping — SPEC §11.

Ten cards with the same wrong background is one issue with ten instances, not ten
findings. Getting this wrong is what makes a report unreadable.
"""

from __future__ import annotations

from collections import defaultdict
from hashlib import sha1

from engine.issues.models import Finding, Issue, Status
from engine.issues.severity import escalate

CROSS_PAGE_SCOPE = "*"


def _family(finding: Finding) -> tuple[str, str, str]:
    """SPEC §11's grouping key: checker, kind, and the expected/actual pair — unless the
    checker supplied a coarser key because its measurements differ per instance."""
    detail = finding.groupAs
    if detail is None:
        detail = f"{finding.expected or ''}\x1f{finding.actual or ''}"
    return (finding.checkerId, finding.issueKind, detail)


def _issue_fingerprint(family: tuple[str, str, str], scope: str) -> str:
    return sha1("\x1f".join([*family, scope]).encode()).hexdigest()


class _Union:
    """Tiny union-find, so per-page buckets that share a component become one issue."""

    def __init__(self) -> None:
        self.parent: dict[int, int] = {}

    def find(self, item: int) -> int:
        self.parent.setdefault(item, item)
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, a: int, b: int) -> None:
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.parent[rb] = ra


def group(
    findings: list[Finding], *, run_id: str, depths: dict[str, int] | None = None
) -> list[Issue]:
    depths = depths or {}

    # 1. Within a page.
    per_page: dict[tuple[tuple[str, str, str], str], list[Finding]] = defaultdict(list)
    for finding in findings:
        per_page[(_family(finding), finding.pagePath)].append(finding)

    # 2. Across pages, when the same repeated component is at fault. Two per-page buckets
    #    belong together when they share an element stableKey — that is what "the same
    #    component" means once nth-child indices and coordinates are out of the key.
    by_family: dict[tuple[str, str, str], list[tuple[str, list[Finding]]]] = defaultdict(list)
    for (family, path), bucket in per_page.items():
        by_family[family].append((path, bucket))

    issues: list[Issue] = []
    for family, buckets in by_family.items():
        union = _Union()
        owner: dict[str, int] = {}
        for index, (_, bucket) in enumerate(buckets):
            union.find(index)
            for finding in bucket:
                previous = owner.setdefault(finding.stableKey, index)
                if previous != index:
                    union.union(previous, index)

        merged: dict[int, list[Finding]] = defaultdict(list)
        for index, (_, bucket) in enumerate(buckets):
            merged[union.find(index)].extend(bucket)

        for members in merged.values():
            issues.append(_build(family, members, run_id=run_id))

    return sort(issues, depths)


def _build(family: tuple[str, str, str], members: list[Finding], *, run_id: str) -> Issue:
    members = sorted(members, key=lambda f: (f.pagePath, f.viewport, f.fingerprint))
    lead = members[0]
    paths = sorted({f.pagePath for f in members})
    scope = paths[0] if len(paths) == 1 else CROSS_PAGE_SCOPE
    fingerprint = _issue_fingerprint(family, scope)

    severity = escalate(lead.severity, paths=paths)

    return Issue(
        id=f"iss_{fingerprint[:12]}",
        fingerprint=fingerprint,
        checkerId=lead.checkerId,
        issueKind=lead.issueKind,
        category=lead.category,
        severity=severity,
        defaultSeverity=lead.severity,
        status=Status.new,
        source=lead.source,
        confidence=lead.confidence,
        agent=lead.agent,
        title=lead.title,
        description=lead.description,
        expected=lead.expected,
        actual=lead.actual,
        instances=[f.as_instance() for f in members],
        evidence=list(lead.evidence),
        data=dict(lead.data),
        firstSeenRunId=run_id,
        lastSeenRunId=run_id,
    )


def sort(issues: list[Issue], depths: dict[str, int]) -> list[Issue]:
    """SPEC §11: severity, then instance count, then page depth. The broken login is
    never below a shadow variance."""

    def key(issue: Issue) -> tuple[int, int, int, str]:
        depth = min((depths.get(i.pageId, 0) for i in issue.instances), default=0)
        return (issue.severity.rank, -issue.instanceCount, depth, issue.fingerprint)

    return sorted(issues, key=key)
