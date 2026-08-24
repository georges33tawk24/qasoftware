"""Many projects, side by side, sharing nothing — the multi-tenancy audit.

Every credential in this product is a *reference* stored per project and resolved at run
time. These tests exist because "it works on my machine with my token in the environment"
is exactly how a product becomes single-tenant without anyone deciding to make it one.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from bureau_api import db
from bureau_api.events import MemoryEvents
from bureau_api.events import use as use_events
from bureau_api.jobs import InlineQueue, _provider, _request, _secret
from bureau_api.jobs import use as use_queue
from bureau_api.main import app
from bureau_api.models import Project, Run
from engine.capture.auth import ANONYMOUS


@pytest.fixture
def client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    monkeypatch.setenv("ARTIFACTS_DIR", str(tmp_path / "runs"))
    monkeypatch.setenv("BUREAU_SCHEDULER", "0")
    monkeypatch.delenv("REDIS_URL", raising=False)
    for leaked in ("ANTHROPIC_API_KEY", "GOOGLE_API_KEY", "OPENAI_API_KEY", "BUREAU_PROVIDER"):
        monkeypatch.delenv(leaked, raising=False)
    db.reset(f"sqlite:///{tmp_path}/control.db")
    use_events(MemoryEvents())
    use_queue(InlineQueue())
    with TestClient(app) as active:
        yield active


def make(client: TestClient, **over: object) -> dict[str, object]:
    payload: dict[str, object] = {"name": "A", "target": "https://a.test/"}
    payload.update(over)
    response = client.post("/api/projects", json=payload)
    assert response.status_code == 201, response.text
    body: dict[str, object] = response.json()
    return body


def test_two_projects_carry_their_own_everything(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk-through: two clients, two design files, two trackers, two targets."""
    monkeypatch.setenv("ACME_FIGMA", "figd_acme")
    monkeypatch.setenv("BETA_FIGMA", "figd_beta")

    acme = make(
        client,
        name="Acme",
        target="https://acme.test/",
        figmaFileKey="AAA111",
        figmaTokenRef="env:ACME_FIGMA",
    )
    beta = make(
        client,
        name="Beta",
        target="https://beta.test/",
        figmaFileKey="BBB222",
        figmaTokenRef="env:BETA_FIGMA",
    )
    client.post(
        f"/api/projects/{acme['id']}/exports",
        json={
            "kind": "jira",
            "config": {
                "baseUrl": "https://acme.atlassian.net",
                "project": "ACME",
                "tokenEnv": "ACME_JIRA",
            },
        },
    )
    client.post(
        f"/api/projects/{beta['id']}/exports",
        json={"kind": "github", "config": {"project": "beta/site", "tokenEnv": "BETA_GH"}},
    )

    with db.session() as session:
        first = session.get(Project, acme["id"])
        second = session.get(Project, beta["id"])
        assert first is not None and second is not None
        one = _request(session, first, Run(projectId=first.id), [ANONYMOUS])
        two = _request(session, second, Run(projectId=second.id), [ANONYMOUS])

    assert (one.target, two.target) == ("https://acme.test/", "https://beta.test/")
    assert (one.figma_key, two.figma_key) == ("AAA111", "BBB222")
    assert (one.figma_token, two.figma_token) == ("figd_acme", "figd_beta")
    assert one.out_dir != two.out_dir, "artifacts never share a directory"

    exports = {p["id"]: client.get(f"/api/projects/{p['id']}/exports").json() for p in (acme, beta)}
    assert exports[acme["id"]][0]["config"]["tokenEnv"] == "ACME_JIRA"
    assert exports[beta["id"]][0]["config"]["project"] == "beta/site"
    assert len(exports[beta["id"]]) == 1, "a project sees only its own targets"


def test_a_project_is_optional_about_figma(client: TestClient) -> None:
    """Figma is optional — SPEC §6. A project without it runs everything else."""
    plain = make(client, name="No design", target="https://c.test/")
    assert plain["figmaFileKey"] is None
    with db.session() as session:
        project = session.get(Project, plain["id"])
        assert project is not None
        request = _request(session, project, Run(projectId=project.id), [ANONYMOUS])
    assert request.figma_key is None
    assert request.figma_token is None


