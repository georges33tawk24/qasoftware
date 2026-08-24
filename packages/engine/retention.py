"""Artifact retention — SPEC §5's hardening.

**Issues are kept forever; screenshots are not.** The index holds the finding, its
history and every decision anyone made about it, and none of that is large. What fills a
disk is pictures and video: a twenty-page site at four viewports is a couple of hundred
megabytes a run, and a nightly schedule turns that into a filing cabinet nobody opens.

So: keep the whole artifact for the last N runs, then strip the heavy files and leave the
measurements. An old run stays readable, re-checkable and diffable — it just stops
carrying its screenshots.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path

KEEP_MEDIA_RUNS = 10
"""How many runs keep their pictures. Ten is a fortnight of nightlies, which is longer
than anyone looks back at a screenshot in practice."""

MEDIA_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp", ".webm", ".zip")
"""Screenshots, video, and Playwright traces. `.zip` is only ever a trace in here."""

KEEP_ALWAYS = ("report.html",)
"""The report inlines its own evidence, so it survives its screenshots."""


@dataclass
class Pruned:
    runs: int = 0
    files: int = 0
    bytes: int = 0
    kept: list[str] = field(default_factory=list)

    @property
    def megabytes(self) -> float:
        return round(self.bytes / 1_000_000, 1)


def media_files(run_dir: Path) -> Iterable[Path]:
    for path in sorted(run_dir.rglob("*")):
        if not path.is_file() or path.name in KEEP_ALWAYS:
            continue
        if path.suffix.lower() in MEDIA_SUFFIXES:
            yield path


def prune_run(run_dir: Path, *, dry_run: bool = False) -> Pruned:
    """Strip one run's media. The measurements, issues and diff stay put."""
    result = Pruned(runs=1)
    for path in media_files(run_dir):
        result.files += 1
        result.bytes += path.stat().st_size
        if not dry_run:
            path.unlink()
    if not dry_run and result.files:
        marker = run_dir / "pruned.json"
        marker.write_text(
            json.dumps({"media": "pruned", "files": result.files, "bytes": result.bytes}, indent=2)
            + "\n",
            encoding="utf-8",
        )
    return result


def prune(project_dir: Path, *, keep: int = KEEP_MEDIA_RUNS, dry_run: bool = False) -> Pruned:
    """Keep the newest `keep` runs whole; strip the media from everything older.

    Ordered by directory name, which is `run_<timestamp>` — chronological by construction
    and stable regardless of what a filesystem reports for mtime after a copy.
    """
    runs = sorted(
        (path for path in project_dir.iterdir() if path.is_dir() and (path / "run.json").is_file()),
        key=lambda path: path.name,
        reverse=True,
    )
    total = Pruned()
    total.kept = [path.name for path in runs[:keep]]
    for run_dir in runs[keep:]:
        if (run_dir / "pruned.json").is_file():
            continue
        one = prune_run(run_dir, dry_run=dry_run)
        total.runs += one.runs
        total.files += one.files
        total.bytes += one.bytes
    return total
