"""`bureau` — the engine's command line.

Phase 1 ships `capture` and `validate`. `check` and `report` arrive with their phases.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

from engine import branding
from engine.artifact import store
from engine.artifact.context import RunContext
from engine.artifact.models import VIEWPORT_PRESETS, PageRecord, RunConfig, Viewport
from engine.capture.auth import ANONYMOUS, Persona
from engine.capture.challenge import RunBlocked
from engine.capture.driver import DRIVERS
from engine.capture.run import capture
from engine.capture.secrets import resolve
from engine.checkers import runner
from engine.figma.client import FigmaClient, FigmaError
from engine.figma.ingest import ingest
from engine.matching import run as matching
from engine.report import build as build_report


def parse_viewport(spec: str) -> Viewport:
    """A preset name, or `name:WIDTHxHEIGHT[@SCALE]`."""
    if spec in VIEWPORT_PRESETS:
        return VIEWPORT_PRESETS[spec]
    try:
        name, _, size = spec.partition(":")
        dimensions, _, scale = size.partition("@")
        width, _, height = dimensions.partition("x")
        return Viewport(
            name=name,
            width=int(width),
            height=int(height),
            deviceScaleFactor=float(scale) if scale else 1.0,
        )
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{spec!r} is not a preset ({', '.join(VIEWPORT_PRESETS)}) or `name:1440x900@2`"
        ) from None


def load_personas(path: Path | None) -> list[Persona]:
    """Personas come from a project file. Credentials never do — the file holds only
    `env:` / `keychain:` references (CLAUDE.md)."""
    if path is None:
        return [ANONYMOUS]
    payload = json.loads(path.read_text())
    return [Persona.model_validate(entry) for entry in payload]


def _capture(args: argparse.Namespace) -> int:
    personas = load_personas(args.personas)
    config = RunConfig(
        driver=args.driver,
        viewports=args.viewport or list(RunConfig().viewports),
        personas=[p.name for p in personas],
        maxDepth=args.max_depth,
        maxPages=args.max_pages,
        include=args.include,
        exclude=args.exclude,
        ignoreQueryParams=args.ignore_param,
        sameOriginOnly=not args.any_origin,
        respectRobots=not args.ignore_robots,
        maskSelectors=args.mask,
        pageTimeoutMs=args.timeout,
        vitalsSamples=args.vitals_samples,
        platform=getattr(args, "platform", "web"),
        appPath=getattr(args, "app_path", None),
        appPackage=getattr(args, "app_package", None),
        appActivity=getattr(args, "app_activity", None),
        bundleId=getattr(args, "bundle_id", None),
        appiumUrl=getattr(args, "appium_url", "http://127.0.0.1:4723"),
    )

    def announce(page: PageRecord) -> None:
        mark = f"blocked: {page.crawlBlocked}" if page.crawlBlocked else f"{page.status}"
        print(f"  {page.path:<40} {mark}", file=sys.stderr)

    try:
        result = asyncio.run(
            capture(
                args.url,
                args.out,
                config=config,
                personas=personas,
                on_page=announce,
            )
        )
    except RunBlocked as exc:
        print(f"\nrun aborted: {exc}", file=sys.stderr)
        return 2

    print(f"\n{result.paths.root}")
    print(f"  pages    {len(result.manifest.pageIds)} ({result.blocked} blocked)")
    print(f"  viewports {', '.join(v.name for v in config.viewports)}")
    if result.problems:
        print("\nartifact problems:", file=sys.stderr)
        for problem in result.problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1
    return 0


def _figma(args: argparse.Namespace) -> int:
    paths = store.RunPaths(args.run_dir)
    ctx = RunContext.open(paths.root)
    confirmed = dict(pair.split("=", 1) for pair in args.frame)
    pins = dict(pair.split("=", 1) for pair in args.pin)

    client = None
    if args.file:
        token = resolve(args.token) if args.token else None
        if not token:
            print(
                "a Figma token is required to fetch a file: --token env:FIGMA_TOKEN",
                file=sys.stderr,
            )
            return 2
        client = FigmaClient(token, cache_dir=args.cache)

    try:
        result = ingest(
            paths,
            ctx,
            file_key=args.file,
            client=client,
            confirmed=confirmed,
            accept_suggested=args.accept_suggested,
        )
    except FigmaError as exc:
        print(f"figma: {exc}", file=sys.stderr)
        return 2

    print(f"{paths.figma}")
    print(f"  frames    {len(result.document.frames)} in the file")
    print(
        f"  tokens    {len(result.tokens.palette)} colours, "
        f"{len(result.tokens.typeScale)} type sizes, {len(result.tokens.spacing)} spacings"
    )
    if args.propose or not result.frameMap:
        print("  suggested mapping:")
        from engine.figma.frames import best_per_frame

        for proposal in best_per_frame(result.proposals).values():
            mark = "*" if proposal.suggested else " "
            print(
                f"   {mark} {proposal.frameName!r} -> {proposal.pagePath} "
                f"({proposal.score}) {proposal.reasons}"
            )
        if args.propose:
            return 0

    for note in result.notes:
        print(f"  note      {note}")
    if not result.frameMap:
        return 0

    matched = matching.run(paths, ctx, result.document, result.frameMap, pins=pins)
    for mapping in matched.mappings:
        state = "ok" if mapping.confident else "COULD NOT MATCH"
        print(
            f"  {mapping.frameName!r} -> {mapping.pageId} @ {mapping.viewport}: "
            f"{mapping.matched} matched, {mapping.unmatchedNodes} unmatched nodes "
            f"[{state}]"
        )
    return 0


def _reason(args: argparse.Namespace) -> int:
    from engine.agents import AgentConfig, pipeline
    from engine.agents.config import Tier
    from engine.agents.provider import ProviderError
    from engine.agents.providers import build as build_provider

    paths = store.RunPaths(args.run_dir)
    ctx = RunContext.open(paths.root)
    config = AgentConfig(concurrency=args.concurrency, agents=args.agent)
    config.ceilings.perRunUsd = args.budget
    for tier, key in (
        (Tier.cheap, args.cheap),
        (Tier.strong, args.strong),
        (Tier.verify, args.verifier),
    ):
        if key:
            config.tiers[tier] = key

    knowledge = args.knowledge.read_text().splitlines() if args.knowledge else []
    try:
        provider = build_provider(
            args.provider, **({"api_key": resolve(args.token)} if args.token else {})
        )
        result = pipeline.reason(ctx, provider, config, knowledge=[k for k in knowledge if k])
    except ProviderError as exc:
        print(f"agents: {exc}", file=sys.stderr)
        return 2

    pipeline.write(paths, result)
    if paths.issues.is_file():
        merged = pipeline.merge(ctx, runner.read(paths), result)
        store.write_bytes(paths.issues, merged.model_dump_json(indent=2).encode() + b"\n")

    print(f"{paths.agents}")
    print(f"  surfaces  {result.surfaces} swept by {len(result.calibration.agents)} agents")
    print(
        f"  findings  {len(result.findings)} confirmed, "
        f"{len(result.calibration.rejected)} rejected and dropped"
    )
    print(
        f"  cost      ${result.budget.spent:.3f} of ${config.ceilings.perRunUsd:.2f} "
        f"over {result.budget.calls} calls"
    )
    for agent, tally in sorted(result.calibration.agents.items()):
        if tally.judged:
            print(
                f"    {agent:<20} {tally.kept}/{tally.judged} confirmed "
                f"({(tally.confirmRate or 0):.0%})"
            )
    for agent in result.calibration.underperforming():
        print(
            f"  WARNING   {agent} confirms under 20% of what it flags; its prompt needs "
            "work and it is burning money",
            file=sys.stderr,
        )
    if result.stopped:
        print(f"\nSTOPPED EARLY: {result.stopped}", file=sys.stderr)
        return 3
    return 0


def _exercise(args: argparse.Namespace) -> int:
    import asyncio

    from engine.capture.exercise import exercise

    paths = store.RunPaths(args.run_dir)
    ctx = RunContext.open(paths.root)
    personas = load_personas(args.personas)
    if args.authorised_by:
        ctx.manifest.config.authorisedBy = args.authorised_by
    if args.authorised_host:
        ctx.manifest.config.authorisedHosts = args.authorised_host
    ctx.manifest.config.flows = not args.no_flows
    ctx.manifest.config.apiProbes = not args.no_api
    store.write_run_manifest(paths, ctx.manifest)

    result = asyncio.run(exercise(paths, ctx, personas=personas))

    print(f"{paths.flows}")
    for flow in result.flows:
        mark = flow.status.value
        detail = f" — {flow.failures[0].message}" if flow.failures else ""
        print(f"  {mark:<8} {flow.name}{detail[:70]}")
    print(f"\n  flows     {len(result.flows)} run, {result.failed} failed")
    print(
        f"  endpoints {len(result.api.endpoints)} found, "
        f"{sum(1 for p in result.api.probes if p.finding)} probe findings"
    )
    if result.api.authorisedBy:
        print(
            f"  authorised by {result.api.authorisedBy} for {', '.join(result.api.authorisedHosts)}"
        )
    for note in result.notes:
        print(f"  note      {note}")
    return 0


def _check(args: argparse.Namespace) -> int:
    ctx = RunContext.open(args.run_dir)
    result = runner.check(ctx)
    runner.write(store.RunPaths(args.run_dir), ctx, result)

    by_severity: dict[str, int] = {}
    for issue in result.issues:
        by_severity[issue.severity.value] = by_severity.get(issue.severity.value, 0) + 1

    print(f"{store.RunPaths(args.run_dir).issues}")
    print(f"  checkers  {len(result.ran)} ran, {len(result.skipped)} skipped")
    print(f"  findings  {result.findings} grouped into {len(result.issues)} issues")
    for severity in ("blocker", "critical", "major", "minor", "trivial"):
        if by_severity.get(severity):
            print(f"  {severity:<9} {by_severity[severity]}")
    if args.verbose:
        for issue in result.issues:
            paths = ", ".join(issue.pagePaths[:3])
            print(f"    [{issue.severity.value:<8}] {issue.checkerId:<28} {issue.title}")
            print(f"    {'':>12} {issue.instanceCount}× on {paths}")
    return 0


def _report(args: argparse.Namespace) -> int:
    result = build_report(args.run_dir)
    print(result.path)
    print(f"  issues    {result.issues}")
    print(f"  evidence  {result.media} annotated {'inline' if result.inlined else 'in media/'}")
    print(f"  size      {result.bytes / 1000:.0f}KB")
    return 0


def _validate(args: argparse.Namespace) -> int:
    problems = store.validate(args.run_dir)
    for problem in problems:
        print(problem, file=sys.stderr)
    if not problems:
        print(f"{args.run_dir}: valid")
    return 1 if problems else 0


SEVERITY_ORDER = ["blocker", "critical", "major", "minor", "trivial"]


def _export(args: argparse.Namespace) -> int:
    """Write a CSV or Markdown file from a checked run. The tracker adapters need a
    control plane for their remote keys; these two need nothing."""
    from engine.artifact.store import RunPaths
    from engine.exporters import csv_file, markdown
    from engine.exporters.base import Bundle, Target, get

    paths = RunPaths(args.run_dir)
    if not paths.issues.is_file():
        print(
            f"{args.run_dir} has not been checked yet; run `{branding.CLI_NAME} check` first",
            file=sys.stderr,
        )
        return 2
    issues_file = runner.read(paths)
    adapter = get(args.format)
    target = Target(kind=args.format, labels=args.label or [])
    rows = [
        adapter.map(Bundle(issue=issue, run_dir=args.run_dir, report_url=args.report_url), target)
        for issue in issues_file.issues
    ]
    text = (
        csv_file.render(rows)
        if args.format == "csv"
        else markdown.render(rows, title=args.title or "Issues")
    )
    if args.out:
        args.out.write_text(text, encoding="utf-8")
        print(f"{args.out}  {len(rows)} issue(s)")
    else:
        print(text, end="")
    return 0


def _ci(args: argparse.Namespace) -> int:
    """Gate a deploy — SPEC §15.

    Posts to a control plane, waits, and exits non-zero when the run introduced something
    at or above the threshold. Prints the counts either way, because a pipeline log that
    only says "failed" sends someone to the UI to find out why.
    """
    import json as _json
    import urllib.error
    import urllib.request

    payload = {
        "target": args.target,
        "name": args.name or "",
        "baseRunId": args.base,
        "authorisedBy": args.authorised_by,
        "wait": args.timeout,
    }
    request = urllib.request.Request(
        f"{args.api.rstrip('/')}/api/ci/runs",
        data=_json.dumps(payload).encode(),
        headers={"content-type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout + 30) as response:
            body = _json.loads(response.read() or b"{}")
    except urllib.error.URLError as exc:
        print(f"{args.api}: {exc}", file=sys.stderr)
        return 2

    new = dict(body.get("new") or {})
    regressed = dict(body.get("regressed") or {})
    print(f"run {body.get('runId')}  {body.get('state')}")
    for label, counts in (("new", new), ("regressed", regressed)):
        rendered = " ".join(f"{k}={v}" for k, v in sorted(counts.items())) or "none"
        print(f"  {label:9} {rendered}")
    if body.get("reportUrl"):
        print(f"  report    {args.api.rstrip('/')}{body['reportUrl']}")

    if body.get("state") not in ("complete", None):
        print(f"the run did not finish: {body.get('error') or body.get('state')}", file=sys.stderr)
        return 2

    limit = SEVERITY_ORDER.index(args.fail_on) if args.fail_on in SEVERITY_ORDER else None
    if limit is None:
        return 0
    breached = sum(
        count
        for counts in (new, regressed)
        for severity, count in counts.items()
        if severity in SEVERITY_ORDER and SEVERITY_ORDER.index(severity) <= limit
    )
    if breached:
        print(f"{breached} new or regressed issue(s) at {args.fail_on} or worse", file=sys.stderr)
        return 1
    return 0


def _volatile(args: argparse.Namespace) -> int:
    """Nominate the parts of a page that will not hold still — SPEC §5.

    Prints selectors, never applies them: a section that genuinely broke between two
    loads looks exactly like a carousel from here, and only a person can tell.
    """
    import asyncio

    from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
    from engine.capture.volatile import sample

    config = RunConfig(consentSelectors=args.consent or [])
    viewport = VIEWPORT_PRESETS.get(args.viewport)
    report = asyncio.run(sample(args.url, config=config, viewport=viewport, loads=args.loads))

    print(f"{report.url}  @{report.viewport}  {report.compared} elements seen in every load")
    if not report.candidates:
        print("  nothing moved between loads")
        return 0
    for candidate in report.candidates:
        print(f"  {candidate.kind:9} {candidate.selector:44} {candidate.detail}")
    print("\nadd to the project's maskSelectors to stop these being reported every run:")
    print(json.dumps(report.selectors(), indent=2))
    return 0


def _prune(args: argparse.Namespace) -> int:
    from engine.retention import prune

    result = prune(args.project_dir, keep=args.keep, dry_run=args.dry_run)
    verb = "would remove" if args.dry_run else "removed"
    print(f"{verb} {result.files} media file(s), {result.megabytes}MB, from {result.runs} run(s)")
    print(f"  kept whole: {', '.join(result.kept) or 'nothing'}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog=branding.CLI_NAME, description=branding.DESCRIPTION)
    sub = parser.add_subparsers(dest="command", required=True)

    cap = sub.add_parser("capture", help="crawl a target and write a run artifact")
    cap.add_argument("url")
    cap.add_argument("--out", type=Path, default=Path("runs"), help="run directory root")
    cap.add_argument(
        "--viewport", type=parse_viewport, action="append", help="repeatable; preset or name:WxH@S"
    )
    cap.add_argument("--persona", dest="personas", type=Path, help="personas JSON file")
    cap.add_argument("--driver", default="playwright", choices=sorted(DRIVERS))
    cap.add_argument("--max-depth", type=int, default=RunConfig().maxDepth)
    cap.add_argument("--max-pages", type=int, default=RunConfig().maxPages)
    cap.add_argument("--include", action="append", default=[], help="regex, repeatable")
    cap.add_argument("--exclude", action="append", default=[], help="regex, repeatable")
    cap.add_argument("--ignore-param", action="append", default=[], help="query param to ignore")
    cap.add_argument("--mask", action="append", default=[], help="volatile CSS selector")
    cap.add_argument("--any-origin", action="store_true", help="follow links off the seed origin")
    cap.add_argument("--ignore-robots", action="store_true", help="override robots.txt")
    cap.add_argument("--timeout", type=int, default=RunConfig().pageTimeoutMs)
    cap.add_argument(
        "--vitals-samples",
        type=int,
        default=RunConfig().vitalsSamples,
        help="page loads used to measure web vitals; 1 disables sampling",
    )
    cap.add_argument(
        "--platform",
        choices=["web", "android", "ios"],
        default="web",
        help="target platform (web, android, ios)",
    )
    cap.add_argument("--app", dest="app_path", help="path to .apk (Android) or .ipa/.app (iOS)")
    cap.add_argument("--package", dest="app_package", help="Android application package name")
    cap.add_argument("--activity", dest="app_activity", help="Android launch activity")
    cap.add_argument("--bundle-id", help="iOS application bundle identifier")
    cap.add_argument("--appium-url", default="http://127.0.0.1:4723", help="Appium server endpoint")
    cap.set_defaults(handler=_capture)

    fig = sub.add_parser("figma", help="ingest a Figma file and match it to the run")
    fig.add_argument("run_dir", type=Path)
    fig.add_argument("--file", help="Figma file key; omit to use figma/file.json")
    fig.add_argument("--token", help="secret reference, e.g. env:FIGMA_TOKEN")
    fig.add_argument("--cache", type=Path, default=Path(".bureau/figma-cache"))
    fig.add_argument(
        "--frame",
        action="append",
        default=[],
        metavar="NAME=/path",
        help="confirm a frame-to-route mapping; repeatable",
    )
    fig.add_argument(
        "--pin",
        action="append",
        default=[],
        metavar="LAYER=selector",
        help="pin a layer to a CSS selector; repeatable",
    )
    fig.add_argument(
        "--accept-suggested",
        action="store_true",
        help="use the automatic frame mapping without confirming it",
    )
    fig.add_argument("--propose", action="store_true", help="print suggestions and stop")
    fig.set_defaults(handler=_figma)

    exr = sub.add_parser("exercise", help="run functional flows and API probes")
    exr.add_argument("run_dir", type=Path)
    exr.add_argument("--persona", dest="personas", type=Path, help="personas JSON file")
    exr.add_argument("--authorised-by", help="who authorised testing this target")
    exr.add_argument(
        "--authorised-host",
        action="append",
        default=[],
        help="a host this run may send requests to; repeatable",
    )
    exr.add_argument("--no-flows", action="store_true")
    exr.add_argument("--no-api", action="store_true")
    exr.set_defaults(handler=_exercise)

    chk = sub.add_parser("check", help="run the deterministic sweep over a run artifact")
    chk.add_argument("run_dir", type=Path)
    chk.add_argument("-v", "--verbose", action="store_true", help="list every issue")
    chk.set_defaults(handler=_check)

    rsn = sub.add_parser("reason", help="run the agent layer over a checked run")
    rsn.add_argument("run_dir", type=Path)
    rsn.add_argument("--provider", default="anthropic", help="anthropic, google, openai")
    rsn.add_argument("--token", help="secret reference, e.g. env:ANTHROPIC_API_KEY")
    rsn.add_argument("--cheap", help="catalogue key for the sweep tier")
    rsn.add_argument("--strong", help="catalogue key for the analysis tier")
    rsn.add_argument("--verifier", help="catalogue key for the verify tier")
    rsn.add_argument("--agent", action="append", default=[], help="limit to one agent; repeatable")
    rsn.add_argument("--budget", type=float, default=3.0, help="per-run ceiling in USD")
    rsn.add_argument("--concurrency", type=int, default=8)
    rsn.add_argument(
        "--knowledge", type=Path, help="a text file of project knowledge, one per line"
    )
    rsn.set_defaults(handler=_reason)

    rep = sub.add_parser("report", help="render the self-contained HTML report")
    rep.add_argument("run_dir", type=Path)
    rep.set_defaults(handler=_report)

    exp = sub.add_parser("export", help="write a CSV or Markdown file from a checked run")
    exp.add_argument("run_dir", type=Path)
    exp.add_argument("--format", choices=["csv", "markdown"], default="markdown")
    exp.add_argument("--out", type=Path, help="write here instead of stdout")
    exp.add_argument("--title", default="", help="markdown heading")
    exp.add_argument("--report-url", default="", help="link back to the report")
    exp.add_argument("--label", action="append", help="a label added to every issue")
    exp.set_defaults(handler=_export)

    ci = sub.add_parser("ci", help="run against a target and fail the build on new issues")
    ci.add_argument("target", help="the URL to sweep")
    ci.add_argument("--api", default="http://127.0.0.1:8000", help="the control plane")
    ci.add_argument("--name", default="", help="project name, when creating one")
    ci.add_argument("--base", default=None, help="run id to diff against")
    ci.add_argument("--authorised-by", default=None, help="who signed off the probing")
    ci.add_argument("--timeout", type=float, default=900.0, help="seconds to wait")
    ci.add_argument(
        "--fail-on",
        default="major",
        choices=[*SEVERITY_ORDER, "never"],
        help="exit non-zero when something new lands at this severity or worse",
    )
    ci.set_defaults(handler=_ci)

    vol = sub.add_parser("volatile", help="find the parts of a page that change on every load")
    vol.add_argument("url")
    vol.add_argument("--viewport", default="desktop_1440")
    vol.add_argument("--loads", type=int, default=2, help="how many times to load it")
    vol.add_argument("--consent", action="append", help="a selector that dismisses an overlay")
    vol.set_defaults(handler=_volatile)

    prn = sub.add_parser("prune", help="strip screenshots from old runs, keeping the data")
    prn.add_argument("project_dir", type=Path, help="a directory of run artifacts")
    prn.add_argument("--keep", type=int, default=10, help="runs that keep their media")
    prn.add_argument("--dry-run", action="store_true")
    prn.set_defaults(handler=_prune)

    val = sub.add_parser("validate", help="check a run artifact against the schema")
    val.add_argument("run_dir", type=Path)
    val.set_defaults(handler=_validate)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    handler: object = args.handler
    assert callable(handler)
    return int(handler(args))


if __name__ == "__main__":
    raise SystemExit(main())
