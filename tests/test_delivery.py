"""Exports, schedules, digests, CI and record-a-flow in the control plane — SPEC §14, §15."""

from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import select

from bureau_api import db, delivery
from bureau_api.events import MemoryEvents
from bureau_api.events import use as use_events
from bureau_api.jobs import InlineQueue
from bureau_api.jobs import use as use_queue
from bureau_api.main import app
from bureau_api.models import Run, RunState, Schedule
from engine.capture.flows.record import PERSONA_PASSWORD, parse
from tests.fixtures.tracker import State, serve
from tests.test_api import checked_artifact, index_fixture, project

TOKEN_ENV = "BUREAU_TEST_TRACKER_TOKEN"


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BUREAU_SCHEDULER", "0")  # fired by hand in these tests
    monkeypatch.setenv(TOKEN_ENV, "test-token")
    monkeypatch.delenv("REDIS_URL", raising=False)
    db.reset(f"sqlite:///{tmp_path}/control.db")
    use_events(MemoryEvents())
    use_queue(InlineQueue())
    with TestClient(app) as active:
        yield active


RECEIVED: list[dict[str, Any]] = []


class _Hook(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: Any) -> None:
        return

    def do_POST(self) -> None:
        length = int(self.headers.get("content-length") or 0)
        RECEIVED.append(json.loads(self.rfile.read(length) or b"{}"))
        self.send_response(200)
        self.send_header("content-length", "0")
        self.end_headers()


@pytest.fixture
def hook() -> Iterator[str]:
    RECEIVED.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Hook)
    Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/digest"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def tracker() -> Iterator[tuple[str, State]]:
    server, base, state = serve()
    try:
        yield base, state
    finally:
        server.shutdown()
        server.server_close()


def when(text: str) -> datetime:
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


# --------------------------------------------------------------------- exporting


def test_issues_land_in_the_tracker_and_the_key_comes_back(
    client: TestClient, tmp_path: Path, tracker: tuple[str, State]
) -> None:
    """The phase 9 done-when, against a server that speaks Jira's REST v3."""
    base, state = tracker
    project_id, _ = index_fixture(client, tmp_path)
    created = client.post(
        f"/api/projects/{project_id}/exports",
        json={
            "kind": "jira",
            "name": "Acme Jira",
            "config": {
                "baseUrl": base,
                "project": "QA",
                "tokenEnv": TOKEN_ENV,
                "user": "qa@example.test",
                "labels": ["from-bureau"],
            },
        },
    ).json()

    results = client.post(f"/api/exports/{created['id']}/run", json={}).json()
    assert results, "there were issues to send"
    assert {r["action"] for r in results} == {"created"}
    assert all(r["remoteKey"].startswith("QA-") for r in results)
    assert len(state.items) == len(results)

    issues = client.get(f"/api/projects/{project_id}/issues").json()
    stored = [i for i in issues if i["id"]]
    assert stored, "issues survived the export"

    again = client.post(f"/api/exports/{created['id']}/run", json={}).json()
    assert {r["action"] for r in again} == {"updated"}, "a second export updates, never duplicates"
    assert len(state.items) == len(results), "no new tickets"
    assert [r["remoteKey"] for r in again] == [r["remoteKey"] for r in results]


def test_a_dismissed_issue_is_not_somebody_elses_backlog(
    client: TestClient, tmp_path: Path, tracker: tuple[str, State]
) -> None:
    base, _ = tracker
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    client.patch(f"/api/issues/{issues[0]['id']}", json={"state": "dismissed", "by": "joujou"})

    created = client.post(
        f"/api/projects/{project_id}/exports",
        json={
            "kind": "jira",
            "config": {"baseUrl": base, "project": "QA", "tokenEnv": TOKEN_ENV, "user": "a@b.c"},
        },
    ).json()
    sent = client.post(f"/api/exports/{created['id']}/run", json={}).json()
    assert issues[0]["fingerprint"] not in {r["fingerprint"] for r in sent}


def test_an_unknown_exporter_is_refused_at_configuration_time(client: TestClient) -> None:
    created = project(client)
    response = client.post(
        f"/api/projects/{created['id']}/exports", json={"kind": "trello", "config": {}}
    )
    assert response.status_code == 422
    assert "trello" in response.text


