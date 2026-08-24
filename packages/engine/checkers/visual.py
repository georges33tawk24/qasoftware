"""Visual regression against the previous run — SPEC §5's hardening.

A pure function over `visual.json`, which the run stage wrote by comparing this artifact
with the base run's. The comparison records what changed; this decides what is worth
saying about it.
"""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers.base import checker
from engine.checkers.support import page_finding
from engine.issues.models import Category, Finding, Severity
from engine.visual import SurfaceComparison, VisualFile

SSIM_MAJOR = 0.90
"""Below this the page looks different to a person. Chosen against the fixture pair: an
unchanged page scores 1.0, a page with one shifted section scores ~0.95, a page with a
section removed scores well under 0.9."""

SSIM_MINOR = 0.97
STRUCTURAL_MIN = 3
"""One element moving is layout. Three at once is a change somebody made."""

MAX_LISTED = 6


def _read(ctx: RunContext) -> VisualFile | None:
    if not ctx.paths.visual.is_file():
        return None
    return VisualFile.model_validate_json(ctx.paths.visual.read_bytes())


@checker
class VisualRegression:
    id = "visual.regression"
    category = Category.layout
    requires = frozenset({Capability.VISUAL})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        payload = _read(ctx)
        if payload is None:
            return
        for surface in payload.surfaces:
            if not surface.compared or surface.ssim >= SSIM_MINOR:
                continue
            severity = Severity.major if surface.ssim < SSIM_MAJOR else Severity.minor
            yield self._finding(ctx, surface, severity, payload.baseRunId)

    def _finding(
        self, ctx: RunContext, surface: SurfaceComparison, severity: Severity, base: str
    ) -> Finding:
        page = ctx.page(surface.pageId)
        moved = ", ".join(
            f"{change.kind} {change.selector or change.stableKey}"
            for change in surface.changes[:MAX_LISTED]
        )
        return page_finding(
            self,
            page,
            kind="page-looks-different",
            title="This page has changed since the last run",
            description=(
                f"Structural similarity is {surface.ssim:.2f} against the previous run at "
                f"{surface.viewport}."
                + (
                    f" {surface.added} added, {surface.removed} removed, "
                    f"{surface.moved} moved or resized: {moved}."
                    if surface.changes
                    else ""
                )
            ),
            expected="the page looks as it did on the last run",
            actual=f"SSIM {surface.ssim:.2f}",
            severity=severity,
            viewport=surface.viewport,
            groupAs=f"visual:{surface.viewport}",
            data={
                "ssim": surface.ssim,
                "added": surface.added,
                "removed": surface.removed,
                "moved": surface.moved,
                "baseRunId": base,
                "changes": [c.model_dump(mode="json") for c in surface.changes[:MAX_LISTED]],
            },
        )


@checker
class DisappearedContent:
    """Elements that were there last run and are not there now.

    Separate from the SSIM finding on purpose: a section quietly vanishing is the failure
    people care most about, and it can happen with barely any change to the picture — a
    removed block below the fold moves nothing above it.
    """

    id = "visual.disappeared"
    category = Category.content
    requires = frozenset({Capability.VISUAL})
    default_severity = Severity.major

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        payload = _read(ctx)
        if payload is None:
            return
        for surface in payload.surfaces:
            gone = [c for c in surface.changes if c.kind == "removed"]
            if len(gone) < STRUCTURAL_MIN:
                continue
            page = ctx.page(surface.pageId)
            listed = ", ".join(c.selector or c.stableKey for c in gone[:MAX_LISTED])
            yield page_finding(
                self,
                page,
                kind="content-disappeared",
                title=f"{len(gone)} element(s) are gone since the last run",
                description=(
                    f"These were on this page at {surface.viewport} on the previous run and "
                    f"are not on it now: {listed}."
                ),
                expected="the same elements as the last run, or a deliberate change",
                actual=f"{len(gone)} missing",
                viewport=surface.viewport,
                groupAs=f"disappeared:{surface.viewport}",
                data={"removed": [c.model_dump(mode="json") for c in gone[:MAX_LISTED]]},
            )
