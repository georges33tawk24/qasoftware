"""Model and cost configuration — SPEC §9, §17.

The only place in the engine that knows a model name. CLAUDE.md: never hardcode one at a
call site; a call site names a *tier* and this decides what that means.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class Tier(StrEnum):
    """SPEC §9.1's two-tier economics, plus the verifier's own tier so it can be moved
    without touching either of the others."""

    cheap = "cheap"
    strong = "strong"
    verify = "verify"


class ModelSpec(BaseModel):
    """A model and what it costs. Prices are US dollars per million tokens."""

    model_config = ConfigDict(extra="forbid")

    provider: str
    model: str
    inputPerMTok: float
    outputPerMTok: float
    maxTokens: int = 4096
    vision: bool = True

    def cost(self, input_tokens: int, output_tokens: int) -> float:
        return (input_tokens * self.inputPerMTok + output_tokens * self.outputPerMTok) / 1_000_000


# Anthropic prices verified 2026-06-24. Other providers are deliberately absent rather
# than guessed: a wrong price in here silently mis-reports every run's spend, and the
# cost ceiling is the only thing standing between a large site and a large bill.
CATALOGUE: dict[str, ModelSpec] = {
    "anthropic:opus": ModelSpec(
        provider="anthropic",
        model="claude-opus-5",
        inputPerMTok=5.00,
        outputPerMTok=25.00,
        maxTokens=8000,
    ),
    "anthropic:sonnet": ModelSpec(
        provider="anthropic",
        model="claude-sonnet-5",
        inputPerMTok=3.00,
        outputPerMTok=15.00,
        maxTokens=8000,
    ),
    "anthropic:haiku": ModelSpec(
        provider="anthropic",
        model="claude-haiku-4-5",
        inputPerMTok=1.00,
        outputPerMTok=5.00,
        maxTokens=2000,
    ),
    "scripted": ModelSpec(
        provider="scripted", model="scripted", inputPerMTok=0.0, outputPerMTok=0.0
    ),
}


class Ceilings(BaseModel):
    """SPEC §9.1 budgets around $1–2 for a twenty-page site with five agents."""

    model_config = ConfigDict(extra="forbid")

    perRunUsd: float = 3.00
    perProjectUsd: float | None = None
    stopOnBreach: bool = True
    """Never silently overspend: on breach the run stops and says so loudly."""


class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    tiers: dict[Tier, str] = Field(
        default_factory=lambda: {
            Tier.cheap: "anthropic:haiku",
            Tier.strong: "anthropic:opus",
            Tier.verify: "anthropic:opus",
        }
    )
    """Tier to catalogue key. SPEC §9.2 asks for different model *families* across the
    agents where possible; `mandateModels` overrides a single agent's strong tier."""

    mandateModels: dict[str, str] = Field(default_factory=dict)
    custom: dict[str, ModelSpec] = Field(default_factory=dict)
    """Models not in the catalogue — a Google or OpenAI model, say — with the prices the
    project has verified for itself."""

    agents: list[str] = Field(default_factory=list)
    """Empty means every mandate that applies to this run."""

    ceilings: Ceilings = Field(default_factory=Ceilings)
    concurrency: int = 8
    """SPEC §9: a twenty-page site should take minutes, not an hour."""

    sweepConfidenceFloor: float = 0.25
    """The sweep is told to over-flag, so this only drops the truly idle guesses."""

    def spec(self, key: str) -> ModelSpec:
        if key in self.custom:
            return self.custom[key]
        if key in CATALOGUE:
            return CATALOGUE[key]
        raise KeyError(f"unknown model {key!r}: add it to AgentConfig.custom with its own prices")

    def for_tier(self, tier: Tier, mandate: str | None = None) -> ModelSpec:
        if mandate and tier is Tier.strong and mandate in self.mandateModels:
            return self.spec(self.mandateModels[mandate])
        return self.spec(self.tiers[tier])
