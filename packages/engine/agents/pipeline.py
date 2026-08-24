"""The agent layer — SPEC §3 stages 6 and 7, SPEC §9.

Sweep cheap and suspicious, write up only what was flagged, then judge every write-up
against a close crop and the measurements. Recall at the front, precision at the back.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

from engine.agents import crops, grounding, mandates
from engine.agents.budget import Budget, BudgetExceeded
from engine.agents.calibration import Calibration
from engine.agents.config import AgentConfig, Tier
from engine.agents.mandates import Mandate
from engine.agents.parsing import parse, parse_list, parse_object
from engine.agents.provider import Image, LLMProvider, ProviderError, Request, estimate_cost
from engine.artifact.context import RunContext
from engine.artifact.models import Box
from engine.artifact.store import RunPaths, write_bytes
from engine.checkers.support import Surface, surfaces, synthetic_key
from engine.issues.group import group, sort
from engine.issues.models import (
    AI_SEVERITY_CEILING,
    Category,
    Finding,
    Issue,
    IssuesFile,
    Severity,
    Source,
)

RETRY_NUDGE = "Your last reply was not valid JSON. Reply with the JSON only — nothing else."
MIN_TITLE_LENGTH = 8


@dataclass
class ReasonResult:
    findings: list[Finding] = field(default_factory=list)
    calibration: Calibration = field(default_factory=Calibration)
    budget: Budget = field(default_factory=Budget)
    stopped: str | None = None
    """Set when the run ended early. Reported loudly rather than swallowed."""

    surfaces: int = 0


@dataclass
class _Candidate:
    mandate: Mandate
    surface: Surface
    box: Box
    kind: str
    note: str
    confidence: float
    title: str = ""
    description: str = ""
    expected: str | None = None
    actual: str | None = None
    severity: Severity = Severity.minor

    def as_log(self) -> dict[str, Any]:
        return {
            "page": self.surface.page.path,
            "viewport": self.surface.viewport.name,
            "kind": self.kind,
            "note": self.note,
            "title": self.title,
            "confidence": self.confidence,
        }


class Reasoner:
    def __init__(
        self,
        ctx: RunContext,
        provider: LLMProvider,
        config: AgentConfig,
        *,
        knowledge: list[str] | None = None,
        prior_spend: float = 0.0,
    ) -> None:
        self.ctx = ctx
        self.provider = provider
        self.config = config
        self.knowledge = knowledge or []
        self.result = ReasonResult(
            budget=Budget(ceilings=config.ceilings, priorProjectSpend=prior_spend)
        )
        self._gate = asyncio.Semaphore(max(1, config.concurrency))

    # ---------------------------------------------------------------- one call

    async def _ask(
        self,
        *,
        mandate: str,
        stage: str,
        system: str,
        prompt: str,
        images: list[tuple[bytes, int, int]],
        tier: Tier,
        max_tokens: int,
    ) -> str | None:
        spec = self.config.for_tier(tier, mandate)
        request = Request(
            system=system,
            prompt=prompt,
            images=tuple(Image(data=d, width=w, height=h) for d, w, h in images),
            max_tokens=max_tokens,
            label=f"{stage}:{mandate}",
        )
        if not self.result.budget.affords(estimate_cost(request, spec)):
            raise BudgetExceeded(
                self.result.budget.spent, self.config.ceilings.perRunUsd, "per-run"
            )
        try:
            response = await asyncio.to_thread(
                self.provider.complete_vision if images else self.provider.complete,
                request,
                spec,
            )
        except ProviderError:
            return None
        self.result.budget.charge(mandate, stage, response.usage.cost(spec))
        return response.text

    async def _ask_json(self, **kwargs: Any) -> str | None:
        """One retry on an unreadable reply, then the candidate is dropped (SPEC §9.3).

        An empty array counts as readable: the sweep is explicitly told that seeing
        nothing is a good answer, and retrying it would pay twice for silence.
        """
        first = await self._ask(**kwargs)
        if first is not None and parse(first) is not None:
            return first
        kwargs["prompt"] = kwargs["prompt"] + "\n\n" + RETRY_NUDGE
        second = await self._ask(**kwargs)
        return second if second is not None and parse(second) is not None else None

    # ------------------------------------------------------------------ stages

    async def sweep(self, mandate: Mandate, surface: Surface) -> list[_Candidate]:
        page = crops.whole_page(self.ctx.paths.full_png(surface.page.id, surface.viewport.name))
        if page is None:
            return []
        ground = grounding.Grounding(
            screenshot=page[0],
            facts=grounding.facts_for(self.ctx, surface, mandate.facts),
            knowledge=grounding.relevant_knowledge(self.knowledge, surface),
        )
        text = await self._ask_json(
            mandate=mandate.id,
            stage="sweep",
            system=mandate.system,
            prompt=ground.as_prompt() + "\n\n" + mandates.prompt("_sweep"),
            images=[page],
            tier=Tier.cheap,
            max_tokens=1200,
        )
        tally = self.result.calibration.tally(mandate.id)
        if text is None:
            tally.unparsed += 1
            return []

        found: list[_Candidate] = []
        for raw in parse_list(text):
            box = _box(raw.get("box"))
            if box is None:
                continue  # cannot be localised, so it does not exist (SPEC §9.3)
            confidence = _float(raw.get("confidence"), 0.5)
            if confidence < self.config.sweepConfidenceFloor:
                continue
            found.append(
                _Candidate(
                    mandate=mandate,
                    surface=surface,
                    box=box,
                    kind=str(raw.get("kind") or "unspecified")[:60],
                    note=str(raw.get("note") or "")[:120],
                    confidence=confidence,
                )
            )
        tally.swept += len(found)
        return found

    async def analyse(self, candidate: _Candidate) -> bool:
        surface = candidate.surface
        page = crops.whole_page(self.ctx.paths.full_png(surface.page.id, surface.viewport.name))
        if page is None:
            return False
        ground = grounding.Grounding(
            screenshot=page[0],
            facts=grounding.facts_for(self.ctx, surface, candidate.mandate.facts),
            knowledge=grounding.relevant_knowledge(self.knowledge, surface),
        )
        text = await self._ask_json(
            mandate=candidate.mandate.id,
            stage="analyse",
            system=candidate.mandate.system,
            prompt="\n\n".join(
                [
                    ground.as_prompt(),
                    mandates.prompt("_analyse"),
                    "## The flagged region\n"
                    + json.dumps(
                        {
                            "box": candidate.box.model_dump(),
                            "kind": candidate.kind,
                            "note": candidate.note,
                        }
                    ),
                ]
            ),
            images=[page],
            tier=Tier.strong,
            max_tokens=1200,
        )
        tally = self.result.calibration.tally(candidate.mandate.id)
        payload = parse_object(text or "")
        if payload is None:
            tally.unparsed += 1
            return False

        title = str(payload.get("title") or "").strip()
        if len(title) < MIN_TITLE_LENGTH:
            tally.withdrawn += 1  # the strong model looked properly and withdrew it
            return False

        candidate.title = title[:140]
        candidate.description = str(payload.get("description") or "").strip()[:800]
        candidate.expected = _text(payload.get("expected"))
        candidate.actual = _text(payload.get("actual"))
        candidate.severity = _severity(payload.get("severity"), Severity.minor)
        candidate.confidence = _float(payload.get("confidence"), candidate.confidence)
        tally.analysed += 1
        return True

    async def verify(self, candidate: _Candidate) -> Finding | None:
        surface = candidate.surface
        viewport = next((v for v in self.ctx.viewports if v.name == surface.viewport.name), None)
        crop = crops.region(
            self.ctx.paths.full_png(surface.page.id, surface.viewport.name),
            candidate.box,
            scale=viewport.deviceScaleFactor if viewport else 1.0,
        )
        if crop is None:
            return None
        ground = grounding.Grounding(
            screenshot=crop[0],
            facts=grounding.facts_for(self.ctx, surface, candidate.mandate.facts),
            knowledge=grounding.relevant_knowledge(self.knowledge, surface),
        )
        text = await self._ask_json(
            mandate=candidate.mandate.id,
            stage="verify",
            system=mandates.prompt("_verify"),
            prompt="\n\n".join(
                [
                    "## The candidate finding\n"
                    + json.dumps(
                        {
                            "agent": candidate.mandate.id,
                            "title": candidate.title,
                            "description": candidate.description,
                            "expected": candidate.expected,
                            "actual": candidate.actual,
                            "severity": candidate.severity.value,
                        },
                        indent=1,
                    ),
                    ground.as_prompt(),
                ]
            ),
            images=[crop],
            tier=Tier.verify,
            max_tokens=600,
        )
        tally = self.result.calibration.tally(candidate.mandate.id)
        payload = parse_object(text or "")
        if payload is None:
            tally.unparsed += 1
            return None

        verdict = str(payload.get("verdict") or "reject").strip().lower()
        reasoning = str(payload.get("reasoning") or "").strip()[:400]
        if verdict not in ("confirm", "downgrade"):
            tally.rejected += 1
            # Dropped silently and logged for calibration, never shown (SPEC §9.4).
            self.result.calibration.note_rejection(
                candidate.mandate.id, candidate.as_log(), reasoning
            )
            return None

        severity = _severity(payload.get("severity"), candidate.severity)
        if verdict == "downgrade":
            tally.downgraded += 1
            severity = _weaken(severity)
        else:
            tally.confirmed += 1
        return _finding(candidate, severity, reasoning)

    # --------------------------------------------------------------- the whole

    async def run(self) -> ReasonResult:
        work = [
            (mandate, surface)
            for surface in surfaces(self.ctx)
            for mandate in mandates.selected(self.config.agents)
            if mandate.applies(self.ctx, surface)
        ]
        self.result.surfaces = len({(m, s.page.id, s.viewport.name) for m, s in work})

        try:
            await asyncio.gather(*(self._one(m, s) for m, s in work))
        except BudgetExceeded as exc:
            self.result.stopped = str(exc)
        return self.result

    async def _one(self, mandate: Mandate, surface: Surface) -> None:
        """Bounded so a twenty-page site takes minutes, not an hour (SPEC §9)."""
        async with self._gate:
            for candidate in await self.sweep(mandate, surface):
                if not await self.analyse(candidate):
                    continue
                finding = await self.verify(candidate)
                if finding is not None:
                    self.result.findings.append(finding)


# ------------------------------------------------------------------- helpers


def _finding(candidate: _Candidate, severity: Severity, reasoning: str) -> Finding:
    surface = candidate.surface
    return Finding(
        checkerId=f"ai.{candidate.mandate.id}",
        issueKind=candidate.kind,
        category=Category.ai,
        # SPEC §8.3: the AI layer never raises severity above major on its own.
        severity=severity if severity.rank >= AI_SEVERITY_CEILING.rank else AI_SEVERITY_CEILING,
        title=candidate.title,
        description=candidate.description + (f"\n\nVerifier: {reasoning}" if reasoning else ""),
        expected=candidate.expected,
        actual=candidate.actual,
        pageId=surface.page.id,
        pagePath=surface.page.path,
        viewport=surface.viewport.name,
        stableKey=synthetic_key(candidate.mandate.id, candidate.kind, candidate.title.lower()),
        box=candidate.box,
        source=Source.verified,
        confidence=round(candidate.confidence, 2),
        agent=candidate.mandate.id,
        groupAs=candidate.kind,
        data={"agent": candidate.mandate.id, "note": candidate.note},
    )


def _weaken(severity: Severity) -> Severity:
    order = [Severity.blocker, Severity.critical, Severity.major, Severity.minor, Severity.trivial]
    return order[min(len(order) - 1, order.index(severity) + 1)]


def _severity(value: Any, fallback: Severity) -> Severity:
    try:
        return Severity(str(value).strip().lower())
    except ValueError:
        return fallback


def _float(value: Any, fallback: float) -> float:
    try:
        return max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        return fallback


def _text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text[:300] or None


def _box(raw: Any) -> Box | None:
    if not isinstance(raw, dict):
        return None
    try:
        box = Box(x=float(raw["x"]), y=float(raw["y"]), w=float(raw["w"]), h=float(raw["h"]))
    except (KeyError, TypeError, ValueError):
        return None
    return box if box.w > 0 and box.h > 0 else None


def to_issues(ctx: RunContext, result: ReasonResult) -> list[Issue]:
    """Group the confirmed findings the same way the deterministic ones are grouped."""
    depths = {page.id: page.depth for page in ctx.pages()}
    return group(result.findings, run_id=ctx.run_id, depths=depths)


def merge(ctx: RunContext, existing: IssuesFile, result: ReasonResult) -> IssuesFile:
    """AI issues alongside the measured ones, in one sorted list.

    They never merge *into* a measured issue — a different checkerId is a different
    issue by construction (SPEC §8.2) — so this is a concatenation and a re-sort.
    """
    depths = {page.id: page.depth for page in ctx.pages()}
    issues = [i for i in existing.issues if i.category is not Category.ai]
    issues.extend(to_issues(ctx, result))
    existing.issues = sort(issues, depths)
    # Every agent that ran belongs in the appendix, including the quiet ones: SPEC §12.1
    # exists so a reader can see what was checked, not only what was found.
    ran = {
        f"ai.{mandate.id}"
        for mandate in mandates.selected([])
        if mandate.id in result.calibration.agents
    }
    existing.checkersRan = sorted(set(existing.checkersRan) | ran)
    return existing


def write(paths: RunPaths, result: ReasonResult) -> None:
    """`agents/` — the internal record SPEC §9.4 asks to be surfaced somewhere."""
    write_bytes(
        paths.agents / "calibration.json",
        json.dumps(result.calibration.report(), indent=2).encode() + b"\n",
    )
    write_bytes(
        paths.agents / "cost.json",
        json.dumps(result.budget.report(), indent=2).encode() + b"\n",
    )
    write_bytes(
        paths.agents / "rejected.json",
        json.dumps(result.calibration.rejected, indent=2).encode() + b"\n",
    )


async def reason_async(
    ctx: RunContext,
    provider: LLMProvider,
    config: AgentConfig,
    *,
    knowledge: list[str] | None = None,
    prior_spend: float = 0.0,
) -> ReasonResult:
    """The entry point for a caller that already has an event loop — which `run.execute`
    does, because the capture that produced this artifact was async."""
    return await Reasoner(ctx, provider, config, knowledge=knowledge, prior_spend=prior_spend).run()


def reason(
    ctx: RunContext,
    provider: LLMProvider,
    config: AgentConfig,
    *,
    knowledge: list[str] | None = None,
    prior_spend: float = 0.0,
) -> ReasonResult:
    """The synchronous entry point, for the CLI.

    `asyncio.run` refuses to nest, so calling this from inside a running loop raises —
    which is why `run.execute` awaits `reason_async` instead. Keeping both is one line
    and stops the next caller from having to know.
    """
    return asyncio.run(
        reason_async(ctx, provider, config, knowledge=knowledge, prior_spend=prior_spend)
    )
