"""Stage 5 of SPEC §3 — EXERCISE.

Runs the flows and the API probes against a captured run and writes what happened into
the artifact. Produces data, never issues: groups H and I read this back as pure
functions, the same as every other checker group (CLAUDE.md).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.api.authorisation import NotAuthorised, authorise
from engine.api.endpoints import derive
from engine.api.probes import ProbeContext, run_probes
from engine.artifact.context import RunContext
from engine.artifact.models import ApiReport, FlowRecord, FlowStatus, RunConfig, Viewport
from engine.artifact.store import RunPaths, write_bytes
from engine.capture.auth import ANONYMOUS, Persona, context_options
from engine.capture.driver import BrowserDriver, ContextOptions, get_driver
from engine.capture.flows import journeys
from engine.capture.flows.runner import FlowRunner, FlowSpec, run_flows
from engine.capture.secrets import Redactor
from engine.capture.secrets import resolve as resolve_secret


@dataclass
class ExerciseResult:
    flows: list[FlowRecord] = field(default_factory=list)
    api: ApiReport = field(default_factory=ApiReport)
    notes: list[str] = field(default_factory=list)

    @property
    def failed(self) -> int:
        return sum(1 for flow in self.flows if flow.status is not FlowStatus.passed)


def widest(ctx: RunContext) -> Viewport:
    return max(ctx.viewports, key=lambda v: v.width)


async def exercise(
    paths: RunPaths,
    ctx: RunContext,
    *,
    personas: list[Persona] | None = None,
    driver: BrowserDriver | None = None,
) -> ExerciseResult:
    config = ctx.manifest.config
    personas = personas or [ANONYMOUS]
    result = ExerciseResult()
    redactor = Redactor()

    owned = driver is None
    driver = driver or get_driver(config.driver)
    if owned:
        await driver.launch()

    try:
        if config.flows:
            result.flows = await _run_flows(paths, ctx, driver, personas, redactor, config)
        else:
            result.notes.append("flows are switched off for this run")
        if config.apiProbes:
            await _run_api(paths, ctx, driver, personas, redactor, result)
        else:
            result.notes.append("API probes are switched off for this run")
    finally:
        if owned:
            await driver.close()

    _write(paths, result)
    return result


async def _run_flows(
    paths: RunPaths,
    ctx: RunContext,
    driver: BrowserDriver,
    personas: list[Persona],
    redactor: Redactor,
    config: object,
) -> list[FlowRecord]:
    records: list[FlowRecord] = []
    for index, persona in enumerate(personas):
        runner = FlowRunner(
            driver=driver,
            paths=paths,
            config=ctx.manifest.config,
            persona=persona,
            viewport=widest(ctx),
            redactor=redactor,
        )
        specs = journeys.build(ctx, persona, shared=index == 0)
        specs.extend(recorded(ctx.manifest.config, persona))
        records.extend(await run_flows(runner, specs))
    return records


def recorded(config: RunConfig, persona: Persona) -> list[FlowSpec]:
    """The saved journeys, replayed through the same step wrapper as everything else."""
    from engine.capture.flows.record import RecordedStep, to_spec

    secrets = _persona_secrets(persona)
    specs: list[FlowSpec] = []
    for entry in config.recordings or []:
        if not entry.get("enabled", True):
            continue
        wanted = str(entry.get("persona") or ANONYMOUS.name)
        if wanted != persona.name:
            continue
        steps = [RecordedStep(**step) for step in entry.get("steps") or []]
        if steps:
            specs.append(
                to_spec(str(entry.get("name") or "Recorded journey"), steps, secrets=secrets)
            )
    return specs


def _persona_secrets(persona: Persona) -> dict[str, str]:
    """A recorded password is a reference; this is where it becomes a value, in memory,
    for exactly as long as the flow takes."""
    if persona.login is None:
        return {}
    try:
        return {
            "user": resolve_secret(persona.login.usernameRef),
            "password": resolve_secret(persona.login.passwordRef),
        }
    except Exception:
        return {}


async def _run_api(
    paths: RunPaths,
    ctx: RunContext,
    driver: BrowserDriver,
    personas: list[Persona],
    redactor: Redactor,
    result: ExerciseResult,
) -> None:
    config = ctx.manifest.config
    endpoints = derive(ctx)
    result.api.endpoints = endpoints
    if not endpoints:
        result.notes.append("no API endpoints were seen during the crawl")
        return

    try:
        authorisation = authorise(config, ctx.manifest.target)
    except NotAuthorised as exc:
        # Not an error: a run nobody has taken responsibility for is crawled and checked,
        # and simply not probed.
        result.api.skipped["*"] = str(exc)
        result.notes.append(str(exc))
        return

    result.api.authorisedBy = authorisation.by
    result.api.authorisedHosts = sorted(authorisation.hosts)

    context = await driver.new_context(
        ContextOptions(viewport=widest(ctx), persona=personas[0]), redactor
    )
    try:
        headers = {}
        for persona in personas:
            identity = await _identify(driver, ctx, persona, redactor)
            if identity:
                headers[persona.name] = identity
        named = sorted(headers)
        probe_ctx = ProbeContext(
            request=context.request, authorisation=authorisation, personas=headers
        )
        probes, skipped = await run_probes(
            probe_ctx,
            endpoints,
            personas=(named[0], named[1]) if len(named) >= 2 else None,
        )
        result.api.probes = probes
        result.api.skipped.update(skipped)
    finally:
        await context.close()


async def _identify(
    driver: BrowserDriver, ctx: RunContext, persona: Persona, redactor: Redactor
) -> dict[str, str]:
    """The headers that make a request *be* this persona.

    Most sites authenticate with a cookie rather than a bearer token, so signing in with
    a browser and carrying the resulting cookie is the only way the cross-persona check
    reaches a real session (SPEC §8.4 I).
    """
    from engine.capture.auth import ensure_authenticated

    static = context_options(persona, redactor).get("extra_http_headers", {})
    if persona.login is None:
        return dict(static)

    context = await driver.new_context(
        ContextOptions(viewport=widest(ctx), persona=persona), redactor
    )
    page = await context.new_page()
    try:
        await ensure_authenticated(page, persona, redactor)
        cookies = await context.cookies()
    except Exception:
        return dict(static)
    finally:
        await page.close()
        await context.close()

    jar = "; ".join(f"{c['name']}={c['value']}" for c in cookies if c.get("name"))
    for cookie in cookies:
        redactor.add(str(cookie.get("value")))
    return {**static, **({"Cookie": jar} if jar else {})}


def _write(paths: RunPaths, result: ExerciseResult) -> None:
    for flow in result.flows:
        write_bytes(paths.flow_steps(flow.id), flow.model_dump_json(indent=2).encode() + b"\n")
    if result.api.endpoints:
        write_bytes(
            paths.api_endpoints,
            json.dumps(
                [e.model_dump() for e in result.api.endpoints], indent=2, default=str
            ).encode()
            + b"\n",
        )
    write_bytes(paths.api_probes, result.api.model_dump_json(indent=2).encode() + b"\n")


def read_flows(paths: RunPaths) -> list[FlowRecord]:
    return [
        FlowRecord.model_validate_json(paths.flow_steps(flow_id).read_bytes())
        for flow_id in paths.flow_ids()
    ]


def read_api(paths: RunPaths) -> ApiReport | None:
    path = paths.api_probes
    return ApiReport.model_validate_json(path.read_bytes()) if path.is_file() else None


def flow_media(paths: RunPaths, flow: FlowRecord, name: str) -> Path:
    return paths.flow_dir(flow.id) / name
