"""The control plane — SPEC §17.

SQLite and an in-process queue: the API has to be runnable and testable without a
database server or a Redis, and one environment variable is the whole difference.
"""

from __future__ import annotations

import json
import shutil
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from bureau_api import db
from bureau_api.events import MemoryEvents
from bureau_api.events import use as use_events
from bureau_api.issues import index_run
from bureau_api.jobs import InlineQueue
from bureau_api.jobs import use as use_queue
from bureau_api.knowledge import use as use_provider
from bureau_api.main import app
from bureau_api.models import Issue, IssueState, Run, RunState
from engine.agents.providers.scripted import ScriptedProvider
from engine.fixtures import fixture_path


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "runs"))
    monkeypatch.delenv("REDIS_URL", raising=False)
    db.reset(f"sqlite:///{tmp_path}/control.db")
    use_events(MemoryEvents())
    use_queue(InlineQueue())
    with TestClient(app) as active:
        yield active


def project(client: TestClient, **over: Any) -> dict[str, Any]:
    payload = {"name": "Fixture", "target": "https://example.test/", "authorisedBy": "Jo Blake"}
    payload.update(over)
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


# ------------------------------------------------------------------- projects


def test_health(client: TestClient) -> None:
    assert client.get("/api/health").json()["status"] == "ok"


def test_a_project_round_trips(client: TestClient) -> None:
    created = project(client)
    assert created["authorisedBy"] == "Jo Blake"
    assert created["runs"] == 0 and created["openIssues"] == 0
    assert client.get(f"/api/projects/{created['id']}").json()["name"] == "Fixture"
    assert [p["id"] for p in client.get("/api/projects").json()] == [created["id"]]


def test_a_missing_project_is_a_404(client: TestClient) -> None:
    assert client.get("/api/projects/prj_nope").status_code == 404


def test_personas_carry_references_not_credentials(client: TestClient) -> None:
    """A password never reaches this table (CLAUDE.md)."""
    created = project(client)
    persona = client.post(
        f"/api/projects/{created['id']}/personas",
        json={
            "name": "ada",
            "config": {
                "login": {
                    "url": "https://example.test/login",
                    "usernameSelector": "#email",
                    "passwordSelector": "#password",
                    "usernameRef": "env:ADA_USER",
                    "passwordRef": "env:ADA_PASSWORD",
                }
            },
        },
    ).json()
    stored = json.dumps(persona["config"])
    assert "env:ADA_PASSWORD" in stored
    assert "hunter2" not in stored
    assert client.delete(f"/api/personas/{persona['id']}").status_code == 204
    assert client.get(f"/api/projects/{created['id']}/personas").json() == []


# ------------------------------------------------------------------ knowledge


def test_knowledge_is_never_silently_applied(client: TestClient) -> None:
    """SPEC §10: a model parses the free text and a human confirms it before the run."""
    created = project(client)
    entry = client.post(
        f"/api/projects/{created['id']}/knowledge",
        json={"raw": "client asked for the CTA to be green", "createdBy": "joujou"},
    ).json()
    assert entry["confirmed"] is False
    confirmed = client.patch(f"/api/knowledge/{entry['id']}", json={"confirm": True}).json()
    assert confirmed["confirmed"] is True
    assert client.get(f"/api/projects/{created['id']}/knowledge").json()[0]["createdBy"] == "joujou"


# ----------------------------------------------------------------- indexing


def checked_artifact(tmp_path: Path, name: str) -> Path:
    """A frozen artifact with its `issues.json` written, the way a finished run leaves it.

    The frozen fixtures carry the measurements and not the output, so the sweep runs here
    exactly as it does in a real run.
    """
    from engine.artifact.context import RunContext
    from engine.artifact.store import RunPaths
    from engine.checkers import runner

    artifact = tmp_path / f"artifact-{name}"
    if not artifact.exists():
        shutil.copytree(fixture_path(name), artifact)
        ctx = RunContext.open(artifact)
        runner.write(RunPaths(artifact), ctx, runner.check(ctx))
    return artifact


