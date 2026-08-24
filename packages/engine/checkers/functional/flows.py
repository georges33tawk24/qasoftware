"""Catalogue group H — SPEC §8.4 H.

Pure over `flows/*/steps.json`. The flow already did the work and wrote down what
happened; this decides what it means and hands the reader the step list, the trace and
the video that came with it (SPEC §12.3).
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import FlowFailure, FlowRecord, FlowStatus
from engine.checkers.base import checker
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Evidence, EvidenceKind, Finding, Severity

SEVERITY = {
    # A journey nobody can complete.
    "valid-login-rejected": Severity.blocker,
    "wrong-password-accepted": Severity.blocker,
    "unknown-user-accepted": Severity.blocker,
    "empty-login-accepted": Severity.blocker,
    "logout-does-not-invalidate-session": Severity.blocker,
    "logout-does-not-clear-browser-session": Severity.blocker,
    "invalid-format-accepted": Severity.major,
    "session-lost-on-refresh": Severity.major,
    "total-does-not-match-line-items": Severity.blocker,
    "line-total-does-not-match": Severity.blocker,
    "unescaped-input-reflected": Severity.major,
    "submitted-markup-executed": Severity.critical,
    "server-error-on-input": Severity.major,
    "no-required-validation": Severity.major,
    "error-names-no-field": Severity.minor,
    "no-error-message": Severity.major,
    "no-way-to-sign-out": Severity.major,
    "no-empty-state": Severity.minor,
    "flow-error": Severity.major,
}
DEFAULT = Severity.major
"""Severity is set here, not left to escalation.

`escalate` is capped below `blocker` on purpose (see `issues.severity`) because page
counts and URL patterns cannot tell a broken journey from a widespread cosmetic one.
A checker that ran the journey and watched it fail can, so it says so outright: a session
that survives logout and a total that does not match its own line items are both
`blocker` wherever they happen, not only on a path that matches a regex.
"""


def _severity(failure: FlowFailure) -> Severity:
    hint = failure.data.get("severityHint")
    if isinstance(hint, str):
        try:
            return Severity(hint)
        except ValueError:
            pass
    return SEVERITY.get(failure.kind, DEFAULT)


def _evidence(flow: FlowRecord) -> list[Evidence]:
    """Everything the flow left behind, attached to the Issue (build prompt phase 6)."""
    base = f"flows/{flow.id}/"
    out: list[Evidence] = []
    for step in flow.steps:
        if step.screenshot:
            out.append(
                Evidence(
                    kind=EvidenceKind.screenshot,
                    path=base + step.screenshot,
                    caption=f"{step.n}. {step.text}",
                )
            )
    if flow.trace:
        out.append(
            Evidence(kind=EvidenceKind.trace, path=base + flow.trace, caption="Playwright trace")
        )
    if flow.video:
        out.append(Evidence(kind=EvidenceKind.video, path=base + flow.video, caption="Video"))
    out.append(
        Evidence(kind=EvidenceKind.steps, path=base + "steps.json", caption="Reproduction steps")
    )
    return out


def _steps(flow: FlowRecord) -> list[dict[str, object]]:
    """The numbered list, exactly as it was logged. Never written by hand (SPEC §12.3)."""
    return [
        {"n": step.n, "text": step.text, "url": step.url, "status": step.status.value}
        for step in flow.steps
    ]


@checker
class FunctionalFlows:
    id = "functional.flows"
    category = Category.functional
    requires = frozenset({Capability.FLOWS})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        pages = {page.id: page for page in live_pages(ctx)}
        seed = min(pages.values(), key=lambda p: p.depth) if pages else None
        for flow in ctx.flows():
            if flow.status is FlowStatus.passed or flow.status is FlowStatus.skipped:
                continue
            page = pages.get(flow.pageId or "") or seed
            if page is None:
                continue
            evidence = _evidence(flow)
            for failure in flow.failures:
                yield page_finding(
                    self,
                    page,
                    kind=failure.kind,
                    title=failure.message,
                    description=(
                        f"Found by the flow “{flow.name}” as {flow.persona}, on attempt "
                        f"{flow.attempts} of {ctx.manifest.config.flowRetries + 1}. "
                        "The steps below are the log of what was actually done."
                    ),
                    expected=failure.expected,
                    actual=failure.actual,
                    severity=_severity(failure),
                    stable_key=synthetic_key(self.id, flow.id, failure.kind),
                    evidence=evidence,
                    groupAs=f"{flow.kind}:{failure.kind}",
                    data={
                        "flow": flow.name,
                        "flowId": flow.id,
                        "persona": flow.persona,
                        "attempts": flow.attempts,
                        "failedAtStep": failure.step,
                        "steps": _steps(flow),
                        **failure.data,
                    },
                )


@checker
class FlakyFlows:
    id = "functional.flaky"
    category = Category.functional
    requires = frozenset({Capability.FLOWS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        """A flow that only passed on a retry is worth knowing about — quietly.

        SPEC §5 drops findings that do not reproduce, which is right; but a journey that
        needs two attempts is still telling you something.
        """
        pages = {page.id: page for page in live_pages(ctx)}
        for flow in ctx.flows():
            if flow.status is not FlowStatus.passed or flow.attempts <= 1:
                continue
            page = pages.get(flow.pageId or "")
            if page is None:
                continue
            yield page_finding(
                self,
                page,
                kind="flow-needed-a-retry",
                title=f"“{flow.name}” only passed on attempt {flow.attempts}",
                description="It failed and then passed unchanged, so something here is "
                "timing-dependent.",
                expected="passes first time",
                actual=f"passed on attempt {flow.attempts}",
                severity=Severity.minor,
                stable_key=synthetic_key(self.id, flow.id),
                groupAs="retry",
                data={"flow": flow.name, "attempts": flow.attempts},
            )
