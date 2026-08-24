"""One-shot builder for `fixtures/exercised`.

Captures the fixture application, exercises it, then freezes the result so the group H
and I checkers can be tested without a browser and without a server.

Step screenshots are downscaled and passing flows are dropped: the frozen artifact exists
to test the checkers, and a full-resolution copy of every screenshot of every flow that
worked is five megabytes of nothing.

Run with: .venv/bin/python -m tests.build_flow_fixture
"""

from __future__ import annotations

import asyncio
import os
import shutil
from pathlib import Path

from PIL import Image

from engine.artifact.context import RunContext
from engine.artifact.models import VIEWPORT_PRESETS, FlowStatus, RunConfig
from engine.artifact.store import RunPaths
from engine.capture.auth import Persona
from engine.capture.exercise import exercise
from engine.capture.run import capture
from tests.fixtures.app import USERS, serve

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "exercised"
TMP = ROOT / "fixtures" / "_flows_tmp"
SHOT_WIDTH = 760
KEEP_TRACE = "ada-sign-in-stay-signed-in-sign-out"
"""One real trace is enough to prove the pipeline attaches one."""

ACCOUNTS = list(USERS.items())


def persona(name: str, base: str, index: int) -> Persona:
    user, password = ACCOUNTS[index]
    os.environ[f"FIXTURE_USER_{index}"] = user
    os.environ[f"FIXTURE_PASSWORD_{index}"] = password
    return Persona.model_validate(
        {
            "name": name,
            "login": {
                "url": f"{base}/app/login",
                "usernameSelector": "#email",
                "passwordSelector": "#password",
                "usernameRef": f"env:FIXTURE_USER_{index}",
                "passwordRef": f"env:FIXTURE_PASSWORD_{index}",
                "submitSelector": "#login-submit",
                "successSelector": "h1",
            },
            "sessionCheck": {"url": f"{base}/app/account", "loggedInSelector": "#logout"},
        }
    )


def shrink(directory: Path) -> None:
    for shot in directory.rglob("step_*.png"):
        with Image.open(shot) as image:
            if image.width <= SHOT_WIDTH:
                continue
            ratio = SHOT_WIDTH / image.width
            image.convert("RGB").resize(
                (SHOT_WIDTH, max(1, int(image.height * ratio))), Image.Resampling.LANCZOS
            ).save(shot, format="PNG", optimize=True)


async def build() -> None:
    server, base, _ = serve()
    try:
        config = RunConfig(
            viewports=[VIEWPORT_PRESETS["desktop_1440"]],
            maxPages=8,
            maxDepth=2,
            settleMs=150,
            authorisedBy="Jo Blake (client CTO)",
            authorisedHosts=[base.split("//")[1]],
        )
        result = await capture(base + "/app/", TMP, config=config)
        ctx = RunContext.open(result.paths.root)
        personas = [persona("ada", base, 0), persona("grace", base, 1)]
        await exercise(RunPaths(result.paths.root), ctx, personas=personas)
    finally:
        server.shutdown()
        server.server_close()

    paths = RunPaths(result.paths.root)
    for record in RunContext.open(paths.root).flows():
        directory = paths.flow_dir(record.id)
        if record.status is FlowStatus.passed:
            shutil.rmtree(directory, ignore_errors=True)
            continue
        if record.id != KEEP_TRACE:
            (directory / "trace.zip").unlink(missing_ok=True)
    shrink(paths.flows)

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(paths.root, OUT)
    shutil.rmtree(TMP, ignore_errors=True)
    size = sum(f.stat().st_size for f in OUT.rglob("*") if f.is_file())
    print(f"wrote {OUT} ({size / 1_000_000:.1f}MB)")


if __name__ == "__main__":
    asyncio.run(build())
