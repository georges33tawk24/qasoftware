"""Sweep-to-verify confirm rates — SPEC §9.4.

An agent whose candidates are almost always rejected has a bad prompt and is burning
money. That is invisible unless someone counts, so this counts.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

POOR_CONFIRM_RATE = 0.20
"""SPEC §9.4. Below this, the prompt needs work rather than the budget needing raising."""

MIN_SAMPLE = 5
"""One rejected candidate is not a calibration signal."""


@dataclass
class AgentTally:
    swept: int = 0
    analysed: int = 0
    withdrawn: int = 0
    confirmed: int = 0
    downgraded: int = 0
    rejected: int = 0
    unparsed: int = 0

    @property
    def kept(self) -> int:
        return self.confirmed + self.downgraded

    @property
    def judged(self) -> int:
        return self.kept + self.rejected

    @property
    def confirmRate(self) -> float | None:
        return self.kept / self.judged if self.judged else None


@dataclass
class Calibration:
    project: str | None = None
    agents: dict[str, AgentTally] = field(default_factory=lambda: defaultdict(AgentTally))
    rejected: list[dict[str, Any]] = field(default_factory=list)
    """Dropped candidates, kept for calibration and never shown to the user (SPEC §9.4)."""

    def tally(self, agent: str) -> AgentTally:
        return self.agents[agent]

    def note_rejection(self, agent: str, candidate: dict[str, Any], reasoning: str) -> None:
        self.rejected.append({"agent": agent, "reasoning": reasoning, **candidate})

    def underperforming(self) -> list[str]:
        return sorted(
            agent
            for agent, tally in self.agents.items()
            if tally.judged >= MIN_SAMPLE and (tally.confirmRate or 0.0) < POOR_CONFIRM_RATE
        )

    def report(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "poorConfirmRate": POOR_CONFIRM_RATE,
            "underperforming": self.underperforming(),
            "agents": {
                agent: {
                    "swept": tally.swept,
                    "analysed": tally.analysed,
                    "withdrawn": tally.withdrawn,
                    "confirmed": tally.confirmed,
                    "downgraded": tally.downgraded,
                    "rejected": tally.rejected,
                    "unparsed": tally.unparsed,
                    "confirmRate": round(tally.confirmRate, 3)
                    if tally.confirmRate is not None
                    else None,
                }
                for agent, tally in sorted(self.agents.items())
            },
        }
