"""Group E, axe-core — SPEC §8.4 E.

axe is injected during capture and its raw output is stored in the artifact. This maps
it into Issues. Reimplementing WCAG rules would be a worse version of a very well tested
library, so we do not (build prompt, phase 2).
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import Box
from engine.checkers.base import checker
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Finding, Severity

IMPACT_TO_SEVERITY = {
    "critical": Severity.major,
    "serious": Severity.major,
    "moderate": Severity.minor,
    "minor": Severity.trivial,
}
"""SPEC §8.3: a WCAG A/AA failure is `major`. Nothing axe reports is a blocker on its
own — a human decides that."""

_POSITIONAL = re.compile(r":nth-(?:last-)?(?:child|of-type)\([^)]*\)")
MAX_NODES_PER_RULE = 25


def _target(node: dict[str, Any]) -> str:
    target = node.get("target") or []
    if isinstance(target, list):
        target = target[-1] if target else ""
    return str(target)


@checker
class AxeViolations:
    id = "a11y.axe"
    category = Category.a11y
    requires = frozenset({Capability.AXE})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for page in live_pages(ctx):
            report = ctx.axe(page.id)
            if not report:
                continue
            for violation in report.get("violations", []):
                yield from self._rule(ctx, page, violation)

    def _rule(self, ctx: RunContext, page: Any, violation: dict[str, Any]) -> Iterable[Finding]:
        rule = str(violation.get("id", "unknown"))
        impact = str(violation.get("impact") or "moderate")
        tags = [t for t in violation.get("tags", []) if str(t).startswith("wcag")]
        nodes = violation.get("nodes", [])[:MAX_NODES_PER_RULE]
        for node in nodes:
            selector = _target(node)
            box = node.get("box")
            yield page_finding(
                self,
                page,
                kind=f"axe-{rule}",
                title=str(violation.get("help", rule)),
                description=str(violation.get("description", "")),
                expected=str(violation.get("help", "")),
                actual=(node.get("failureSummary") or "").strip()[:400] or selector,
                severity=IMPACT_TO_SEVERITY.get(impact, Severity.minor),
                groupAs=rule,
                stable_key=synthetic_key(self.id, rule, _POSITIONAL.sub("", selector)),
                selector=selector or None,
                box=Box(**box) if isinstance(box, dict) and "x" in box else None,
                data={
                    "rule": rule,
                    "impact": impact,
                    "wcag": tags,
                    "helpUrl": violation.get("helpUrl"),
                    "html": (node.get("html") or "")[:300],
                },
            )