def test_the_shipped_adapters_are_discoverable(client: TestClient) -> None:
    kinds = client.get("/api/exporters").json()["kinds"]
    assert {"jira", "openproject", "azure_devops", "linear", "github", "csv", "markdown"} <= set(
        kinds
    )


# -------------------------------------------------------------------- scheduling


def test_a_schedule_is_validated_when_it_is_written(client: TestClient) -> None:
    created = project(client)
    bad = client.post(
        f"/api/projects/{created['id']}/schedules", json={"expression": "every night"}
    )
    assert bad.status_code == 422

    good = client.post(
        f"/api/projects/{created['id']}/schedules",
        json={"expression": "0 2 * * *", "timezone": "Europe/London"},
    ).json()
    assert good["nextFireAt"], "the next firing is worked out immediately"


def test_a_due_schedule_queues_exactly_one_run(client: TestClient) -> None:
    created = project(client)
    client.post(f"/api/projects/{created['id']}/schedules", json={"expression": "*/5 * * * *"})

    with db.session() as session:
        schedule = session.exec(select(Schedule)).one()
        schedule.lastFiredAt = when("2026-03-01T10:00")
        session.add(schedule)
        session.commit()

        started = delivery.fire_due(session, now=when("2026-03-01T10:06"))
        assert len(started) == 1

        again = delivery.fire_due(session, now=when("2026-03-01T10:07"))
        assert again == [], "the window fired once"


def test_a_schedule_does_not_pile_up_on_a_slow_site(client: TestClient) -> None:
    created = project(client)
    client.post(f"/api/projects/{created['id']}/schedules", json={"expression": "* * * * *"})
    with db.session() as session:
        session.add(Run(projectId=created["id"], state=RunState.running))
        session.commit()
        assert delivery.fire_due(session, now=when("2026-03-01T10:06")) == []
        schedule = session.exec(select(Schedule)).one()
        assert schedule.lastFiredAt is not None, "the window is consumed, not queued up"


# ------------------------------------------------------------------- notifying


def diff_file(artifact: Path, entries: list[dict[str, Any]]) -> None:
    (artifact / "diff.json").write_text(json.dumps({"baseRunId": "run_before", "entries": entries}))


def test_a_quiet_scheduled_run_sends_nothing_at_all(
    client: TestClient, tmp_path: Path, hook: str
) -> None:
    """The phase 9 done-when. Nothing is sent, and nothing arrives."""
    project_id, run_id = index_fixture(client, tmp_path)
    client.post(
        f"/api/projects/{project_id}/channels", json={"kind": "webhook", "config": {"url": hook}}
    )
    artifact = checked_artifact(tmp_path, "broken")
    diff_file(
        artifact,
        [
            {
                "fingerprint": "a",
                "change": "still-open",
                "title": "old news",
                "severity": "major",
                "checkerId": "layout.alignment",
                "instanceCount": 1,
            },
            {
                "fingerprint": "b",
                "change": "fixed",
                "title": "gone",
                "severity": "minor",
                "checkerId": "layout.alignment",
                "instanceCount": 0,
            },
        ],
    )
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert delivery.notify(session, run) == []
    assert RECEIVED == []


def test_a_new_issue_earns_a_digest(client: TestClient, tmp_path: Path, hook: str) -> None:
    project_id, run_id = index_fixture(client, tmp_path)
    client.post(
        f"/api/projects/{project_id}/channels", json={"kind": "webhook", "config": {"url": hook}}
    )
    artifact = checked_artifact(tmp_path, "broken")
    diff_file(
        artifact,
        [
            {
                "fingerprint": "c",
                "change": "regressed",
                "title": "login broke again",
                "severity": "blocker",
                "checkerId": "functional.flows",
                "instanceCount": 1,
            }
        ],
    )
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert delivery.notify(session, run) == [("webhook", "200")]
    assert RECEIVED[0]["regressed"] == [{"severity": "blocker", "title": "login broke again"}]


def test_a_threshold_holds_back_the_small_stuff(
    client: TestClient, tmp_path: Path, hook: str
) -> None:
    project_id, run_id = index_fixture(client, tmp_path)
    client.post(
        f"/api/projects/{project_id}/channels",
        json={"kind": "webhook", "config": {"url": hook}, "minSeverity": "major"},
    )
    artifact = checked_artifact(tmp_path, "broken")
    diff_file(
        artifact,
        [
            {
                "fingerprint": "d",
                "change": "new",
                "title": "a shadow is 1px off",
                "severity": "trivial",
                "checkerId": "layout.alignment",
                "instanceCount": 1,
            }
        ],
    )
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        assert delivery.notify(session, run) == []
    assert RECEIVED == []


