"""The agent layer — SPEC §9.

Runs against the scripted provider: the things worth testing here are what the pipeline
does with an answer — the grounding contract, the verifier dropping candidates, the cost
ceiling, the calibration log — not which model gave it.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from engine.agents import mandates
from engine.agents.budget import Budget, BudgetExceeded
from engine.agents.calibration import Calibration
from engine.agents.config import CATALOGUE, AgentConfig, Ceilings, ModelSpec, Tier
from engine.agents.grounding import (
    CONTRACT,
    LOCALISE,
    NOTHING_MEASURABLE,
    Grounding,
    GroundingError,
    facts_for,
    relevant_knowledge,
)
from engine.agents.parsing import parse_list, parse_object
from engine.agents.pipeline import ReasonResult, reason
from engine.agents.provider import Image, Request, Usage, estimate_cost
from engine.agents.providers import build as build_provider
from engine.agents.providers.scripted import ScriptedProvider
from engine.checkers.support import surfaces
from engine.fixtures import load_fixture
from engine.issues.models import Category, Severity, Source

SWEEP = json.dumps(
    [
        {
            "box": {"x": 24, "y": 48, "w": 600, "h": 44},
            "kind": "competing-calls-to-action",
            "note": "two primary actions",
            "confidence": 0.6,
        }
    ]
)
ANALYSIS = json.dumps(
    {
        "title": "Two controls compete to be the next step",
        "description": "The hero offers two equally weighted actions.",
        "expected": "one obvious next step",
        "actual": "two",
        "severity": "major",
        "confidence": 0.7,
    }
)
CONFIRM = json.dumps(
    {"verdict": "confirm", "reasoning": "visible in the crop", "severity": "major"}
)
REJECT = json.dumps({"verdict": "reject", "reasoning": "nothing there", "severity": "minor"})


def scripted_config(**over: Any) -> AgentConfig:
    fields: dict[str, Any] = {
        "tiers": {Tier.cheap: "scripted", Tier.strong: "scripted", Tier.verify: "scripted"},
        "concurrency": 4,
        "agents": ["layout-critic"],
    }
    fields.update(over)
    return AgentConfig(**fields)


def run(provider: ScriptedProvider, **over: Any) -> ReasonResult:
    return reason(load_fixture("design"), provider, scripted_config(**over))


def full_pipeline(verdict: str = CONFIRM, **script: str) -> ScriptedProvider:
    responses = {
        "sweep:layout-critic": SWEEP,
        "analyse:layout-critic": ANALYSIS,
        "verify:layout-critic": verdict,
    }
    responses.update(script)
    return ScriptedProvider(responses, default="[]")


# ------------------------------------------------------------------- grounding


def test_a_call_without_a_screenshot_will_not_construct() -> None:
    """SPEC §9.3 is mandatory, so it is enforced in the type rather than the prompt."""
    with pytest.raises(GroundingError, match="screenshot"):
        Grounding(screenshot=b"", facts={"page": "/"})


def test_a_call_without_measured_facts_will_not_construct() -> None:
    """Never the screenshot alone (SPEC §1.3)."""
    with pytest.raises(GroundingError, match="measured facts"):
        Grounding(screenshot=b"png", facts={})


def test_the_contract_cannot_be_edited_out() -> None:
    with pytest.raises(GroundingError, match="contract"):
        Grounding(screenshot=b"png", facts={"page": "/"}, contract="be nice")


def test_every_call_carries_the_screenshot_the_facts_and_the_rules() -> None:
    provider = full_pipeline()
    run(provider)
    assert provider.calls
    for call in provider.calls:
        assert call.images, f"{call.label} reached a model with no screenshot"
        assert "## Measured facts for this page" in call.prompt
        assert LOCALISE in call.prompt
        assert NOTHING_MEASURABLE in call.prompt


def test_project_knowledge_reaches_the_model() -> None:
    provider = full_pipeline()
    reason(
        load_fixture("design"),
        provider,
        scripted_config(),
        knowledge=["The client asked for the CTA to be green, not blue."],
    )
    assert any("asked for the CTA to be green" in call.prompt for call in provider.calls)


def test_scoped_knowledge_only_reaches_the_page_it_names() -> None:
    ctx = load_fixture("design")
    surface = next(iter(surfaces(ctx)))
    entries = ["/checkout: totals are deliberately rounded", "the testimonials are deferred"]
    kept = relevant_knowledge(entries, surface)
    assert kept == ["the testimonials are deferred"]


# ------------------------------------------------------------------- mandates


def test_the_agents_are_actually_different() -> None:
    """SPEC §9.2: if two prompts could be swapped without anyone noticing, they are not
    differentiated enough — and two agents given the same facts find the same things."""
    assert mandates.distinct()
    assert len(mandates.MANDATES) >= 5


def test_each_agent_sees_a_different_slice_of_the_facts() -> None:
    provider = ScriptedProvider(default="[]")
    reason(load_fixture("design"), provider, scripted_config(agents=[]))
    prompts = {call.label: call.prompt for call in provider.calls if call.label.startswith("sweep")}
    assert "textInventory" in prompts["sweep:copy-critic"]
    assert "spacingHistogram" not in prompts["sweep:copy-critic"]
    assert "spacingHistogram" in prompts["sweep:layout-critic"]
    assert "textInventory" not in prompts["sweep:layout-critic"]
    assert "siteMap" in prompts["sweep:impatient-customer"]


def test_the_brand_critic_stays_out_of_it_without_a_design() -> None:
    provider = ScriptedProvider(default="[]")
    reason(load_fixture("broken"), provider, scripted_config(agents=[]))
    assert not any(call.label.endswith("brand-critic") for call in provider.calls)


def test_a_named_agent_that_does_not_exist_is_an_error() -> None:
    with pytest.raises(KeyError, match="unknown agent"):
        mandates.selected(["layout-critique"])


# -------------------------------------------------------------------- parsing


@pytest.mark.parametrize(
    "text",
    [
        '```json\n[{"kind": "a"}]\n```',
        'Here you go:\n[{"kind": "a"}]\nHope that helps.',
        '[{"kind": "a"},]',
        '{"candidates": [{"kind": "a"}]}',
    ],
)
def test_json_survives_the_usual_mangling(text: str) -> None:
    assert parse_list(text) == [{"kind": "a"}]


def test_unreadable_json_is_retried_exactly_once_then_dropped() -> None:
    provider = ScriptedProvider(
        {"sweep:layout-critic": "I could not do that, sorry."}, default="[]"
    )
    result = run(provider)
    assert provider.counts["sweep:layout-critic"] == 2, "one retry, not a loop"
    assert result.findings == []
    assert result.calibration.tally("layout-critic").unparsed == 1


def test_a_recovered_retry_is_used() -> None:
    calls: list[str] = []

    class Flaky(ScriptedProvider):
        def complete(self, request: Request, spec: ModelSpec) -> Any:
            calls.append(request.label)
            if request.label == "sweep:layout-critic" and calls.count(request.label) == 1:
                return super().complete(Request(**{**request.__dict__, "label": "broken"}), spec)
            return super().complete(request, spec)

    provider = Flaky(
        {
            "broken": "not json at all",
            "sweep:layout-critic": SWEEP,
            "analyse:layout-critic": ANALYSIS,
            "verify:layout-critic": CONFIRM,
        },
        default="[]",
    )
    result = reason(load_fixture("design"), provider, scripted_config())
    assert len(result.findings) == 1


# ------------------------------------------------------------------ the sweep


def test_candidates_that_cannot_be_localised_are_dropped() -> None:
    """SPEC §9.3: if you cannot point at it with a box, it does not exist."""
    provider = full_pipeline(
        **{
            "sweep:layout-critic": json.dumps(
                [
                    {"kind": "vague-unease", "confidence": 0.9},
                    {"box": {"x": 0, "y": 0, "w": 0, "h": 0}, "kind": "zero", "confidence": 0.9},
                ]
            )
        }
    )
    assert run(provider).findings == []
    assert provider.counts["analyse:layout-critic"] == 0


def test_the_confidence_floor_drops_idle_guesses() -> None:
    low = json.dumps(
        [{"box": {"x": 1, "y": 1, "w": 10, "h": 10}, "kind": "maybe", "confidence": 0.05}]
    )
    provider = full_pipeline(**{"sweep:layout-critic": low})
    assert run(provider).findings == []


# ---------------------------------------------------------------- the verifier


def test_a_confirmed_candidate_becomes_a_verified_finding() -> None:
    result = run(full_pipeline())
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.checkerId == "ai.layout-critic"
    assert finding.category is Category.ai
    assert finding.source is Source.verified
    assert finding.agent == "layout-critic"
    assert finding.box is not None
    assert finding.confidence == 0.7


def test_a_rejected_candidate_is_dropped_silently_and_logged() -> None:
    """SPEC §9.4: never shown to the user, always kept for calibration."""
    result = run(full_pipeline(REJECT))
    assert result.findings == []
    assert result.calibration.tally("layout-critic").rejected == 1
    assert result.calibration.rejected[0]["agent"] == "layout-critic"
    assert result.calibration.rejected[0]["reasoning"] == "nothing there"


def test_a_downgrade_weakens_the_severity() -> None:
    downgrade = json.dumps(
        {"verdict": "downgrade", "reasoning": "real but small", "severity": "major"}
    )
    result = run(full_pipeline(downgrade))
    assert result.findings[0].severity is Severity.minor
    assert result.calibration.tally("layout-critic").downgraded == 1


def test_the_ai_can_never_raise_severity_above_major() -> None:
    """SPEC §8.3, and the reason a human keeps the last word."""
    shouting = json.dumps(
        {"verdict": "confirm", "reasoning": "catastrophic", "severity": "blocker"}
    )
    assert run(full_pipeline(shouting)).findings[0].severity is Severity.major


def test_the_strong_model_may_withdraw_a_candidate() -> None:
    provider = full_pipeline(
        **{"analyse:layout-critic": json.dumps({"title": "", "confidence": 0})}
    )
    result = run(provider)
    assert result.findings == []
    assert result.calibration.tally("layout-critic").withdrawn == 1
    assert provider.counts["verify:layout-critic"] == 0, "a withdrawn candidate is not verified"


def test_a_clean_page_produces_nothing() -> None:
    """The half of the done-when that matters: quiet when there is nothing to say."""
    provider = ScriptedProvider(default="[]")
    result = reason(load_fixture("design"), provider, scripted_config(agents=[]))
    assert result.findings == []
    assert all(tally.swept == 0 for tally in result.calibration.agents.values())


# ---------------------------------------------------------------------- money


def test_the_two_tiers_are_used_where_the_spec_says() -> None:
    config = AgentConfig()
    assert config.for_tier(Tier.cheap).model != config.for_tier(Tier.strong).model
    assert config.for_tier(Tier.cheap).inputPerMTok < config.for_tier(Tier.strong).inputPerMTok


def test_an_agent_can_be_given_its_own_model_family() -> None:
    """SPEC §9.2: five runs of one model gives you one model's blind spots five times."""
    config = AgentConfig(mandateModels={"copy-critic": "anthropic:sonnet"})
    assert config.for_tier(Tier.strong, "copy-critic").model == "claude-sonnet-5"
    assert config.for_tier(Tier.strong, "layout-critic").model == "claude-opus-5"


