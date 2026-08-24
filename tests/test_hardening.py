"""Phase 10 — the trust phase. Drivers, flake control, masking, visual regression,
retention, and the line that matters: two runs on an unchanged site agree.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest
from PIL import Image, ImageDraw

from engine.artifact.context import Capability, RunContext
from engine.artifact.models import Box, ElementRecord, MaskRegion
from engine.artifact.selectors import matches
from engine.artifact.store import RunPaths
from engine.capture.driver import DRIVERS, DriverUnavailable, get_driver
from engine.checkers import runner
from engine.fixtures import fixture_path
from engine.issues.diff import Change, diff
from engine.issues.models import IssuesFile
from engine.retention import prune, prune_run
from engine.visual import _prepare as prepare
from engine.visual import compare, structural
from engine.visual import ssim as ssim_of
from tests.conftest import styles

# ---------------------------------------------------------------------- drivers


def test_every_driver_in_the_spec_is_registered() -> None:
    assert set(DRIVERS) == {
        "playwright",
        "playwright_headed",
        "patchright",
        "camoufox",
        "remote",
    }


@pytest.mark.parametrize("name", ["patchright", "camoufox"])
def test_a_stealth_driver_says_what_to_install(name: str) -> None:
    """Not a stub: the driver is implemented and the browser is an optional extra. The
    message has to name the fix, because a missing-module traceback does not."""

    async def launch() -> None:
        await get_driver(name).launch()

    import asyncio

    with pytest.raises(DriverUnavailable) as caught:
        asyncio.run(launch())
    assert "pip install" in str(caught.value)


def test_the_remote_driver_wants_its_endpoint_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The URL usually carries the API key, so it never comes from project config."""
    import asyncio

    monkeypatch.delenv("BUREAU_REMOTE_WS", raising=False)
    with pytest.raises(DriverUnavailable) as caught:
        asyncio.run(get_driver("remote").launch())
    assert "BUREAU_REMOTE_WS" in str(caught.value)


def test_the_docs_lead_with_the_thing_that_keeps_working() -> None:
    """SPEC §5 is explicit about the order, and the order is the whole point."""
    text = (Path(__file__).parents[1] / "docs" / "bot-protection.md").read_text()
    allowlist = text.index("Ask for a bypass")
    stealth = text.index("Only then, a stealth driver")
    assert allowlist < stealth
    assert "No CAPTCHA solving" in text


# ------------------------------------------------------------------- selectors


@pytest.mark.parametrize(
    ("selector", "expected"),
    [
        (".card", True),
        ("div.card", True),
        ("span.card", False),
        ("#hero", True),
        (".card.wide", False),
        ("main > div.card:nth-of-type(2)", True),
        (".nothing", False),
    ],
)
def test_the_selector_subset(selector: str, expected: bool) -> None:
    element = ElementRecord(
        id="el_1",
        stableKey="k",
        selector="main > div.card:nth-of-type(2)",
        tag="div",
        classes=["card"],
        htmlId="hero",
        text="",
        textFull="",
        box=Box(x=0, y=0, w=10, h=10),
        boxViewport=Box(x=0, y=0, w=10, h=10),
        styles=styles(),
        resolvedBackground="rgb(255,255,255)",
    )
    assert matches(selector, element) is expected


# ---------------------------------------------------------------------- SSIM


def picture(tmp_path: Path, name: str, *, shift: int = 0, blocks: int = 4) -> Path:
    path = tmp_path / name
    image = Image.new("RGB", (800, 600), "white")
    draw = ImageDraw.Draw(image)
    for index in range(blocks):
        top = 40 + index * 120 + shift
        draw.rectangle((60, top, 740, top + 90), fill=(40, 60 + index * 30, 200))
        draw.text((80, top + 20), f"card {index}", fill="white")
    image.save(path)
    return path


def score(first: Path, second: Path, boxes: list[Box] | None = None) -> float:
    a = prepare(first, boxes or [], 1.0)
    b = prepare(second, boxes or [], 1.0)
    assert a is not None and b is not None
    return ssim_of(a, b)


def test_an_unchanged_page_scores_one(tmp_path: Path) -> None:
    """The whole point. Anything less and every run reports every page."""
    assert score(picture(tmp_path, "a.png"), picture(tmp_path, "b.png")) == 1.0


def test_a_moved_section_scores_lower(tmp_path: Path) -> None:
    moved = score(picture(tmp_path, "a.png"), picture(tmp_path, "c.png", shift=30))
    assert 0.5 < moved < 0.99