# --------------------------------------------------------------------------- ci


def test_the_ci_endpoint_answers_with_counts_and_a_report(
    client: TestClient, tmp_path: Path
) -> None:
    project_id, run_id = index_fixture(client, tmp_path)
    artifact = checked_artifact(tmp_path, "broken")
    diff_file(
        artifact,
        [
            {
                "fingerprint": "e",
                "change": "new",
                "title": "one",
                "severity": "major",
                "checkerId": "layout.alignment",
                "instanceCount": 1,
            },
            {
                "fingerprint": "f",
                "change": "new",
                "title": "two",
                "severity": "minor",
                "checkerId": "layout.alignment",
                "instanceCount": 1,
            },
            {
                "fingerprint": "g",
                "change": "regressed",
                "title": "three",
                "severity": "blocker",
                "checkerId": "functional.flows",
                "instanceCount": 1,
            },
        ],
    )
    body = client.get(f"/api/runs/{run_id}/ci").json()
    assert body["new"] == {"major": 1, "minor": 1}
    assert body["regressed"] == {"blocker": 1}
    assert body["runId"] == run_id
    assert project_id


def test_the_ci_endpoint_finds_a_project_by_target(client: TestClient) -> None:
    created = project(client, target="https://ci.example.test/")
    queued = client.post(
        "/api/ci/runs", json={"target": "https://ci.example.test/", "wait": 0}
    ).json()
    assert queued["projectId"] == created["id"], "a pipeline knows a URL, not a project id"
    assert queued["runId"]


# ------------------------------------------------------------------- recordings


CODEGEN = """
import asyncio
from playwright.async_api import async_playwright, expect

async def run(playwright):
    browser = await playwright.chromium.launch(headless=False)
    page = await browser.new_page()
    await page.goto("https://shop.test/")
    await page.get_by_role("link", name="Sign in").click()
    await page.get_by_label("Email").fill("ada@example.test")
    await page.get_by_label("Password").fill("hunter2")
    await page.get_by_role("button", name="Sign in").click()
    await expect(page.get_by_text("Signed in as Ada")).to_be_visible()
    await page.locator("#cart").click()
"""


def test_a_recorded_journey_becomes_readable_steps() -> None:
    steps = parse(CODEGEN)
    assert [s.action for s in steps] == [
        "goto",
        "click",
        "fill",
        "fill",
        "click",
        "expect_visible",
        "click",
    ]
    assert steps[1].selector == 'role=link[name="Sign in"]'
    assert steps[1].description == "Click “Sign in”"
    assert steps[-1].selector == "#cart"


def test_a_recorded_password_is_a_reference_not_a_password() -> None:
    """CLAUDE.md: no credential lands anywhere durable, and a recording is durable."""
    steps = parse(CODEGEN)
    email, password = steps[2], steps[3]
    assert email.value == "ada@example.test"
    assert password.value == PERSONA_PASSWORD
    assert "hunter2" not in json.dumps([s.as_dict() for s in steps])


def test_a_recording_is_saved_with_generated_descriptions(client: TestClient) -> None:
    created = project(client)
    saved = client.post(
        f"/api/projects/{created['id']}/recordings",
        json={"name": "Sign in and open the cart", "script": CODEGEN, "persona": "ada"},
    ).json()
    assert saved["name"] == "Sign in and open the cart"
    assert [s["description"] for s in saved["steps"]][:2] == [
        "Open https://shop.test/",
        "Click “Sign in”",
    ]
    assert client.get(f"/api/projects/{created['id']}/recordings").json()[0]["id"] == saved["id"]


def test_a_recording_travels_with_the_run_config(client: TestClient) -> None:
    """It runs on every future run, so the artifact has to say what ran."""
    from bureau_api.jobs import _recordings

    created = project(client)
    client.post(
        f"/api/projects/{created['id']}/recordings",
        json={"name": "Journey", "script": CODEGEN},
    )
    with db.session() as session:
        carried = _recordings(session, created["id"])
    assert carried[0]["name"] == "Journey"
    assert carried[0]["steps"][0]["action"] == "goto"