def test_an_unpriced_model_is_refused() -> None:
    """A wrong price silently mis-reports every run's spend."""
    with pytest.raises(KeyError, match="add it to AgentConfig"):
        AgentConfig().spec("google:whatever")


def test_cost_is_estimated_before_the_call_is_made() -> None:
    spec = CATALOGUE["anthropic:haiku"]
    request = Request(
        system="s" * 400,
        prompt="p" * 4000,
        images=(Image(data=b"x", width=1000, height=1000),),
        max_tokens=1000,
    )
    assert estimate_cost(request, spec) > 0


def test_spend_is_logged_per_agent_and_per_stage() -> None:
    provider = ScriptedProvider(
        {
            "sweep:layout-critic": SWEEP,
            "analyse:layout-critic": ANALYSIS,
            "verify:layout-critic": CONFIRM,
        },
        default="[]",
        usage=Usage(inputTokens=100_000, outputTokens=1_000),
    )
    result = reason(
        load_fixture("design"),
        provider,
        scripted_config(
            tiers={
                Tier.cheap: "anthropic:haiku",
                Tier.strong: "anthropic:opus",
                Tier.verify: "anthropic:opus",
            }
        ),
    )
    report = result.budget.report()
    assert report["spentUsd"] > 0
    assert set(report["byStage"]) == {"sweep", "analyse", "verify"}
    assert report["byAgent"]["layout-critic"] > 0