def test_a_removed_section_is_caught(tmp_path: Path) -> None:
    """Half the page gone still leaves the other half identical, so this is the case a
    threshold has to be low enough to catch and a mean-of-windows score has to weight."""
    from engine.checkers.visual import SSIM_MAJOR

    gone = score(picture(tmp_path, "a.png"), picture(tmp_path, "d.png", blocks=2))
    assert gone < SSIM_MAJOR


def test_a_masked_region_is_ignored_on_both_sides(tmp_path: Path) -> None:
    """A timestamp that changes every minute must not be a finding every run."""
    clean = picture(tmp_path, "a.png")
    noisy = picture(tmp_path, "e.png")
    with Image.open(noisy) as image:
        draw = ImageDraw.Draw(image)
        draw.rectangle((60, 500, 400, 560), fill=(200, 30, 30))
        image.save(noisy)

    assert score(clean, noisy) < 0.99
    masked = score(clean, noisy, [Box(x=50, y=490, w=360, h=80)])
    assert masked == 1.0


# ------------------------------------------------------ structural element diff


def element(
    key: str, x: float = 0, y: float = 0, classes: list[str] | None = None
) -> ElementRecord:
    return ElementRecord(
        id=f"el_{key}",
        stableKey=key,
        selector=f".{(classes or ['x'])[0]}",
        tag="div",
        classes=classes or ["x"],
        text="",
        textFull="",
        box=Box(x=x, y=y, w=100, h=40),
        boxViewport=Box(x=x, y=y, w=100, h=40),
        styles=styles(),
        resolvedBackground="rgb(255,255,255)",
    )


def test_the_structural_diff_names_what_changed() -> None:
    base = {"a": element("a"), "b": element("b", y=100), "c": element("c", y=200)}
    current = {"a": element("a"), "b": element("b", y=180), "d": element("d", y=300)}
    changes = {c.stableKey: c for c in structural(current, base, [])}
    assert changes["d"].kind == "added"
    assert changes["c"].kind == "removed"
    assert changes["b"].kind == "moved" and changes["b"].delta == 80.0
    assert "a" not in changes, "an element that did not move is not a change"


def test_a_masked_element_is_not_a_structural_change() -> None:
    base = {"t": element("t", classes=["timestamp"])}
    current = {"t": element("t", y=400, classes=["timestamp"])}
    assert structural(current, base, [".timestamp"]) == []
    assert structural(current, base, []) != []


def test_a_two_pixel_shift_is_not_a_change() -> None:
    """A font loading a moment later moves things a little; that is not a regression."""
    base = {"a": element("a", y=100)}
    current = {"a": element("a", y=102)}
    assert structural(current, base, []) == []


# -------------------------------------------------------- the visual comparison


def artifact_pair(tmp_path: Path) -> tuple[RunContext, RunContext]:
    first = tmp_path / "first"
    second = tmp_path / "second"
    shutil.copytree(fixture_path("broken"), first)
    shutil.copytree(fixture_path("broken"), second)
    return RunContext.open(first), RunContext.open(second)


def test_an_unchanged_run_compares_clean(tmp_path: Path) -> None:
    current, base = artifact_pair(tmp_path)
    result = compare(current, base)
    assert result.surfaces
    assert all(not surface.changes for surface in result.surfaces)
    assert all(surface.ssim == 1.0 for surface in result.surfaces)


def test_the_comparison_survives_a_pruned_base(tmp_path: Path) -> None:
    """An old run keeps its measurements after pruning, so the element diff still works."""
    current, base = artifact_pair(tmp_path)
    prune_run(Path(base.paths.root))
    result = compare(current, base)
    assert result.surfaces
    assert all(not surface.compared for surface in result.surfaces)
    assert any("screenshot" in surface.note for surface in result.surfaces)


def test_mask_regions_reach_what_a_selector_cannot() -> None:
    region = MaskRegion(x=10, y=20, w=100, h=50, viewport="desktop_1440")
    assert region.box() == Box(x=10, y=20, w=100, h=50)


# ------------------------------------------------------------------ flake control


def issues_from(name: str) -> IssuesFile:
    ctx = RunContext.open(fixture_path(name))
    from datetime import UTC, datetime

    return IssuesFile(
        runId=ctx.run_id, generatedAt=datetime.now(UTC), issues=runner.check(ctx).issues
    )


