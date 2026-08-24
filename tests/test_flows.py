"""Functional flows and API probes — SPEC §8.4 H and I, §12.3.

The checkers run against `fixtures/exercised`, a frozen run of the fixture application.
The engine itself is exercised against that application live, behind the `browser` mark.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import pytest

from engine.api.authorisation import NotAuthorised, authorise
from engine.api.endpoints import derive, template
from engine.api.probes import PII_PATTERNS
from engine.artifact.context import RunContext
from engine.artifact.models import (
    VIEWPORT_PRESETS,
    FlowStatus,
    RunConfig,
    StepStatus,
)
from engine.artifact.store import RunPaths
from engine.capture.auth import Persona
from engine.capture.flows.discovery import classify, discover
from engine.checkers import runner
from engine.checkers.runner import CheckResult
from engine.fixtures import fixture_path, load_fixture
from engine.issues.models import EvidenceKind, Severity

FLOWS_MD = Path(__file__).parent / "fixtures" / "FLOWS.md"
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")


def documented() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for line in FLOWS_MD.read_text().splitlines():
        match = _ROW.match(line.strip())
        if match and match.group(1).startswith(("functional.", "api.")):
            rows.add((match.group(1), match.group(2)))
    return rows


@pytest.fixture(scope="module")
def checked() -> CheckResult:
    return runner.check(load_fixture("exercised"))


def found(result: CheckResult) -> set[tuple[str, str]]:
    return {
        (issue.checkerId, issue.issueKind)
        for issue in result.issues
        if issue.checkerId.startswith(("functional.", "api."))
    }


# ------------------------------------------------------------------- the contract


def test_every_planted_flow_defect_is_found(checked: CheckResult) -> None:
    missing = sorted(documented() - found(checked))
    assert not missing, "planted defects not found: " + ", ".join(f"{c}/{k}" for c, k in missing)


def test_nothing_undocumented_is_reported(checked: CheckResult) -> None:
    extra = sorted(found(checked) - documented())
    assert not extra, "findings with no row in FLOWS.md: " + ", ".join(f"{c}/{k}" for c, k in extra)


# ------------------------------------------------------- reproduction steps (§12.3)


def test_the_steps_are_the_log_not_a_description(checked: CheckResult) -> None:
    """Reproduction steps are never written by hand — they fall out of the log, and that
    is the whole point."""
    issue = next(i for i in checked.issues if i.issueKind == "logout-does-not-invalidate-session")
    steps = issue.data["steps"]
    assert len(steps) >= 8
    assert [step["n"] for step in steps] == list(range(1, len(steps) + 1))
    assert steps[0]["text"].startswith("Open ")
    assert all(step["url"] for step in steps)
    assert any("Sign out" in step["text"] for step in steps)


def test_a_screenshot_per_step_a_trace_and_a_video_are_attached(checked: CheckResult) -> None:
    issue = next(i for i in checked.issues if i.issueKind == "logout-does-not-invalidate-session")
    kinds = {evidence.kind for evidence in issue.evidence}
    assert EvidenceKind.screenshot in kinds
    assert EvidenceKind.trace in kinds
    assert EvidenceKind.video in kinds
    assert EvidenceKind.steps in kinds

    root = fixture_path("exercised")
    shots = [e for e in issue.evidence if e.kind is EvidenceKind.screenshot]
    assert len(shots) == len(issue.data["steps"])
    assert all((root / shot.path).is_file() for shot in shots)


def test_the_trace_is_a_real_playwright_trace() -> None:
    import zipfile

    root = fixture_path("exercised")
    traces = list(root.glob("flows/*/trace.zip"))
    assert traces, "no trace was kept in the frozen fixture"
    with zipfile.ZipFile(traces[0]) as archive:
        names = archive.namelist()
    assert any(name.endswith(".trace") for name in names), names[:5]


def test_a_password_never_reaches_the_step_list() -> None:
    """The step describes the action; the value is the user's, and on a password field it
    is a credential (CLAUDE.md)."""
    ctx = load_fixture("exercised")
    for flow in ctx.flows():
        for step in flow.steps:
            assert "correct-horse" not in step.text
            assert "battery-staple" not in step.text
        assert "Enter the password" in [s.text for s in flow.steps] or flow.kind != "auth"


def test_a_failed_step_is_marked(checked: CheckResult) -> None:
    ctx = load_fixture("exercised")
    statuses = {step.status for flow in ctx.flows() for step in flow.steps}
    assert StepStatus.ok in statuses


# -------------------------------------------------------------------- the retries


def test_a_failing_flow_is_retried_before_it_becomes_an_issue() -> None:
    """SPEC §5: roughly half of flaky findings vanish on the second attempt."""
    ctx = load_fixture("exercised")
    failures = [flow for flow in ctx.flows() if flow.status is FlowStatus.failed]
    assert failures
    assert all(flow.attempts == ctx.manifest.config.flowRetries + 1 for flow in failures)


def test_the_issue_says_how_many_attempts_it_took(checked: CheckResult) -> None:
    issue = next(i for i in checked.issues if i.checkerId == "functional.flows")
    assert "attempt 3 of 3" in issue.description


# ---------------------------------------------------------------------- severity


def test_a_broken_session_and_a_wrong_total_outrank_everything(checked: CheckResult) -> None:
    worst = {i.issueKind for i in checked.issues if i.severity is Severity.blocker}
    assert "logout-does-not-invalidate-session" in worst
    assert "total-does-not-match-line-items" in worst


def test_the_cart_total_was_worked_out_independently(checked: CheckResult) -> None:
    """SPEC §8.4 H: verify the line-item arithmetic rather than trusting the total. This
    is the class of bug worth the most to catch."""
    issue = next(i for i in checked.issues if i.issueKind == "total-does-not-match-line-items")
    assert issue.expected == "29.00"
    assert issue.actual == "27.00"
    assert issue.data["lines"]


# --------------------------------------------------------------------- discovery


def test_forms_are_discovered_from_the_artifact_alone() -> None:
    """No browser: `elements.json` already records every field's contract."""
    forms = discover(load_fixture("exercised"))
    kinds = {form.kind for form in forms}
    assert "login" in kinds
    login = next(form for form in forms if form.kind == "login")
    assert {f.info.type for f in login.fields} >= {"email", "password"}
    assert login.submit == "#login-submit"
    assert all(f.info.required for f in login.fields)