def test_no_credential_is_ever_returned_by_the_api(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACME_FIGMA", "figd_the_actual_secret")
    created = make(client, figmaTokenRef="env:ACME_FIGMA")
    body = client.get(f"/api/projects/{created['id']}").text
    assert "figd_the_actual_secret" not in body
    assert client.get(f"/api/projects/{created['id']}").json()["credentials"] == {"figma": "ok"}


def test_an_unset_reference_says_so_without_failing_the_project(client: TestClient) -> None:
    created = make(client, figmaTokenRef="env:NOT_SET_ANYWHERE")
    status = client.get(f"/api/projects/{created['id']}").json()["credentials"]
    assert "NOT_SET_ANYWHERE" in status["figma"]
    assert _secret("env:NOT_SET_ANYWHERE", "figma") is None, "a run continues without it"


def test_with_no_key_anywhere_the_run_is_deterministic_only(client: TestClient) -> None:
    """A normal way to run this product, not a failure: the sweep runs, the agents do
    not, and nothing raises."""
    created = make(client)
    with db.session() as session:
        project = session.get(Project, created["id"])
        assert project is not None
        assert _provider(project) is None
        request = _request(session, project, Run(projectId=project.id), [ANONYMOUS])
    assert request.provider is None


def test_a_project_can_name_its_own_model_key(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ACME_MODEL", "sk-acme")
    created = make(client, modelTokenRef="env:ACME_MODEL", provider="anthropic")
    with db.session() as session:
        project = session.get(Project, created["id"])
        assert project is not None
        # The SDK may not be installed here; what matters is that the reference resolved
        # and the deployment's own key was not silently used instead.
        assert _secret(project.modelTokenRef, "model") == "sk-acme"


def test_no_fixture_is_reachable_from_a_production_path() -> None:
    """`engine.fixtures` is test scaffolding. If anything shipped imported it, a real run
    could quietly read a frozen artifact instead of the site in front of it."""
    root = Path(__file__).resolve().parents[1]
    sources = [
        path
        for directory in ("packages", "apps")
        for path in (root / directory).rglob("*.py")
        if "__pycache__" not in path.parts and "node_modules" not in path.parts
    ]
    guilty = [
        str(path.relative_to(root))
        for path in sources
        if path.name != "fixtures.py" and "engine.fixtures" in path.read_text()
    ]
    assert not guilty, f"production code imports fixtures: {guilty}"


def test_the_agent_layer_survives_the_worker_s_event_loop(
    client: TestClient, browser_ready: None
) -> None:
    """A run started from the API is already inside an event loop.

    This never had a test because the agent layer was only ever driven from the CLI,
    where `asyncio.run` is legal. From the worker it raised `asyncio.run() cannot be
    called from a running event loop` and the run failed — with the failure attributed
    to the run rather than to the pipeline, which is how it stayed hidden.
    """
    import json

    from bureau_api.knowledge import use as use_provider
    from engine.agents.providers.scripted import ScriptedProvider
    from tests.fixtures.app import serve

    scripted = ScriptedProvider(default=json.dumps([]))
    use_provider(scripted)
    server, base, _ = serve()
    try:
        created = make(
            client,
            name="Agents",
            target=f"{base}/app/",
            config={
                "viewports": [{"name": "desktop_1440", "width": 1440, "height": 900}],
                "maxPages": 1,
                "maxDepth": 0,
                "settleMs": 120,
                "flows": False,
                "apiProbes": False,
            },
        )
        run_id = client.post(f"/api/projects/{created['id']}/runs", json={}).json()["id"]
        body = _await(client, run_id)
    finally:
        use_provider(None)
        server.shutdown()
        server.server_close()

    assert body["state"] == "complete", body
    assert scripted.calls, "the agents actually ran, rather than being skipped"


def _await(client: TestClient, run_id: str, timeout: float = 300) -> dict[str, object]:
    import time

    deadline = time.time() + timeout
    body: dict[str, object] = {}
    while time.time() < deadline:
        body = client.get(f"/api/runs/{run_id}").json()
        if body["state"] in ("complete", "failed", "aborted"):
            return body
        time.sleep(0.25)
    raise AssertionError(f"run {run_id} did not finish: {body}")


def test_a_literal_credential_never_reaches_the_database(client: TestClient) -> None:
    """Refusing at resolve time is too late — by then it is stored and readable.

    CLAUDE.md: no secrets anywhere durable, project JSON included.
    """
    response = client.post(
        "/api/projects",
        json={"name": "acme", "target": "https://acme.test/", "figmaTokenRef": "figd_real_token"},
    )
    assert response.status_code == 422
    assert "not a secret reference" in response.text
    assert "figd_real_token" not in str(client.get("/api/projects").json())


def test_a_literal_inside_persona_config_is_refused_too(client: TestClient) -> None:
    """Personas keep their refs nested, so the walk has to go down into them."""
    project = make(client)
    response = client.post(
        f"/api/projects/{project['id']}/personas",
        json={
            "name": "ada",
            "config": {"login": {"usernameRef": "env:ADA_USER", "passwordRef": "hunter2"}},
        },
    )
    assert response.status_code == 422
    assert "not a secret reference" in response.text


def test_a_proper_reference_still_passes(client: TestClient) -> None:
    project = make(client)
    response = client.post(
        f"/api/projects/{project['id']}/personas",
        json={
            "name": "ada",
            "config": {"login": {"usernameRef": "env:ADA_USER", "passwordRef": "env:ADA_PASS"}},
        },
    )
    assert response.status_code < 300, response.text
