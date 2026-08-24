"""Run the deterministic sweep — SPEC §3 stage 4.

Exhaustive by default (SPEC §1.4): every registered checker runs on every run. The only
legitimate reason one does not is a capability the artifact lacks, and that gets recorded
so the report can say so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

from engine.artifact.context import RunContext
from engine.artifact.store import RunPaths, write_bytes
from engine.checkers.base import discover, registry
from engine.issues.group import group
from engine.issues.models import Finding, Issue, IssuesFile


@dataclass
class CheckResult:
    issues: list[Issue] = field(default_factory=list)
    ran: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    findings: int = 0


def check(ctx: RunContext) -> CheckResult:
    discover()
    available = ctx.capabilities()
    findings: list[Finding] = []
    result = CheckResult()

    for checker_id, checker in sorted(registry().items()):
        missing = set(checker.requires) - available
        if missing:
            result.skipped[checker_id] = "needs " + ", ".join(sorted(m.value for m in missing))
            continue
        result.ran.append(checker_id)
        findings.extend(checker.run(ctx))

    result.findings = len(findings)
    depths = {page.id: page.depth for page in ctx.pages()}
    result.issues = group(findings, run_id=ctx.run_id, depths=depths)
    return result


def write(paths: RunPaths, ctx: RunContext, result: CheckResult) -> IssuesFile:
    payload = IssuesFile(
        runId=ctx.run_id,
        generatedAt=datetime.now(UTC),
        checkersRan=result.ran,
        checkersSkipped=result.skipped,
        issues=result.issues,
    )
    write_bytes(paths.issues, payload.model_dump_json(indent=2).encode() + b"\n")
    return payload


def read(paths: RunPaths) -> IssuesFile:
    return IssuesFile.model_validate_json(paths.issues.read_bytes())
