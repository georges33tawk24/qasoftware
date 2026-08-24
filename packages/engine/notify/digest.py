"""What to say, and whether to say anything at all — SPEC §15.

The rule that matters: **a run that finds nothing new sends nothing**. People mute noisy
tools within a fortnight and then the tool is dead, so silence is the default and a
digest has to earn its way out of here.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from engine.issues.diff import Change, RunDiff
from engine.issues.models import Severity

SEVERITY_ORDER = ["blocker", "critical", "major", "minor", "trivial"]
MAX_LISTED = 8


@dataclass
class Digest:
    """The message, in the one shape every channel renders from."""

    project: str
    target: str
    run_id: str
    report_url: str = ""
    board_url: str = ""
    new: list[tuple[str, str]] = field(default_factory=list)
    """(severity, title), worst first."""

    regressed: list[tuple[str, str]] = field(default_factory=list)
    fixed: int = 0
    still_open: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    @property
    def worth_sending(self) -> bool:
        """SPEC §15: only when something new or regressed appears."""
        return bool(self.new or self.regressed)

    def headline(self) -> str:
        parts = []
        if self.regressed:
            parts.append(f"{len(self.regressed)} regressed")
        if self.new:
            parts.append(f"{len(self.new)} new")
        return f"{self.project}: {' and '.join(parts)}"

    def lines(self) -> list[str]:
        out: list[str] = []
        for label, items in (("Regressed", self.regressed), ("New", self.new)):
            if not items:
                continue
            out.append(f"{label} ({len(items)})")
            out += [f"  {severity} · {title}" for severity, title in items[:MAX_LISTED]]
            if len(items) > MAX_LISTED:
                out.append(f"  …and {len(items) - MAX_LISTED} more")
        if self.fixed:
            out.append(f"Fixed since the last run: {self.fixed}")
        return out

    def text(self) -> str:
        body = "\n".join(self.lines())
        tail = "\n".join(filter(None, [self.report_url, self.board_url]))
        return f"{self.headline()}\n{self.target}\n\n{body}\n\n{tail}".strip()


def build(
    diff: RunDiff,
    *,
    project: str,
    target: str,
    run_id: str,
    report_url: str = "",
    board_url: str = "",
) -> Digest:
    def rank(entry: tuple[str, str]) -> int:
        return SEVERITY_ORDER.index(entry[0]) if entry[0] in SEVERITY_ORDER else len(SEVERITY_ORDER)

    new = sorted(((e.severity, e.title) for e in diff.of(Change.new)), key=rank)
    regressed = sorted(((e.severity, e.title) for e in diff.of(Change.regressed)), key=rank)
    counts: dict[str, int] = {}
    for entry in diff.entries:
        if entry.change in (Change.new, Change.regressed):
            counts[entry.severity] = counts.get(entry.severity, 0) + 1
    return Digest(
        project=project,
        target=target,
        run_id=run_id,
        report_url=report_url,
        board_url=board_url,
        new=new,
        regressed=regressed,
        fixed=len(diff.of(Change.fixed)),
        still_open=len(diff.of(Change.still_open)),
        counts=counts,
    )


def above(digest: Digest, threshold: Severity | None) -> bool:
    """Some teams only want waking for the bad ones. `None` means anything new."""
    if threshold is None:
        return digest.worth_sending
    limit = SEVERITY_ORDER.index(threshold.value)
    return any(
        SEVERITY_ORDER.index(severity) <= limit
        for severity, _ in [*digest.new, *digest.regressed]
        if severity in SEVERITY_ORDER
    )
