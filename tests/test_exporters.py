"""Export adapters — SPEC §14.

Every HTTP adapter makes a real request to `tests/fixtures/tracker.py` and reads a real
response. The point is the wire shape: a field a tracker silently ignores is a field
nobody notices is wrong until an export lands empty in someone's backlog.
"""

from __future__ import annotations

import csv
import io
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.artifact.context import RunContext
from engine.checkers import runner
from engine.exporters import base as exporters
from engine.exporters import csv_file, markdown
from engine.exporters.base import Bundle, Target, export
from engine.exporters.common import body
from engine.exporters.jira import adf
from engine.fixtures import fixture_path
from engine.issues.models import Issue
from tests.fixtures.tracker import State, serve

TOKEN_ENV = "BUREAU_TEST_TRACKER_TOKEN"


@pytest.fixture(scope="module")
def issues() -> list[Issue]:
    """Three findings that can be pictured.

    A page-level finding — "the CSP header is missing" — has no box and so no attachment,
    which is correct and would make the attachment assertions vacuous.
    """
    ctx = RunContext.open(fixture_path("broken"))
    located = [i for i in runner.check(ctx).issues if any(x.box for x in i.instances)]
    assert len(located) >= 3
    return located[:3]


@pytest.fixture(scope="module")
def flow_issues() -> list[Issue]:
    """Flow failures, which carry step screenshots and a trace on disk."""
    ctx = RunContext.open(fixture_path("exercised"))
    return [i for i in runner.check(ctx).issues if i.evidence][:2]


@pytest.fixture
def tracker(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[str, State]]:
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    server, base, state = serve()
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()


def target(kind: str, base: str, **over: object) -> Target:
    fields: dict[str, object] = {
        "kind": kind,
        "base_url": base,
        "project": "QA",
        "token_env": TOKEN_ENV,
        "user": "qa@example.test",
    }
    fields.update(over)
    return Target(**fields)  # type: ignore[arg-type]


# --------------------------------------------------------------------- the shape


def test_every_shipped_adapter_is_registered() -> None:
    """SPEC §14's list. A missing one is a silently unavailable integration."""
    assert set(exporters.discover()) == {
        "jira",
        "openproject",
        "azure_devops",
        "linear",
        "github",
        "csv",
        "markdown",
    }


def test_adding_a_tracker_needs_no_change_to_the_core() -> None:
    """The abstraction's own test: a new adapter is a class with two methods."""

    @exporters.exporter
    class Nowhere:
        kind = "nowhere"

        def map(self, bundle: Bundle, target: Target) -> dict[str, object]:
            return {"title": bundle.issue.title}

        def push(
            self, payloads: list[tuple[Bundle, dict[str, object]]], target: Target
        ) -> list[exporters.ExportResult]:
            return [
                exporters.ExportResult(b.issue.fingerprint, remote_key="1") for b, _ in payloads
            ]

    try:
        assert isinstance(exporters.get("nowhere"), exporters.Exporter)
    finally:
        exporters.registry.__globals__["_REGISTRY"].pop("nowhere", None)


def test_a_token_never_comes_from_project_config(issues: list[Issue]) -> None:
    """CLAUDE.md: credentials come from the environment. A `Target` cannot hold one."""
    assert not hasattr(Target(kind="jira"), "token")
    results = export(issues, target("jira", "http://127.0.0.1:1", dry_run=True))
    assert {r.action for r in results} == {"skipped"}


# ----------------------------------------------------------------------- bodies


def test_the_body_says_what_where_and_how_to_see_it(issues: list[Issue]) -> None:
    text = body(Bundle(issue=issues[0], report_url="https://qa.test/report"))
    assert issues[0].title in text or issues[0].description in text
    assert "**Severity:**" in text
    assert "https://qa.test/report" in text
    assert issues[0].fingerprint in text, "the fingerprint is how a re-export finds it"


def test_adf_survives_bullets_and_bold() -> None:
    document = adf("Intro line\n\n- **Expected:** 16px\n- **Actual:** 20px\n\nTail")
    kinds = [node["type"] for node in document["content"]]
    assert kinds == ["paragraph", "bulletList", "paragraph"]
    first_item = document["content"][1]["content"][0]["content"][0]["content"]
    assert first_item[0]["marks"] == [{"type": "strong"}]
    assert first_item[0]["text"] == "Expected:"


# ------------------------------------------------------------------------- jira


def test_jira_creates_updates_and_attaches(
    flow_issues: list[Issue], tracker: tuple[str, State]
) -> None:
    """The phase 9 done-when, against a server that speaks REST v3."""
    base, state = tracker
    issues = flow_issues
    run_dir = fixture_path("exercised")
    first = export(issues, target("jira", base), run_dir=run_dir)

    assert [r.action for r in first] == ["created"] * len(issues)
    assert all(r.remote_key.startswith("QA-") for r in first)
    assert all(r.url.endswith(r.remote_key) for r in first)

    created = state.of("/rest/api/3/issue")[0].body
    assert created["fields"]["project"] == {"key": "QA"}
    assert created["fields"]["issuetype"] == {"name": "Bug"}
    assert created["fields"]["description"]["type"] == "doc"
    assert all(" " not in label for label in created["fields"]["labels"])

    attached = sum(r.attachments for r in first)
    assert attached > 0, "a flow failure arrives with its step screenshots"
    assert sum(len(v) for v in state.attachments.values()) == attached
    assert all(
        name.endswith((".png", ".zip", ".webm", ".json"))
        for names in state.attachments.values()
        for name in names
    )

    known = {r.fingerprint: r.remote_key for r in first}
    again = export(issues, target("jira", base), run_dir=run_dir, known=known)
    assert [r.action for r in again] == ["updated"] * len(issues)
    assert [r.remote_key for r in again] == [r.remote_key for r in first]
    assert len(state.items) == len(issues), "a second export updates rather than duplicates"


