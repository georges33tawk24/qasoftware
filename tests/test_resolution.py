"""Cross-checker resolution — SPEC §8.5.

The pass exists because one run reported `/.git/config is publicly readable` at
`critical` while separately reporting that the site answers 200 to everything.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from engine.artifact.context import RunContext
from engine.checkers import resolution
from engine.checkers.resolution import EXISTENCE_BASIS, resolve
from engine.fixtures import fixture_path
from engine.issues.models import Category, Finding, Severity


def finding(**over: object) -> Finding:
    fields: dict[str, object] = {
        "checkerId": "free.exposed-paths",
        "issueKind": "exposed-path",
        "category": Category.free,
        "severity": Severity.critical,
        "title": "/.git/config is publicly readable",
        "pageId": "p_home",
        "pagePath": "/",
        "viewport": "*",
        "stableKey": "k",
    }
    fields.update(over)
    return Finding(**fields)  # type: ignore[arg-type]


def soft_404() -> Finding:
    return finding(
        checkerId="free.not-found",
        issueKind="soft-404",
        title="A missing page returns 200",
        severity=Severity.major,
        stableKey="k404",
    )


@pytest.fixture
def ctx() -> RunContext:
    return RunContext.open(fixture_path("exercised"))


def test_a_status_only_claim_falls_to_a_soft_404(ctx: RunContext) -> None:
    claim = finding(data={EXISTENCE_BASIS: "status"})
    outcome = resolve([soft_404(), claim], ctx)
    assert claim not in outcome.kept
    assert [i.rule for i in outcome.invalidated] == ["existence-needs-more-than-a-status"]
    assert "200 to paths that do not exist" in outcome.notes()[0]


def test_a_claim_that_read_the_body_survives(ctx: RunContext) -> None:
    """The whole point of making the checker verify content: it stops being guesswork."""
    claim = finding(data={EXISTENCE_BASIS: "content"})
    outcome = resolve([soft_404(), claim], ctx)
    assert claim in outcome.kept
    assert outcome.invalidated == []


def test_without_a_soft_404_a_status_claim_stands(ctx: RunContext) -> None:
    claim = finding(data={EXISTENCE_BASIS: "status"})
    outcome = resolve([claim], ctx)
    assert claim in outcome.kept


def test_a_body_matching_the_404_page_is_withdrawn(tmp_path: Path, ctx: RunContext) -> None:
    probes = ctx.probes()
    assert probes is not None
    probes.paths[0].kind = "not-found-handling"
    probes.paths[0].bodyHash = "deadbeef"
    claim = finding(data={resolution.BODY_HASH: "deadbeef"})
    outcome = resolve([claim], ctx)
    assert claim not in outcome.kept
    assert outcome.invalidated[0].rule == "a-200-that-is-the-404-page-is-a-404"


def test_a_rule_matches_the_claim_not_the_checker(ctx: RunContext) -> None:
    """A new checker resting on the same basis is covered without touching the rule."""
    newcomer = finding(checkerId="free.something-invented-later", data={EXISTENCE_BASIS: "status"})
    outcome = resolve([soft_404(), newcomer], ctx)
    assert newcomer not in outcome.kept


def test_every_rule_has_a_distinct_id() -> None:
    assert len(resolution.registry()) == 2


def test_exposed_paths_rejects_html_shell_as_content() -> None:
    from engine.checkers.free.security import _looks_like

    html = '<!doctype html><html><head><meta charset="utf-8"></head><body>app</body></html>'
    assert not _looks_like("/.env", html)
    assert not _looks_like("/.git/config", html)
    assert not _looks_like("/config.json", html)
    assert _looks_like("/.env", "SECRET_KEY=12345\nDB_PASS=xyz")
    assert _looks_like("/.git/config", "[core]\nrepositoryformatversion = 0")