def test_a_scheduled_run_on_an_unchanged_site_sends_nothing_at_all(
    client: TestClient, hook: str, browser_ready: None
) -> None:
    """The phase 9 done-when, for real: two runs against the same unchanged site.

    The first run finds what it finds. The second finds the same things, so the digest
    has nothing to say and says nothing — which is the difference between a tool people
    keep switched on and one they mute in a fortnight.
    """
    from tests.fixtures.app import serve as serve_app

    server, base, _ = serve_app()
    try:
        created = project(
            client,
            target=f"{base}/app/",
            config={
                "viewports": [{"name": "desktop_1440", "width": 1440, "height": 900}],
                "maxPages": 2,
                "maxDepth": 1,
                "settleMs": 120,
                "vitalsSamples": 1,
                "flows": False,
                "apiProbes": False,
            },
        )
        client.post(
            f"/api/projects/{created['id']}/channels",
            json={"kind": "webhook", "config": {"url": hook}},
        )
        client.post(f"/api/projects/{created['id']}/schedules", json={"expression": "* * * * *"})

        first = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        _await(client, first)
        assert RECEIVED, "a first run is all new, so it has something to say"
        RECEIVED.clear()

        with db.session() as session:
            started = delivery.fire_due(session, now=datetime.now(UTC))
        assert len(started) == 1, "the schedule queued the second run"
        second = _await(client, started[0])
    finally:
        server.shutdown()
        server.server_close()

    assert second["state"] == "complete", second
    assert second["diff"], "the second run was diffed against the first"
    assert second["diff"].get("new", 0) == 0
    assert second["diff"].get("regressed", 0) == 0
    assert RECEIVED == [], "nothing new, so nothing sent"


def _await(client: TestClient, run_id: str, timeout: float = 300) -> dict[str, Any]:
    import time

    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in ("complete", "failed", "aborted"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def test_a_second_run_compares_itself_with_the_first(
    client: TestClient, browser_ready: None
) -> None:
    """Visual regression end to end — SPEC §5's hardening.

    The second run writes `visual.json` against the first, and an unchanged site scores a
    perfect similarity, which is the only result that makes the check usable at all.
    """
    from tests.fixtures.app import serve as serve_app

    server, base, _ = serve_app()
    try:
        created = project(
            client,
            target=f"{base}/app/",
            config={
                "viewports": [{"name": "desktop_1440", "width": 1440, "height": 900}],
                "maxPages": 2,
                "maxDepth": 1,
                "settleMs": 120,
                "vitalsSamples": 1,
                "flows": False,
                "apiProbes": False,
                "maskSelectors": [".timestamp"],
            },
        )
        first = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        _await(client, first)
        second = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        _await(client, second)
    finally:
        server.shutdown()
        server.server_close()

    with db.session() as session:
        run = session.get(Run, second)
        assert run is not None and run.artifactPath
        path = Path(run.artifactPath) / "visual.json"

    assert path.is_file(), "the second run compared itself with the first"
    payload = json.loads(path.read_text())
    assert payload["maskSelectors"] == [".timestamp"]
    compared = [s for s in payload["surfaces"] if s["compared"]]
    assert compared, "there were surfaces in common"
    assert all(s["ssim"] == 1.0 for s in compared), [(s["pagePath"], s["ssim"]) for s in compared]
    assert all(not s["changes"] for s in compared), "nothing moved on an unchanged site"


def test_a_slack_webhook_url_cannot_be_stored(client: TestClient) -> None:
    """CLAUDE.md: a Slack webhook URL is itself a credential, so it is named not stored."""
    project = client.post(
        "/api/projects", json={"name": "acme", "target": "https://acme.test/"}
    ).json()
    literal = client.post(
        f"/api/projects/{project['id']}/channels",
        json={"kind": "slack", "config": {"url": "https://hooks.slack.com/services/T/B/xxx"}},
    )
    assert literal.status_code == 422
    assert "hooks.slack.com" not in str(
        client.get(f"/api/projects/{project['id']}/channels").json()
    )

    named = client.post(
        f"/api/projects/{project['id']}/channels",
        json={"kind": "slack", "config": {"url_env": "ACME_SLACK_WEBHOOK"}},
    )
    assert named.status_code == 201, named.text
