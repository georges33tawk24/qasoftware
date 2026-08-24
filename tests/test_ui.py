"""Bureau checked by Bureau — SPEC §16.

A QA tool whose own interface fails the checks it ships is not credible, so the a11y
sweep runs against our own UI in CI. The gate is deliberately narrow: the accessibility
and layout checkers, which are ours to get right, and not the security-header or HTTPS
checkers, which belong to whatever fronts the app in production.

Needs the web app built with `NEXT_PUBLIC_API_ORIGIN` pointing at `API_PORT` below,
because Next inlines it at build time. `make dogfood` does both halves; the test skips
cleanly when the build is missing.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from urllib.error import URLError
from urllib.request import ProxyHandler, build_opener

import pytest

from bureau_api import db
from bureau_api.issues import index_run
from bureau_api.models import Project, Run, RunState
from engine.artifact.context import RunContext
from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
from engine.artifact.store import RunPaths
from engine.capture.run import capture
from engine.checkers import runner
from engine.fixtures import fixture_path
from engine.issues.models import Issue, Severity

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "apps" / "web"
API_PORT = 8099
WEB_PORT = 3099
API_ORIGIN = f"http://127.0.0.1:{API_PORT}"

GATED = ("a11y.", "layout.", "typography.", "responsive.")
"""What the UI owns.