def index_fixture(
    client: TestClient, tmp_path: Path, name: str = "broken", project_id: str | None = None
) -> tuple[str, str]:
    """Project a checked artifact into the index, the way a finished run does."""
    project_id = project_id or project(client)["id"]
    artifact = checked_artifact(tmp_path, name)
    with db.session() as session:
        run = Run(projectId=project_id, state=RunState.complete, artifactPath=str(artifact))
        session.add(run)
        session.commit()
        session.refresh(run)
        index_run(session, run, artifact)
        run_id = run.id
    return project_id, run_id


def test_issues_are_indexed_from_the_artifact(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    assert len(issues) > 20
    assert all(issue["fingerprint"] for issue in issues)
    assert issues[0]["payload"]["instances"], "the neutral record travels with the row"


def test_issues_come_back_worst_first(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    order = ["blocker", "critical", "major", "minor", "trivial"]
    ranks = [order.index(issue["severity"]) for issue in issues]
    assert ranks == sorted(ranks)


def test_issues_can_be_filtered(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    a11y = client.get(f"/api/projects/{project_id}/issues", params={"category": "a11y"}).json()
    assert a11y and all(issue["category"] == "a11y" for issue in a11y)


def test_a_dismissal_is_permanent(client: TestClient, tmp_path: Path) -> None:
    """The single feature that decides whether the tool gets used past week two."""
    project_id, _ = index_fixture(client, tmp_path)
    first = client.get(f"/api/projects/{project_id}/issues").json()[0]
    dismissed = client.patch(
        f"/api/issues/{first['id']}",
        json={"state": "dismissed", "reason": "client asked for it", "by": "joujou"},
    ).json()
    assert dismissed["state"] == "dismissed"

    visible = client.get(f"/api/projects/{project_id}/issues").json()
    assert first["id"] not in {issue["id"] for issue in visible}

    with db.session() as session:
        run = Run(projectId=project_id, state=RunState.complete)
        session.add(run)
        session.commit()
        session.refresh(run)
        index_run(session, run, checked_artifact(tmp_path, "broken"))
        row = session.get(Issue, first["id"])
        assert row is not None
        assert row.state is IssueState.dismissed
        assert row.dismissedBy == "joujou"

    again = client.get(f"/api/projects/{project_id}/issues").json()
    assert first["id"] not in {issue["id"] for issue in again}


def test_an_issue_that_stops_appearing_is_fixed(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path, "broken")
    before = {issue["id"] for issue in client.get(f"/api/projects/{project_id}/issues").json()}
    with db.session() as session:
        run = Run(projectId=project_id, state=RunState.complete)
        session.add(run)
        session.commit()
        session.refresh(run)
        index_run(session, run, checked_artifact(tmp_path, "tiny"))
    after = client.get(f"/api/projects/{project_id}/issues", params={"state": "fixed"}).json()
    assert {issue["id"] for issue in after} & before


def test_severity_is_always_editable(client: TestClient, tmp_path: Path) -> None:
    """SPEC §2: severity assignment remains human, and the human's edit wins."""
    project_id, _ = index_fixture(client, tmp_path)
    first = client.get(f"/api/projects/{project_id}/issues").json()[0]
    updated = client.patch(f"/api/issues/{first['id']}", json={"severity": "trivial"}).json()
    assert updated["severity"] == "trivial"


def test_anyone_with_the_link_can_comment(client: TestClient, tmp_path: Path) -> None:
    """Developers will not sign up for another tool (SPEC §13)."""
    project_id, _ = index_fixture(client, tmp_path)
    first = client.get(f"/api/projects/{project_id}/issues").json()[0]
    created = client.post(
        f"/api/issues/{first['id']}/comments",
        json={"author": "sam", "body": "client changed this, ignore"},
    )
    assert created.status_code == 201
    assert client.get(f"/api/issues/{first['id']}/comments").json()[0]["author"] == "sam"


# ---------------------------------------------------------------------- media


def test_evidence_is_served_from_beside_the_run(client: TestClient, tmp_path: Path) -> None:
    _, run_id = index_fixture(client, tmp_path)
    artifact = tmp_path / "copy"
    shutil.copytree(fixture_path("exercised"), artifact)
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.artifactPath = str(artifact)
        session.add(run)
        session.commit()

    shot = next(artifact.glob("flows/*/step_01.png"))
    relative = shot.relative_to(artifact).as_posix()
    assert client.get(f"/api/runs/{run_id}/media/{relative}").status_code == 200


def test_media_cannot_climb_out_of_the_run(client: TestClient, tmp_path: Path) -> None:
    _, run_id = index_fixture(client, tmp_path)
    artifact = tmp_path / "copy2"
    artifact.mkdir()
    (tmp_path / "secret.txt").write_text("not yours")
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None
        run.artifactPath = str(artifact)
        session.add(run)
        session.commit()
    assert client.get(f"/api/runs/{run_id}/media/../secret.txt").status_code == 404


# ------------------------------------------------------------------ progress


def test_the_event_stream_replays_what_a_late_watcher_missed(client: TestClient) -> None:
    """A browser that connects halfway through a run needs the events it missed, or the
    page it lands on is emptier than the run it is watching (SPEC §16)."""
    created = project(client)
    run_id = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]

    channel = MemoryEvents()
    use_events(channel)
    for event in (
        {"kind": "stage", "stage": "capture"},
        {"kind": "page", "stage": "capture", "path": "/", "status": 200},
        {"kind": "issue", "stage": "check", "severity": "major", "title": "Something"},
        {"kind": "stage", "stage": "done", "issues": 1},
    ):
        channel.publish(run_id, event)

    seen = _read_stream(client, run_id)
    assert [e["kind"] for e in seen] == ["stage", "page", "issue", "stage"]
    assert seen[-1]["stage"] == "done"


def test_the_stream_can_resume_from_where_a_client_left_off(client: TestClient) -> None:
    created = project(client)
    run_id = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
    channel = MemoryEvents()
    use_events(channel)
    channel.publish(run_id, {"kind": "stage", "stage": "capture"})
    channel.publish(run_id, {"kind": "page", "stage": "capture", "path": "/a"})
    channel.publish(run_id, {"kind": "stage", "stage": "done"})

    assert len(_read_stream(client, run_id)) == 3
    assert len(_read_stream(client, run_id, after=2)) == 1


def _read_stream(client: TestClient, run_id: str, after: int = 0) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []
    with client.stream("GET", f"/api/runs/{run_id}/events", params={"after": after}) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        for line in response.iter_lines():
            if line.startswith("data: "):
                seen.append(json.loads(line[6:]))
    return seen


def test_a_queued_run_is_accepted_without_blocking(client: TestClient) -> None:
    """SPEC §15's CI hook: POST a run, get an id back, watch it happen."""
    created = project(client, target="http://127.0.0.1:9/unreachable")
    response = client.post(f"/api/projects/{created['id']}/runs", json={"triggeredBy": "ci"})
    assert response.status_code == 202
    assert response.json()["state"] == "queued"
    assert response.json()["reportUrl"] is None


@pytest.mark.browser
def test_a_run_that_cannot_reach_its_target_fails_with_a_reason(
    client: TestClient, browser_ready: None
) -> None:
    created = project(client, target="http://127.0.0.1:9/unreachable")
    run_id = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
    body = _await_run(client, run_id)
    assert body["state"] in ("failed", "aborted"), body
    assert body["error"], "a failed run must say why"


@pytest.mark.browser
def test_a_full_run_is_triggerable_and_watchable(client: TestClient, browser_ready: None) -> None:
    """The done-when: triggered from the API, watched live, issues readable afterwards."""
    from tests.fixtures.app import serve

    server, base, _ = serve()
    try:
        created = project(
            client,
            target=f"{base}/app/",
            config={
                "viewports": [{"name": "desktop_1440", "width": 1440, "height": 900}],
                "maxPages": 3,
                "maxDepth": 1,
                "settleMs": 120,
                "flows": False,
                "apiProbes": False,
            },
        )
        run_id = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        body = _await_run(client, run_id, timeout=300)
    finally:
        server.shutdown()
        server.server_close()

    assert body["state"] == "complete", body
    assert body["pages"] >= 1
    assert body["issues"] > 0
    assert body["reportUrl"]
    assert client.get(f"/api/runs/{run_id}/report").status_code == 200

    issues = client.get(f"/api/projects/{created['id']}/issues").json()
    assert issues and issues[0]["payload"]["title"]

    events = _read_stream(client, run_id)
    kinds = {event["kind"] for event in events}
    assert {"stage", "page", "issue"} <= kinds, kinds
    assert events[-1]["stage"] == "done"


def _await_run(client: TestClient, run_id: str, timeout: float = 180) -> dict[str, Any]:
    deadline = time.time() + timeout
    body: dict[str, Any] = {}
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in ("complete", "failed", "aborted"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def test_the_report_endpoint_needs_a_report(client: TestClient, tmp_path: Path) -> None:
    _, run_id = index_fixture(client, tmp_path)
    assert client.get(f"/api/runs/{run_id}/report").status_code == 404


# ------------------------------------------------------- phase 8: the lifecycle


def test_a_dismissal_survives_a_re_run_forever(client: TestClient, tmp_path: Path) -> None:
    """SPEC §1.7 and the phase 8 done-when: dismiss fifteen, re-run, see none of them."""
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    assert len(issues) > 15

    dismissed = [issue["id"] for issue in issues[:15]]
    for issue_id in dismissed:
        response = client.patch(
            f"/api/issues/{issue_id}",
            json={"state": "dismissed", "reason": "known", "by": "joujou"},
        )
        assert response.status_code == 200

    index_fixture(client, tmp_path, project_id=project_id)

    after = {issue["id"] for issue in client.get(f"/api/projects/{project_id}/issues").json()}
    assert not (set(dismissed) & after), "a dismissed issue came back"

    board = client.get(f"/api/projects/{project_id}/board").json()
    column = next(c for c in board["columns"] if c["state"] == "dismissed")
    assert len(column["issues"]) == 15, "they are still there, just not in anyone's way"


def test_the_board_is_a_view_over_the_issues(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    issue = client.get(f"/api/projects/{project_id}/issues").json()[0]
    client.patch(
        f"/api/issues/{issue['id']}",
        json={"state": "confirmed", "assignee": "sam", "labels": ["checkout", " checkout "]},
    )
    board = client.get(f"/api/projects/{project_id}/board").json()

    assert [c["state"] for c in board["columns"]] == [
        "new",
        "confirmed",
        "regressed",
        "fixed",
        "wont_fix",
        "dismissed",
    ]
    confirmed = next(c for c in board["columns"] if c["state"] == "confirmed")
    assert [i["id"] for i in confirmed["issues"]] == [issue["id"]]
    assert confirmed["issues"][0]["labels"] == ["checkout"], "labels are de-duped and trimmed"
    assert board["assignees"] == ["sam"]
    assert board["labels"] == ["checkout"]


def test_a_comment_becomes_a_knowledge_draft_not_a_rule(client: TestClient, tmp_path: Path) -> None:
    """SPEC §13's loop, respecting §10: the next run knows once a human confirms."""
    project_id, _ = index_fixture(client, tmp_path)
    issue = client.get(f"/api/projects/{project_id}/issues").json()[0]

    comment = client.post(
        f"/api/issues/{issue['id']}/comments",
        json={
            "author": "dev@client",
            "body": "the client changed this, ignore it",
            "intoKnowledge": True,
        },
    ).json()
    assert comment["knowledgeId"]

    drafts = client.get(f"/api/projects/{project_id}/knowledge").json()
    draft = next(k for k in drafts if k["id"] == comment["knowledgeId"])
    assert draft["source"] == "comment"
    assert draft["confirmed"] is False, "a comment is not a decision"
    assert draft["raw"] == "the client changed this, ignore it"


def test_a_plain_comment_leaves_the_knowledge_store_alone(
    client: TestClient, tmp_path: Path
) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    issue = client.get(f"/api/projects/{project_id}/issues").json()[0]
    client.post(
        f"/api/issues/{issue['id']}/comments",
        json={"author": "dev@client", "body": "looking at it tomorrow"},
    )
    assert client.get(f"/api/projects/{project_id}/knowledge").json() == []


def test_a_dismissal_reason_can_become_knowledge(client: TestClient, tmp_path: Path) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    client.patch(
        f"/api/issues/{issues[0]['id']}",
        json={"state": "dismissed", "reason": "duplicate of the one above", "by": "joujou"},
    )
    assert client.get(f"/api/projects/{project_id}/knowledge").json() == []

    client.patch(
        f"/api/issues/{issues[1]['id']}",
        json={
            "state": "dismissed",
            "reason": "client asked for the tiles to be 40px apart",
            "by": "joujou",
            "intoKnowledge": True,
        },
    )
    drafts = client.get(f"/api/projects/{project_id}/knowledge").json()
    assert [k["source"] for k in drafts] == ["dismissal"]


def test_entries_are_editable_before_they_are_confirmed(client: TestClient, tmp_path: Path) -> None:
    """The model's reading is a draft. What gets applied is what the human agreed to."""
    project_id, _ = index_fixture(client, tmp_path)
    draft = client.post(
        f"/api/projects/{project_id}/knowledge",
        json={"raw": "the tiles are 40px apart on purpose now"},
    ).json()

    updated = client.patch(
        f"/api/knowledge/{draft['id']}",
        json={
            "confirm": True,
            "entries": [
                {"kind": "ignore", "scope": "checker:layout.spacing-scale", "note": "client"},
                {"kind": "override", "scope": "no prefix here"},
            ],
        },
    ).json()
    assert updated["confirmed"] is True
    assert [e["scope"] for e in updated["entries"]] == ["checker:layout.spacing-scale"]
    assert updated["entries"][0]["assertPresence"] is False


def test_run_diff_is_readable_when_there_is_one(client: TestClient, tmp_path: Path) -> None:
    _, run_id = index_fixture(client, tmp_path)
    assert client.get(f"/api/runs/{run_id}/diff").status_code == 404, "no artifact diff yet"

    artifact = checked_artifact(tmp_path, "broken")
    (artifact / "diff.json").write_text(
        json.dumps({"baseRunId": "run_before", "entries": []}),
    )
    body = client.get(f"/api/runs/{run_id}/diff").json()
    assert body["baseRunId"] == "run_before"


def test_a_comment_changes_what_the_next_run_reports(
    client: TestClient, browser_ready: None
) -> None:
    """The phase 8 done-when, end to end.

    Run, comment "the client asked for this", confirm the entry, run again — and the
    second run both stops reporting it and says out loud whether the change was made.
    """
    from tests.fixtures.app import serve

    scripted = ScriptedProvider(
        {
            "knowledge:parse": json.dumps(
                [
                    {
                        "kind": "override",
                        "scope": "selector:button",
                        "property": "backgroundColor",
                        "expected": "#1c64c8",
                        "note": "client picked this blue themselves",
                    },
                    {
                        "kind": "override",
                        "scope": "selector:button",
                        "property": "borderRadius",
                        "expected": "16px",
                        "note": "client asked for rounder buttons",
                    },
                ]
            )
        }
    )
    use_provider(scripted)
    server, base, _ = serve()
    try:
        created = project(
            client,
            target=f"{base}/app/",
            config={
                "viewports": [{"name": "desktop_1440", "width": 1440, "height": 900}],
                "maxPages": 2,
                "maxDepth": 1,
                "settleMs": 120,
                "flows": False,
                "apiProbes": False,
            },
        )
        first = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        _await_run(client, first, timeout=300)

        issue = client.get(f"/api/projects/{created['id']}/issues").json()[0]
        comment = client.post(
            f"/api/issues/{issue['id']}/comments",
            json={
                "author": "dev@client",
                "body": "the client picked that blue and wants rounder buttons",
                "intoKnowledge": True,
            },
        ).json()
        client.patch(f"/api/knowledge/{comment['knowledgeId']}", json={"confirm": True})

        second = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        body = _await_run(client, second, timeout=300)
    finally:
        use_provider(None)
        server.shutdown()
        server.server_close()

    assert body["state"] == "complete", body
    assert body["baseRunId"] == first, "a second run is diffed against the first"
    assert body["diff"], "SPEC §11: every run after the first arrives as a diff"

    artifact = Path(client.get(f"/api/runs/{second}").json()["artifactRunId"] or "")
    assert artifact.name, "the run wrote an artifact"

    knowledge_file = json.loads(
        (Path(_artifact_path(client, second)) / "knowledge.json").read_text()
    )
    assert [n["raw"] for n in knowledge_file["notes"]], "the run carried the confirmed note"
    verdicts = {c["entry"]["property"]: c["verdict"] for c in knowledge_file["changes"]}
    assert verdicts["backgroundColor"] == "applied", "the blue really is that blue"
    assert verdicts["borderRadius"] == "not-applied", "the rounder buttons never happened"

    report = client.get(f"/api/runs/{second}/report").text
    assert "Requested change confirmed present" in report
    assert "Requested change NOT applied" in report
    assert "Since the last run" in report


def _artifact_path(client: TestClient, run_id: str) -> str:
    with db.session() as session:
        run = session.get(Run, run_id)
        assert run is not None and run.artifactPath
        return run.artifactPath


# ------------------------------------------------------ phase 10: flake control


def test_a_finding_that_comes_and_goes_is_tagged_flaky(client: TestClient, tmp_path: Path) -> None:
    """SPEC §5: appeared, vanished, came back. Grouped apart and never called a
    regression, because a regression is something a person did."""
    project_id, _ = index_fixture(client, tmp_path)
    issues = client.get(f"/api/projects/{project_id}/issues").json()
    victim = issues[0]

    # Run two: everything except the one issue.
    artifact = checked_artifact(tmp_path, "broken")
    payload = json.loads((artifact / "issues.json").read_text())
    trimmed = tmp_path / "trimmed"
    shutil.copytree(artifact, trimmed, dirs_exist_ok=True)
    kept = [i for i in payload["issues"] if i["fingerprint"] != victim["fingerprint"]]
    (trimmed / "issues.json").write_text(json.dumps({**payload, "issues": kept}))
    _index(client, trimmed, project_id)

    after_two = _issue(client, project_id, victim["fingerprint"])
    assert after_two["state"] == "fixed"
    assert after_two["flaky"] is False, "gone once is just gone"

    # Run three: it is back.
    _index(client, artifact, project_id)
    after_three = _issue(client, project_id, victim["fingerprint"])
    assert after_three["flaky"] is True
    assert after_three["state"] != "regressed", "flaky findings do not fill the regressed column"


def test_a_flaky_finding_that_vanishes_again_is_not_called_fixed(
    client: TestClient, tmp_path: Path
) -> None:
    project_id, _ = index_fixture(client, tmp_path)
    victim = client.get(f"/api/projects/{project_id}/issues").json()[0]
    artifact = checked_artifact(tmp_path, "broken")
    payload = json.loads((artifact / "issues.json").read_text())
    trimmed = tmp_path / "trimmed2"
    shutil.copytree(artifact, trimmed, dirs_exist_ok=True)
    kept = [i for i in payload["issues"] if i["fingerprint"] != victim["fingerprint"]]
    (trimmed / "issues.json").write_text(json.dumps({**payload, "issues": kept}))

    for source in (trimmed, artifact, trimmed):
        _index(client, source, project_id)

    row = _issue(client, project_id, victim["fingerprint"])
    assert row["flaky"] is True
    assert row["state"] != "fixed", "it is not fixed, it is intermittent"


def _index(client: TestClient, artifact: Path, project_id: str) -> str:
    with db.session() as session:
        run = Run(projectId=project_id, state=RunState.complete, artifactPath=str(artifact))
        session.add(run)
        session.commit()
        session.refresh(run)
        index_run(session, run, artifact)
        return run.id


def _issue(client: TestClient, project_id: str, fingerprint: str) -> dict[str, Any]:
    issues = client.get(f"/api/projects/{project_id}/issues?include_dismissed=true").json()
    return next(i for i in issues if i["fingerprint"] == fingerprint)


# ------------------------------------------------- phase 1 of the reality check


def test_a_bare_postgres_url_points_at_the_driver_we_ship() -> None:
    """SPEC §17's database. Everyone writes `postgresql://` — Postgres prints it, hosting
    providers hand it out, and `docker compose` had it — and SQLAlchemy reads that as
    psycopg2, which we do not ship. This went unnoticed until compose was run for the
    first time, because every test until then used SQLite, which needs no driver."""
    assert db.normalise("postgresql://u:p@host:5432/bureau") == (
        "postgresql+psycopg://u:p@host:5432/bureau"
    )
    assert db.normalise("postgres://u:p@host/bureau").startswith("postgresql+psycopg://")
    assert db.normalise("sqlite:///./bureau.db") == "sqlite:///./bureau.db"
    assert db.normalise("postgresql+psycopg://u@h/db") == "postgresql+psycopg://u@h/db"


def test_the_driver_is_actually_installed() -> None:
    """A declared dependency that nothing imports is a dependency nobody checks."""
    import importlib.util

    assert importlib.util.find_spec("psycopg") is not None, (
        "psycopg is declared in the `api` extra but is not installed here; "
        "run `pip install -e '.[api]'`"
    )