def test_breaching_the_ceiling_stops_the_run_and_says_so() -> None:
    """Never silently overspend (build prompt item 7)."""
    provider = ScriptedProvider(
        {
            "sweep:layout-critic": SWEEP,
            "analyse:layout-critic": ANALYSIS,
            "verify:layout-critic": CONFIRM,
        },
        default="[]",
        usage=Usage(inputTokens=2_000_000, outputTokens=100_000),
    )
    result = reason(
        load_fixture("design"),
        provider,
        scripted_config(
            agents=[],
            tiers={
                Tier.cheap: "anthropic:opus",
                Tier.strong: "anthropic:opus",
                Tier.verify: "anthropic:opus",
            },
            ceilings=Ceilings(perRunUsd=0.05),
        ),
    )
    assert result.stopped is not None
    assert "ceiling" in result.stopped
    assert "$" in result.stopped


def test_the_project_ceiling_counts_earlier_runs() -> None:
    budget = Budget(ceilings=Ceilings(perRunUsd=100.0, perProjectUsd=10.0), priorProjectSpend=9.5)
    assert budget.remaining == pytest.approx(0.5)
    with pytest.raises(BudgetExceeded, match="per-project"):
        budget.charge("a", "sweep", 1.0)


# --------------------------------------------------------------- calibration


