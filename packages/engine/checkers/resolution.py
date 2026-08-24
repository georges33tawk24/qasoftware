"""Findings that contradict each other — the pass between check and grouping.

A checker is a pure function over the artifact and cannot see what any other checker
found. That is exactly what makes the suite testable, and it is also how one run came to
report `/.git/config is publicly readable` at `critical` while, three findings away, it
reported that the same site answers 200 to every path that does not exist. The evidence
to stand down was already in the artifact. Nothing was allowed to read it.

So: after every checker has run and before anything is grouped, each rule gets the whole
list and says which findings its evidence kills. A rule matches on what a finding
*claims*, declared in `data`, never on which checker produced it — otherwise every new
checker that rests on the same shaky basis has to be added to the rule by hand.

Invalidated findings are kept and written to `resolution.json` rather than dropped on the
floor. "Why did the security sweep say nothing about .git?" needs an answer, and the
honest one is that another finding in the same run took it apart.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from engine.artifact.context import RunContext
from engine.issues.models import Finding

EXISTENCE_BASIS = "existenceBasis"
"""What a finding's "this resource exists" claim rests on.

`status` means nothing but a 2xx was seen, which is worth nothing on a site that answers
200 to everything. `content` means the body was actually looked at.
"""

BODY_HASH = "bodyHash"
"""Set by any finding whose evidence is a response body, so a rule can compare it against
the body the site serves for things that do not exist."""


@dataclass(frozen=True)
class Invalidation:
    finding: Finding
    rule: str
    reason: str


@dataclass
class Resolution:
    kept: list[Finding] = field(default_factory=list)
    invalidated: list[Invalidation] = field(default_factory=list)

    def notes(self) -> list[str]:
        return [
            f"{i.finding.checkerId}: {i.finding.title} — withdrawn by {i.rule}: {i.reason}"
            for i in self.invalidated
        ]


Rule = Callable[[list[Finding], RunContext], Iterable[Invalidation]]

_RULES: dict[str, Rule] = {}


def rule(identifier: str) -> Callable[[Rule], Rule]:
    def register(function: Rule) -> Rule:
        if identifier in _RULES:
            raise ValueError(f"duplicate resolution rule {identifier!r}")
        _RULES[identifier] = function
        return function

    return register


def registry() -> dict[str, Rule]:
    return dict(_RULES)


def soft_404_origins(findings: list[Finding]) -> bool:
    """Did anything in this run establish that missing paths come back 200?

    One origin per run today, so this is a yes or no. It becomes a set of origins the day
    a run crawls more than one host, and the callers below already read it as a gate.
    """
    return any(f.issueKind == "soft-404" for f in findings)


@rule("existence-needs-more-than-a-status")
def _existence_needs_more_than_a_status(
    findings: list[Finding], ctx: RunContext
) -> Iterable[Invalidation]:
    """A 200 proves a resource exists only on a site that 404s properly."""
    if not soft_404_origins(findings):
        return
    for finding in findings:
        if finding.data.get(EXISTENCE_BASIS) == "status":
            yield Invalidation(
                finding=finding,
                rule="existence-needs-more-than-a-status",
                reason="this site answers 200 to paths that do not exist, so a 200 is not "
                "evidence that this one does",
            )


@rule("a-200-that-is-the-404-page-is-a-404")
def _the_404_page_wearing_a_200(findings: list[Finding], ctx: RunContext) -> Iterable[Invalidation]:
    """The body came back identical to what the site serves for nothing at all."""
    probes = ctx.probes()
    if probes is None:
        return
    missing = next(
        (p.bodyHash for p in probes.paths if p.kind == "not-found-handling" and p.bodyHash),
        None,
    )
    if not missing:
        return
    for finding in findings:
        if finding.data.get(BODY_HASH) == missing:
            yield Invalidation(
                finding=finding,
                rule="a-200-that-is-the-404-page-is-a-404",
                reason="the response body is byte-for-byte what this site serves for a "
                "path that does not exist",
            )


def resolve(findings: list[Finding], ctx: RunContext) -> Resolution:
    """Apply every rule, then keep what survived all of them."""
    invalidated: list[Invalidation] = []
    seen: set[int] = set()
    for _, apply in sorted(_RULES.items()):
        for invalidation in apply(findings, ctx):
            if id(invalidation.finding) in seen:
                continue
            seen.add(id(invalidation.finding))
            invalidated.append(invalidation)
    return Resolution(
        kept=[f for f in findings if id(f) not in seen],
        invalidated=invalidated,
    )
