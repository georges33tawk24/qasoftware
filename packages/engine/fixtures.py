"""Frozen run artifacts for checker unit tests.

`fixtures/` holds whole run directories captured once and never regenerated. Checker
tests assert against these, never against a live site.
"""

from __future__ import annotations

from pathlib import Path

from engine.artifact.context import RunContext

FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"


def available() -> list[str]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p.name for p in FIXTURES_DIR.iterdir() if (p / "run.json").is_file())


def fixture_path(name: str) -> Path:
    path = FIXTURES_DIR / name
    if not (path / "run.json").is_file():
        raise FileNotFoundError(f"no fixture {name!r} in {FIXTURES_DIR}; have {available()}")
    return path


def load_fixture(name: str) -> RunContext:
    return RunContext.open(fixture_path(name))