def test_a_password_value_is_never_captured() -> None:
    ctx = load_fixture("exercised")
    for page in ctx.pages():
        for viewport in ctx.viewport_names(page.id):
            for element in ctx.elements(page.id, viewport):
                if element.field and element.field.type == "password":
                    assert not hasattr(element.field, "value")


def test_forms_are_classified_by_their_fields() -> None:
    from engine.artifact.models import ElementRecord, FieldInfo
    from engine.capture.flows.discovery import Field

    def field(kind: str, name: str = "") -> Field:
        return Field(
            ElementRecord.model_validate(
                {
                    "id": "e",
                    "stableKey": "k",
                    "selector": "input",
                    "tag": "input",
                    "box": {"x": 0, "y": 0, "w": 1, "h": 1},
                    "boxViewport": {"x": 0, "y": 0, "w": 1, "h": 1},
                    "styles": {
                        "color": "rgb(0,0,0)",
                        "backgroundColor": "rgba(0, 0, 0, 0)",
                        "fontFamily": "x",
                        "fontSize": 16,
                        "fontWeight": 400,
                    },
                    "resolvedBackground": "rgb(255,255,255)",
                }
            ),
            FieldInfo(type=kind, name=name),
        )

    assert classify([field("email"), field("password")]) == "login"
    assert classify([field("text", "q")]) == "search"
    assert classify([field("text", "name"), field("email")]) == "generic"


# ------------------------------------------------------------------ authorisation


def test_probing_without_an_authoriser_is_refused() -> None:
    """Every project record carries an authorisedBy and the engine requires it."""
    with pytest.raises(NotAuthorised, match="authorisedBy"):
        authorise(RunConfig(), "https://example.test/")


def test_the_seed_host_is_authorised_by_being_the_target() -> None:
    authorisation = authorise(RunConfig(authorisedBy="Jo Blake"), "https://shop.example.test/cart")
    assert authorisation.allows("https://shop.example.test/api/items")
    assert not authorisation.allows("https://analytics.vendor.test/collect")


def test_an_unauthorised_host_is_refused_with_a_reason() -> None:
    authorisation = authorise(
        RunConfig(authorisedBy="Jo Blake", authorisedHosts=["a.test"]), "https://a.test/"
    )
    assert "not in the authorised host list" in authorisation.refuse("https://b.test/x")


def test_an_unauthorised_run_says_so_rather_than_staying_quiet(tmp_path: Path) -> None:
    """Silence about a check that did not run reads exactly like a check that passed."""
    import shutil

    root = tmp_path / "unauthorised"
    shutil.copytree(fixture_path("exercised"), root)
    paths = RunPaths(root)
    report = json.loads(paths.api_probes.read_text())
    report.update({"probes": [], "authorisedBy": None, "skipped": {"*": "no authorisedBy"}})
    paths.api_probes.write_text(json.dumps(report))

    result = runner.check(RunContext.open(root))
    kinds = {i.issueKind for i in result.issues if i.checkerId.startswith("api.")}
    assert kinds == {"api-not-probed"}