def test_a_flaky_finding_is_neither_new_nor_regressed() -> None:
    """It came back because it comes and goes, not because someone broke something."""
    payload = issues_from("broken")
    base = payload.model_copy(update={"issues": payload.issues[:5]})
    returning = payload.issues[7]
    current = base.model_copy(update={"issues": [*base.issues, returning]})

    without = diff(current, base, previously_fixed={returning.fingerprint})
    assert without.entries[0].change is Change.regressed

    with_flag = diff(
        current, base, previously_fixed={returning.fingerprint}, flaky={returning.fingerprint}
    )
    entry = next(e for e in with_flag.entries if e.fingerprint == returning.fingerprint)
    assert entry.change is Change.flaky
    assert with_flag.counts()["regressed"] == 0


def test_a_flaky_finding_that_is_absent_is_not_called_fixed() -> None:
    payload = issues_from("broken")
    base = payload.model_copy(update={"issues": payload.issues[:5]})
    current = payload.model_copy(update={"issues": payload.issues[:4]})
    gone = base.issues[4]

    assert diff(current, base).counts()["fixed"] == 1
    assert diff(current, base, flaky={gone.fingerprint}).counts()["fixed"] == 0


# --------------------------------------------------------------------- retention


def test_pruning_keeps_the_data_and_drops_the_pictures(tmp_path: Path) -> None:
    project = tmp_path / "project"
    for index in range(3):
        run = project / f"run_2026030{index}T000000"
        shutil.copytree(fixture_path("exercised"), run)

    before = sum(1 for p in project.rglob("*.png"))
    assert before > 0, "the fixture carries flow screenshots"

    result = prune(project, keep=1)
    assert result.runs == 2
    assert result.files > 0
    assert [p.name for p in sorted(project.iterdir())][-1] in result.kept

    newest = project / "run_20260302T000000"
    assert list(newest.rglob("*.png")), "the newest run keeps everything"

    oldest = project / "run_20260300T000000"
    assert not list(oldest.rglob("*.png")), "the oldest keeps none of its pictures"
    assert (oldest / "run.json").is_file(), "the measurements stay"
    assert (oldest / "pruned.json").is_file(), "and it says so"

    ctx = RunContext.open(oldest)
    assert ctx.pages(), "a pruned run is still readable"
    assert Capability.SCREENSHOT not in ctx.capabilities()


