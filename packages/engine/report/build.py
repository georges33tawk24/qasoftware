"""`bureau report <run_dir>` — SPEC §12.1."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from engine.artifact.context import RunContext
from engine.artifact.store import RunPaths
from engine.checkers import runner
from engine.report import compose, html


@dataclass
class ReportResult:
    path: Path
    issues: int
    media: int
    inlined: bool
    bytes: int


def build(run_dir: Path | str) -> ReportResult:
    paths = RunPaths(run_dir)
    ctx = RunContext.open(paths.root)
    issues_file = runner.read(paths)

    with tempfile.TemporaryDirectory(prefix="bureau-report-") as work:
        composed = compose.compose(ctx, issues_file, Path(work))
        inline = composed.inline
        if not inline:
            compose.write_media(paths, composed)
        document = html.render(composed, inline=inline)

    paths.report.write_text(document, encoding="utf-8")
    return ReportResult(
        path=paths.report,
        # What the report actually shows, which is not what the artifact holds once
        # somebody has dismissed something (SPEC §11).
        issues=int(composed.payload["totals"]["issues"]),
        media=len(composed.media),
        inlined=inline,
        bytes=len(document.encode()),
    )
