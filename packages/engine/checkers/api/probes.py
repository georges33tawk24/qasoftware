"""Catalogue group I — SPEC §8.4 I.

Pure over `api/probes.json`. The probe recorded what happened; this decides what it is
worth, which is the only place a severity judgement belongs.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import Endpoint, ProbeResult
from engine.checkers.base import checker
from engine.checkers.support import live_pages, page_finding, synthetic_key
from engine.issues.models import Category, Finding, Severity

TITLES = {
    "no-auth": (
        "Endpoint answers an unauthenticated request",
        Severity.critical,
        "It was called with credentials during the crawl and answered without them.",
    ),
    "cross-persona": (
        "One account can read another account's data",
        Severity.critical,
        "Two personas asked the same question and received byte-identical answers.",
    ),
    "malformed-input": (
        "Malformed input produces a server error",
        Severity.major,
        "A body with a wrong type in it should be refused, not crash the handler.",
    ),
    "method-tampering": (
        "A read endpoint accepts a write method",
        Severity.major,
        "DELETE was accepted where only GET was ever used.",
    ),
    "cors": (
        "CORS allows any origin with credentials",
        Severity.critical,
        "A wildcard origin combined with credentials lets any site read this signed-in response.",
    ),
    "rate-limit": (
        "No rate limiting on this endpoint",
        Severity.minor,
        "A short burst of identical requests was answered in full every time.",
    ),
    "personal-data": (
        "An anonymous request returns personal data",
        Severity.critical,
        "The values are not recorded here; only what kind of thing they were.",
    ),
    "latency": (
        "Endpoint is slower than the budget",
        Severity.minor,
        "Measured on a single warm request.",
    ),
}


@checker
class ApiProbes:
    id = "api.probes"
    category = Category.api
    requires = frozenset({Capability.API_PROBES})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        report = ctx.api()
        pages = {page.id: page for page in live_pages(ctx)}
        if report is None or not pages:
            return
        seed = min(pages.values(), key=lambda p: p.depth)
        endpoints = {endpoint.id: endpoint for endpoint in report.endpoints}

        for probe in report.probes:
            if not probe.finding:
                continue
            endpoint = endpoints.get(probe.endpointId)
            title, severity, explanation = TITLES.get(
                probe.probe, (f"API probe {probe.probe}", Severity.minor, "")
            )
            page = pages.get((endpoint.seenOn or [""])[0] if endpoint else "") or seed
            shape = endpoint.template if endpoint else probe.url
            yield page_finding(
                self,
                page,
                kind=f"api-{probe.probe}",
                title=f"{title}: {probe.method} {shape}",
                description=f"{explanation} {probe.detail}".strip(),
                expected="the request is refused",
                actual=probe.detail or str(probe.status),
                severity=severity,
                stable_key=synthetic_key(self.id, probe.probe, probe.method, shape),
                groupAs=probe.probe,
                data={
                    "endpoint": shape,
                    "method": probe.method,
                    "status": probe.status,
                    "authorisedBy": report.authorisedBy,
                    **probe.evidence,
                },
            )


@checker
class ApiNotProbed:
    id = "api.not-probed"
    category = Category.api
    requires = frozenset({Capability.API_PROBES})
    default_severity = Severity.trivial

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        """Say out loud when the API was left alone, and why.

        Silence about a check that did not run reads exactly like a check that passed.
        """
        report = ctx.api()
        pages = list(live_pages(ctx))
        if report is None or not pages or "*" not in report.skipped:
            return
        yield page_finding(
            self,
            min(pages, key=lambda p: p.depth),
            kind="api-not-probed",
            title=f"{len(report.endpoints)} endpoints were found and not probed",
            description=report.skipped["*"],
            expected="an authorised run",
            actual="no authorisation on the run config",
            severity=Severity.trivial,
            stable_key=synthetic_key(self.id, "unauthorised"),
            data={"endpoints": [e.template for e in report.endpoints]},
        )


def endpoint_summary(endpoints: list[Endpoint], probes: list[ProbeResult]) -> dict[str, int]:
    return {
        "endpoints": len(endpoints),
        "probes": len(probes),
        "findings": sum(1 for probe in probes if probe.finding),
    }
