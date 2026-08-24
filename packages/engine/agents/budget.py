"""Cost ceilings — SPEC §9, build prompt item 7.

Never silently overspend. On breach the run stops and reports partial results loudly,
because a report that quietly cost forty dollars is worse than one that stopped at three
and said so.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from engine.agents.config import Ceilings


class BudgetExceeded(RuntimeError):
    def __init__(self, spent: float, ceiling: float, scope: str) -> None:
        super().__init__(
            f"stopped after ${spent:.2f}: the {scope} ceiling is ${ceiling:.2f}. "
            "Results so far are reported; raise the ceiling or narrow the run."
        )
        self.spent = spent
        self.ceiling = ceiling
        self.scope = scope


@dataclass
class Budget:
    ceilings: Ceilings = field(default_factory=Ceilings)
    priorProjectSpend: float = 0.0
    spent: float = 0.0
    byAgent: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    byStage: dict[str, float] = field(default_factory=lambda: defaultdict(float))
    calls: int = 0
    breach: str | None = None

    @property
    def remaining(self) -> float:
        run_left = self.ceilings.perRunUsd - self.spent
        if self.ceilings.perProjectUsd is None:
            return run_left
        project_left = self.ceilings.perProjectUsd - self.priorProjectSpend - self.spent
        return min(run_left, project_left)

    def affords(self, estimate: float) -> bool:
        return estimate <= self.remaining

    def charge(self, agent: str, stage: str, cost: float) -> None:
        self.spent += cost
        self.byAgent[agent] += cost
        self.byStage[stage] += cost
        self.calls += 1
        self._check()

    def _check(self) -> None:
        if self.spent > self.ceilings.perRunUsd:
            self.breach = "run"
            if self.ceilings.stopOnBreach:
                raise BudgetExceeded(self.spent, self.ceilings.perRunUsd, "per-run")
        project = self.ceilings.perProjectUsd
        if project is not None and self.priorProjectSpend + self.spent > project:
            self.breach = "project"
            if self.ceilings.stopOnBreach:
                raise BudgetExceeded(self.priorProjectSpend + self.spent, project, "per-project")

    def report(self) -> dict[str, Any]:
        return {
            "spentUsd": round(self.spent, 4),
            "calls": self.calls,
            "ceilingUsd": self.ceilings.perRunUsd,
            "breach": self.breach,
            "byAgent": {k: round(v, 4) for k, v in sorted(self.byAgent.items())},
            "byStage": {k: round(v, 4) for k, v in sorted(self.byStage.items())},
        }