# --------------------------------------------------------------------- endpoints


def test_endpoints_come_from_the_crawl_not_from_guessing() -> None:
    endpoints = derive(load_fixture("exercised"))
    templates = {f"{e.method} {e.template}" for e in endpoints}
    assert "GET /api/items" in templates
    assert "GET /api/orders" in templates
    assert all(e.seenOn for e in endpoints)


def test_ids_collapse_into_one_endpoint() -> None:
    assert template("https://x.test/api/orders/10482") == "/api/orders/{id}"
    assert template("https://x.test/api/u/9f8a7b6c5d4e") == "/api/u/{id}"
    assert template("https://x.test/api/orders") == "/api/orders"


def test_personal_data_is_categorised_never_quoted(checked: CheckResult) -> None:
    """Writing the values into the artifact would be the leak this is looking for."""
    issue = next(i for i in checked.issues if i.issueKind == "api-personal-data")
    assert issue.data["categories"] == ["email"]
    assert "@" not in json.dumps(issue.data)
    assert PII_PATTERNS["email"].search("ada@example.test")


# ---------------------------------------------------------------------- live run


@pytest.mark.browser
def test_the_engine_finds_them_again_against_the_live_application(
    browser_ready: None, tmp_path: Path
) -> None:
    """`fixtures/exercised` is frozen, so it cannot catch a regression in the flow engine
    itself. This drives the real application and re-runs the sweep over the result."""
    import os

    from engine.capture.exercise import exercise
    from engine.capture.run import capture
    from tests.fixtures.app import USERS, serve

    server, base, _ = serve()
    accounts = list(USERS.items())
    try:
        for index, (user, password) in enumerate(accounts):
            os.environ[f"FLOW_USER_{index}"] = user
            os.environ[f"FLOW_PASSWORD_{index}"] = password

        config = RunConfig(
            viewports=[VIEWPORT_PRESETS["desktop_1440"]],
            maxPages=8,
            maxDepth=2,
            settleMs=150,
            authorisedBy="Jo Blake (client CTO)",
            authorisedHosts=[base.split("//")[1]],
        )
        result = asyncio.run(capture(base + "/app/", tmp_path, config=config))
        ctx = RunContext.open(result.paths.root)
        personas = [_persona(name, base, i) for i, name in enumerate(("ada", "grace"))]
        outcome = asyncio.run(exercise(RunPaths(result.paths.root), ctx, personas=personas))
    finally:
        server.shutdown()
        server.server_close()

    assert outcome.failed >= 3
    assert all(flow.trace and flow.video for flow in outcome.flows)
    fresh = runner.check(RunContext.open(result.paths.root))
    missing = sorted(documented() - found(fresh))
    assert not missing, "a live run missed: " + ", ".join(f"{c}/{k}" for c, k in missing)


def _persona(name: str, base: str, index: int) -> Persona:
    return Persona.model_validate(
        {
            "name": name,
            "login": {
                "url": f"{base}/app/login",
                "usernameSelector": "#email",
                "passwordSelector": "#password",
                "usernameRef": f"env:FLOW_USER_{index}",
                "passwordRef": f"env:FLOW_PASSWORD_{index}",
                "submitSelector": "#login-submit",
                "successSelector": "h1",
            },
            "sessionCheck": {"url": f"{base}/app/account", "loggedInSelector": "#logout"},
        }
    )


@pytest.mark.browser
def test_nothing_is_probed_without_authorisation(browser_ready: None, tmp_path: Path) -> None:
    from engine.capture.exercise import exercise
    from engine.capture.run import capture
    from tests.fixtures.app import serve

    server, base, _ = serve()
    try:
        config = RunConfig(
            viewports=[VIEWPORT_PRESETS["desktop_1440"]],
            maxPages=2,
            maxDepth=0,
            settleMs=120,
            flows=False,
        )
        result = asyncio.run(capture(base + "/app/", tmp_path, config=config))
        ctx = RunContext.open(result.paths.root)
        outcome = asyncio.run(exercise(RunPaths(result.paths.root), ctx))
    finally:
        server.shutdown()
        server.server_close()

    assert outcome.api.endpoints, "the endpoints are still derived"
    assert outcome.api.probes == [], "but nothing was sent"
    assert "authorisedBy" in outcome.api.skipped["*"]