def test_a_measured_finding_is_attached_as_the_annotated_crop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No trace, no video — so the ticket gets the picture the report would have shown."""
    from PIL import Image

    artifact = tmp_path / "run"
    shutil.copytree(fixture_path("broken"), artifact)
    ctx = RunContext.open(artifact)
    issue = next(i for i in runner.check(ctx).issues if any(x.box for x in i.instances))
    lead = next(x for x in issue.instances if x.box)
    png = ctx.paths.full_png(lead.pageId, lead.viewport)
    png.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (900, 1600), "white").save(png)

    work = tmp_path / "work"
    work.mkdir()
    files = Bundle(issue=issue, run_dir=artifact, work=work).attachments()
    assert len(files) == 1
    assert files[0].suffix == ".png" and files[0].stat().st_size > 0


def test_jira_reports_a_refusal_instead_of_raising(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, _ = tracker
    results = export(issues[:1], target("jira", base, project=""))
    assert results[0].action == "failed"
    assert "project key is required" in results[0].error


def test_field_mapping_is_configurable_per_project(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, state = tracker
    export(
        issues[:1],
        target(
            "jira",
            base,
            priorities={"major": "P2"},
            labels=["client-acme"],
            extra={"issueType": "Defect", "fields": {"customfield_1": "web"}},
        ),
    )
    fields = state.of("/rest/api/3/issue")[0].body["fields"]
    assert fields["issuetype"] == {"name": "Defect"}
    assert "client-acme" in fields["labels"]
    assert fields["customfield_1"] == "web"
    if issues[0].severity.value == "major":
        assert fields["priority"] == {"name": "P2"}


# ----------------------------------------------------------- the other trackers


def test_github_creates_and_updates(issues: list[Issue], tracker: tuple[str, State]) -> None:
    base, state = tracker
    first = export(issues[:1], target("github", base, project="acme/site"))
    assert first[0].action == "created"
    assert first[0].url.startswith("https://github.test/")

    payload = state.of("/issues")[0].body
    assert any(label.startswith("severity:") for label in payload["labels"])

    again = export(
        issues[:1],
        target("github", base, project="acme/site"),
        known={first[0].fingerprint: first[0].remote_key},
    )
    assert again[0].action == "updated"


def test_linear_maps_severity_onto_its_own_priority_scale(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, state = tracker
    results = export(issues[:1], target("linear", f"{base}/graphql", project="team_1"))
    assert results[0].action == "created"
    payload = state.of("/graphql")[0].body["variables"]["input"]
    assert payload["teamId"] == "team_1"
    assert payload["priority"] in (1, 2, 3, 4)


def test_linear_surfaces_a_graphql_error_that_arrived_with_a_200(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, _ = tracker
    results = export(issues[:1], target("linear", f"{base}/graphql", project=""))
    assert results[0].action == "failed"
    assert "teamId" in results[0].error


def test_openproject_locks_before_it_updates(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, state = tracker
    first = export(
        issues[:1], target("openproject", base, project="7"), run_dir=fixture_path("broken")
    )
    assert first[0].action == "created"

    again = export(
        issues[:1],
        target("openproject", base, project="7"),
        known={first[0].fingerprint: first[0].remote_key},
    )
    assert again[0].action == "updated"
    patch = next(c for c in state.calls if c.method == "PATCH")
    assert patch.body["lockVersion"] == 7, "OpenProject rejects a patch without the version"


def test_azure_devops_sends_a_json_patch_document(
    issues: list[Issue], tracker: tuple[str, State]
) -> None:
    base, state = tracker
    results = export(issues[:1], target("azure_devops", base, project="Web"))
    assert results[0].action == "created"
    call = state.of("/_apis/wit/workitems")[0]
    assert call.headers["content-type"] == "application/json-patch+json"
    assert {op["path"] for op in call.body} >= {"/fields/System.Title", "/fields/System.Tags"}


# ------------------------------------------------------------------ file formats


def test_csv_is_one_row_per_issue(issues: list[Issue]) -> None:
    adapter = exporters.get("csv")
    rows = [adapter.map(Bundle(issue=issue), Target(kind="csv")) for issue in issues]
    parsed = list(csv.DictReader(io.StringIO(csv_file.render(rows))))
    assert len(parsed) == len(issues)
    assert parsed[0]["fingerprint"] == issues[0].fingerprint
    assert "**" not in parsed[0]["description"], "csv is read by spreadsheets, not renderers"


def test_markdown_renders_a_document_someone_can_paste(issues: list[Issue]) -> None:
    adapter = exporters.get("markdown")
    rows = [adapter.map(Bundle(issue=issue), Target(kind="markdown")) for issue in issues]
    document = markdown.render(rows, title="Fixture site")
    assert document.startswith("# Fixture site")
    assert document.count("###") == len(issues)


def test_a_file_export_needs_no_credential_and_no_network(
    issues: list[Issue], tmp_path: Path
) -> None:
    results = export(issues, Target(kind="csv"))
    assert [r.action for r in results] == ["created"] * len(issues)