`free.*` and `performance.*` describe the deployment — a dev server over plain HTTP with
no CDN — and `content.*` reads the fixture's issue titles rather than our own copy, so a
gate on either would fail for reasons that have nothing to do with the interface. A gate
people learn to ignore is worse than no gate.
"""

TOLERATED = {
    # The evidence viewer's screenshot pane scrolls its own overflow, which is the point
    # of a pane that shows a 1440px capture inside a 520px column.
    "layout.horizontal-overflow",
}


# A local address must not go through whatever proxy the developer's machine is
# configured with, which is exactly what the default opener would do.
_direct = build_opener(ProxyHandler({}))


def _built_against(origin: str) -> bool:
    return any(
        origin in chunk.read_text(errors="ignore")
        for chunk in (WEB / ".next" / "static").rglob("*.js")
    )


def _free(port: int) -> bool:
    with socket.socket() as probe:
        return probe.connect_ex(("127.0.0.1", port)) != 0


def _wait(url: str, proc: subprocess.Popen[bytes], log: Path, *, timeout: float = 60.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"{url} exited with {proc.returncode}: {_tail(log)}")
        try:
            with _direct.open(url, timeout=2):
                return
        except (URLError, OSError):
            time.sleep(0.3)
    raise TimeoutError(f"{url} never came up: {_tail(log)}")


def _tail(log: Path) -> str:
    """Whatever the process managed to say before it gave up, so a CI failure is readable."""
    return log.read_text()[-2000:] if log.exists() else "<no log>"


def _seed(tmp_path: Path) -> str:
    """A project with one finished run, so the UI has something real to render."""
    artifact = tmp_path / "artifact"
    shutil.copytree(fixture_path("broken"), artifact)
    ctx = RunContext.open(artifact)
    runner.write(RunPaths(artifact), ctx, runner.check(ctx))

    db.reset(f"sqlite:///{tmp_path}/control.db")
    with db.session() as session:
        project = Project(
            name="Fixture application",
            target="http://127.0.0.1:8020/app/",
            authorisedBy="Jo Blake (client CTO)",
        )
        session.add(project)
        session.commit()
        session.refresh(project)
        run = Run(projectId=project.id, state=RunState.complete, artifactPath=str(artifact))
        session.add(run)
        session.commit()
        session.refresh(run)
        index_run(session, run, artifact)
        return project.id


@pytest.fixture(scope="module")
def own_ui(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    if not (WEB / ".next" / "BUILD_ID").exists():
        pytest.skip("apps/web is not built — run `make dogfood`")
    if not _built_against(API_ORIGIN):
        # Next inlines the origin, so a build made for another port would quietly test
        # whatever else is listening there. Better to skip than to pass for free.
        pytest.skip(f"apps/web was not built with NEXT_PUBLIC_API_ORIGIN={API_ORIGIN}")
    if shutil.which("npm") is None:
        pytest.skip("npm unavailable")
    if not (_free(API_PORT) and _free(WEB_PORT)):
        pytest.skip(f"ports {API_PORT}/{WEB_PORT} are busy")

    tmp_path = tmp_path_factory.mktemp("ui")
    project_id = _seed(tmp_path)

    env = os.environ | {
        "DATABASE_URL": f"sqlite:///{tmp_path}/control.db",
        "ARTIFACTS_DIR": str(tmp_path / "runs"),
        "NEXT_PUBLIC_API_ORIGIN": API_ORIGIN,
        "PYTHONPATH": str(ROOT / "packages"),
    }
    env.pop("REDIS_URL", None)
    procs = [
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "--app-dir",
                "apps/api",
                "bureau_api.main:app",
                "--port",
                str(API_PORT),
            ],
            cwd=ROOT,
            env=env,
            stdout=(tmp_path / "api.log").open("wb"),
            stderr=subprocess.STDOUT,
        ),
        subprocess.Popen(
            ["npm", "run", "start", "--", "--port", str(WEB_PORT)],
            cwd=WEB,
            env=env,
            stdout=(tmp_path / "web.log").open("wb"),
            stderr=subprocess.STDOUT,
        ),
    ]
    try:
        _wait(f"{API_ORIGIN}/api/health", procs[0], tmp_path / "api.log")
        _wait(f"http://127.0.0.1:{WEB_PORT}/", procs[1], tmp_path / "web.log")
        yield f"http://127.0.0.1:{WEB_PORT}/projects/{project_id}"
    finally:
        for proc in procs:
            proc.terminate()
            proc.wait(timeout=20)


@pytest.fixture(scope="module", params=["dark", "light"])
def own_issues(
    request: pytest.FixtureRequest,
    own_ui: str,
    tmp_path_factory: pytest.TempPathFactory,
    browser_ready: None,
) -> list[Issue]:
    """Both themes, because §16 specifies both and contrast is a per-theme fact."""
    scheme: str = request.param
    out = tmp_path_factory.mktemp(f"dogfood-{scheme}")
    config = RunConfig(
        maxPages=3,
        authorisedBy="ourselves",
        authorisedHosts=["127.0.0.1"],
        colourScheme=scheme,  # type: ignore[arg-type]  # pytest params are plain strings
        viewports=[VIEWPORT_PRESETS["desktop_1440"], VIEWPORT_PRESETS["mobile_390"]],
    )
    result = asyncio.run(capture(own_ui, out, config=config))
    ctx = RunContext.open(result.paths.root)
    return runner.check(ctx).issues


def _gated(issues: list[Issue]) -> list[Issue]:
    return [
        issue
        for issue in issues
        if issue.checkerId.startswith(GATED)
        and issue.checkerId not in TOLERATED
        and issue.severity.rank <= Severity.major.rank  # 0 is worst
    ]


def test_our_own_ui_passes_our_own_checks(own_issues: list[Issue]) -> None:
    """The quality floor, enforced against the thing that claims to enforce it.

    The gate is `major` or worse rather than the `critical` the build phase asked for,
    deliberately: the point is that it never fires. A gate set where things sometimes
    land is a gate people learn to re-run until it passes.
    """
    failures = _gated(own_issues)
    assert not failures, "\n".join(
        f"{i.severity.value:8} {i.checkerId:28} {i.title} "
        f"[{i.instances[0].viewport if i.instances else '?'}] "
        f"({i.instances[0].selector if i.instances else '?'}) {i.actual or ''}"
        for i in failures
    )


def test_the_dogfood_run_actually_captured_something(own_issues: list[Issue]) -> None:
    """A capture that silently caught nothing would make the gate above pass for free."""
    assert own_issues, "no issues at all means the sweep never saw the page"