def test_the_confirm_rate_is_tracked_per_agent() -> None:
    result = run(full_pipeline())
    tally = result.calibration.tally("layout-critic")
    assert tally.swept == 1
    assert tally.confirmRate == 1.0


def test_an_agent_that_confirms_almost_nothing_is_flagged() -> None:
    """SPEC §9.4: below ~20%, the prompt needs work and the agent is burning money."""
    calibration = Calibration()
    poor = calibration.tally("copy-critic")
    poor.confirmed, poor.rejected = 1, 19
    fine = calibration.tally("layout-critic")
    fine.confirmed, fine.rejected = 8, 12
    assert calibration.underperforming() == ["copy-critic"]


def test_one_rejection_is_not_a_calibration_signal() -> None:
    calibration = Calibration()
    calibration.tally("copy-critic").rejected = 1
    assert calibration.underperforming() == []


def test_the_internal_record_is_written(tmp_path: Path) -> None:
    from engine.agents.pipeline import write
    from engine.artifact.store import RunPaths

    result = run(full_pipeline(REJECT))
    paths = RunPaths(tmp_path)
    write(paths, result)
    calibration = json.loads((paths.agents / "calibration.json").read_text())
    assert calibration["agents"]["layout-critic"]["rejected"] == 1
    assert json.loads((paths.agents / "cost.json").read_text())["calls"] > 0
    assert json.loads((paths.agents / "rejected.json").read_text())[0]["agent"] == "layout-critic"


# ---------------------------------------------------------------- integration


def test_ai_issues_merge_alongside_the_measured_ones(tmp_path: Path) -> None:
    from engine.agents.pipeline import merge
    from engine.artifact.store import RunPaths
    from engine.checkers import runner

    ctx = load_fixture("design")
    paths = RunPaths(tmp_path)
    measured = runner.write(paths, ctx, runner.check(ctx))
    before = len(measured.issues)

    merged = merge(ctx, measured, run(full_pipeline()))
    assert len(merged.issues) == before + 1
    assert any(issue.category is Category.ai for issue in merged.issues)
    ranks = [issue.severity.rank for issue in merged.issues]
    assert ranks == sorted(ranks), "the merged list is still worst-first"
    assert "ai.layout-critic" in merged.checkersRan


def test_unknown_providers_are_named_not_guessed() -> None:
    from engine.agents.provider import ProviderError

    with pytest.raises(ProviderError, match="unknown provider"):
        build_provider("mistral")


def test_the_verifier_sees_a_crop_and_not_the_whole_page() -> None:
    """SPEC §9.4: judging is easier than spotting, and a close crop is what makes it so."""
    provider = full_pipeline()
    run(provider)
    sweep = next(c for c in provider.calls if c.label == "sweep:layout-critic")
    verify = next(c for c in provider.calls if c.label == "verify:layout-critic")
    # Pixels, not bytes: pixels are what a vision model is billed for.
    assert verify.images[0].width * verify.images[0].height < (
        sweep.images[0].width * sweep.images[0].height
    )


def test_the_facts_block_is_built_from_the_artifact() -> None:
    ctx = load_fixture("design")
    surface = next(iter(surfaces(ctx)))
    facts = facts_for(ctx, surface, ("typeInventory", "measure"))
    assert facts["page"] == surface.page.path
    assert facts["viewport"]["name"] == surface.viewport.name
    assert facts["typeInventory"]
    assert "spacingHistogram" not in facts


def test_the_contract_is_one_string_used_everywhere() -> None:
    assert LOCALISE in CONTRACT and NOTHING_MEASURABLE in CONTRACT
    assert parse_object(json.dumps({"a": 1})) == {"a": 1}