def test_pruning_twice_does_nothing_the_second_time(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(fixture_path("exercised"), project / "run_20260301T000000")
    first = prune(project, keep=0)
    second = prune(project, keep=0)
    assert first.files > 0
    assert second.files == 0


def test_a_dry_run_removes_nothing(tmp_path: Path) -> None:
    project = tmp_path / "project"
    shutil.copytree(fixture_path("exercised"), project / "run_20260301T000000")
    result = prune(project, keep=0, dry_run=True)
    assert result.files > 0
    assert list(project.rglob("*.png"))


# --------------------------------------------------- the line that actually matters


def test_only_web_vitals_are_outside_the_determinism_set() -> None:
    """SPEC §20 names exactly one exclusion, so exactly one is what this asserts.

    A new checker that measures the world instead of the artifact has to come here and
    change this line, which is the point: the alternative is someone quietly adding a
    second wobbling checker and the byte-identical promise becoming folklore.
    """
    from engine.checkers.base import discover, non_deterministic

    discover()
    assert non_deterministic() == {"performance.vitals"}


def test_two_runs_on_an_unchanged_site_agree(tmp_path: Path, browser_ready: None) -> None:
    """SPEC §20's definition of done, and the one everything else rests on.

    Two full runs against a site nobody touched. The issue *set* must be identical —
    same fingerprints, same expected and actual values, same instance count — because a
    tool whose findings wobble on their own teaches people to ignore all of them.
    """
    import asyncio

    from engine.artifact.models import RunConfig, Viewport
    from engine.run import RunRequest, execute
    from tests.fixtures.app import serve

    server, base, _ = serve()
    try:
        # Flows and probes included on purpose: they are the least deterministic part of
        # the system — retries, generated input, timing — and excluding them would make
        # this claim smaller than the one SPEC §20 makes.
        config = RunConfig(
            viewports=[Viewport(name="desktop_1440", width=1440, height=900)],
            maxPages=4,
            maxDepth=2,
            flows=True,
            apiProbes=True,
            authorisedBy="the fixture suite",
            authorisedHosts=["127.0.0.1"],
        )
        summaries = [
            asyncio.run(
                execute(
                    RunRequest(
                        target=f"{base}/app/",
                        out_dir=tmp_path / f"run{index}",
                        config=config.model_copy(deep=True),
                        report=False,
                    )
                )
            )
            for index in range(2)
        ]
    finally:
        server.shutdown()
        server.server_close()

    assert all(s.status.value == "complete" for s in summaries), summaries
    first, second = (runner.read(RunPaths(_root(s))) for s in summaries)

    assert _signature(first) == _signature(second), _explain(first, second)
    assert first.checkersRan == second.checkersRan
    assert first.checkersSkipped == second.checkersSkipped

    # And literally byte-identical once the run's own identifiers are taken out, which is
    # the strongest form of the claim and the one worth defending.
    assert _bytes(first) == _bytes(second)


RUN_SCOPED = ("runId", "generatedAt", "firstSeenRunId", "lastSeenRunId", "createdAt")


def _bytes(payload: IssuesFile) -> bytes:
    """`issues.json` with the run's own identity blanked out."""
    text = payload.model_dump_json(indent=2)
    document = json.loads(text)
    return json.dumps(_blank(document), indent=2, sort_keys=True).encode()


def _blank(value: object) -> object:
    if isinstance(value, dict):
        return {k: ("" if k in RUN_SCOPED else _blank(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_blank(v) for v in value]
    return value


def _root(summary: Any) -> Path:
    root = summary.root
    assert root is not None
    return Path(root)


def _signature(payload: IssuesFile) -> list[tuple[str, ...]]:
    """Everything about an issue that is not the run it came from."""
    return sorted(
        (
            issue.fingerprint,
            issue.checkerId,
            issue.issueKind,
            issue.severity.value,
            issue.title,
            str(issue.expected),
            str(issue.actual),
            str(issue.instanceCount),
            "|".join(sorted(i.fingerprint for i in issue.instances)),
        )
        for issue in payload.issues
    )


def _explain(first: IssuesFile, second: IssuesFile) -> str:
    a, b = dict.fromkeys(_signature(first)), dict.fromkeys(_signature(second))
    only_first = [x for x in a if x not in b]
    only_second = [x for x in b if x not in a]
    lines = [f"{len(first.issues)} vs {len(second.issues)} issues"]
    lines += [
        f"  only in the first run:  {x[1]} {x[4]} exp={x[5]} act={x[6]}" for x in only_first[:10]
    ]
    lines += [
        f"  only in the second run: {x[1]} {x[4]} exp={x[5]} act={x[6]}" for x in only_second[:10]
    ]
    return "\n".join(lines)


# ------------------------------------------------- volatile region nomination


def moved(key: str, dy: float, **over: Any) -> ElementRecord:
    return element(key, y=over.pop("y", 0) + dy, **over)


def test_a_shifted_container_does_not_nominate_everything_inside_it() -> None:
    """An ad slot loading a pixel taller pushes the whole page down.

    Measured against the document, that is a nomination for every element below it — 417
    of them on a real page, which is a list nobody reads. Measured against the container,
    it is one.
    """
    from engine.capture.volatile import compare

    def tree(shift: float) -> list[ElementRecord]:
        root = element("root", y=0)
        root.id, root.parentId = "el_root", None
        kids = []
        for index in range(6):
            kid = element(f"k{index}", y=100 + index * 50 + shift)
            kid.id, kid.parentId = f"el_k{index}", "el_root"
            kids.append(kid)
        root.box.y = shift
        root.boxViewport.y = shift
        return [root, *kids]

    candidates = compare(tree(0), tree(80))
    assert len(candidates) == 1, candidates
    assert candidates[0].stableKey == "root", "the container, not the six things inside it"
    assert candidates[0].kind == "moved"


def test_a_container_full_of_changing_things_is_nominated_once() -> None:
    """An ad frame whose internals differ every load is one volatile region, not forty."""
    from engine.capture.volatile import compare

    def tree(text: str) -> list[ElementRecord]:
        slot = element("slot", classes=["adsbygoogle"])
        slot.id, slot.parentId = "el_slot", None
        kids = []
        for index in range(8):
            kid = element(f"a{index}", y=index * 20)
            kid.id, kid.parentId = f"el_a{index}", "el_slot"
            kid.text = kid.textFull = f"{text}-{index}"
            kids.append(kid)
        return [slot, *kids]

    candidates = compare(tree("one"), tree("two"))
    assert len(candidates) == 1, [c.selector for c in candidates]
    assert candidates[0].kind == "region"
    assert "adsbygoogle" in candidates[0].selector


def test_an_unchanged_page_nominates_nothing() -> None:
    from engine.capture.volatile import compare

    page = [element("a"), element("b", y=100)]
    assert compare(page, page) == []
