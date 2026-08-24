"""Annotation and the HTML report — SPEC §12.1, §12.2."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import pytest
from PIL import Image

from engine.artifact.models import Box
from engine.checkers import runner
from engine.fixtures import load_fixture
from engine.issues.models import Category, Instance, Issue, IssuesFile, Severity
from engine.report import compose, html
from engine.report.annotate import (
    Annotation,
    annotate,
    crop_window,
    ring_colours,
    sample_background,
)


def flat_png(path: Path, size: tuple[int, int], colour: tuple[int, int, int]) -> Path:
    Image.new("RGB", size, colour).save(path)
    return path


# ------------------------------------------------------------------ annotation


def test_the_crop_keeps_context_around_the_region(tmp_path: Path) -> None:
    """A full-page screenshot with a tiny circle in it is useless (SPEC §12.2)."""
    shot = flat_png(tmp_path / "full.png", (1440, 3000), (250, 250, 250))
    out = annotate(
        shot,
        tmp_path / "out.png",
        [Annotation(number=1, box=Box(x=100, y=1500, w=40, h=20), severity=Severity.major)],
    )
    assert out is not None
    with Image.open(out) as cropped:
        assert cropped.width >= 420
        assert cropped.height >= 420
        assert cropped.width < 1440


def test_the_crop_scales_with_the_device_pixel_ratio() -> None:
    image = Image.new("RGB", (2000, 2000))
    one = crop_window(image, [(100, 100, 140, 120)], 1.0)
    two = crop_window(image, [(100, 100, 140, 120)], 2.0)
    assert (two[2] - two[0]) > (one[2] - one[0])


def test_nearby_rings_share_one_crop() -> None:
    image = Image.new("RGB", (2000, 2000))
    window = crop_window(image, [(100, 100, 140, 120), (600, 100, 700, 120)], 1.0)
    assert window[0] <= 100
    assert window[2] >= 700


def test_a_distant_ring_does_not_drag_the_crop_across_the_page() -> None:
    image = Image.new("RGB", (2000, 4000))
    window = crop_window(image, [(100, 100, 140, 120), (100, 3800, 140, 3820)], 1.0)
    assert window[3] < 3000


def test_the_ring_gets_a_halo_when_it_would_disappear() -> None:
    """SPEC §12.2 colour-codes by severity, so contrast is bought with a halo rather
    than by changing the hue."""
    visible, no_halo = ring_colours(Severity.blocker, (255, 255, 255))
    assert no_halo == visible  # red on white needs no help

    on_amber, halo = ring_colours(Severity.major, (201, 154, 46))
    assert on_amber == ring_colours(Severity.major, (255, 255, 255))[0]  # same hue
    assert halo != on_amber


def test_the_background_is_sampled_from_around_the_region(tmp_path: Path) -> None:
    with Image.open(flat_png(tmp_path / "s.png", (200, 200), (10, 20, 30))) as image:
        assert sample_background(image, (50, 50, 100, 100)) == (10, 20, 30)


def test_nothing_is_drawn_without_a_screenshot(tmp_path: Path) -> None:
    assert annotate(tmp_path / "missing.png", tmp_path / "o.png", []) is None


# ---------------------------------------------------------------------- report


def issue(**over: object) -> Issue:
    fields: dict[str, object] = {
        "id": "iss_1",
        "fingerprint": "f" * 40,
        "checkerId": "layout.alignment",
        "issueKind": "misaligned-x",
        "category": Category.layout,
        "severity": Severity.major,
        "defaultSeverity": Severity.minor,
        "title": "Left edge is 5px off its siblings",
        "description": "Three of four siblings share a left edge.",
        "expected": "24px",
        "actual": "29px",
        "instances": [
            Instance(
                fingerprint="a" * 40,
                pageId="p_home",
                pagePath="/",
                viewport="desktop_1440",
                stableKey="k1",
                selector="main > div.card",
                actual="29px",
            )
        ],
    }
    fields.update(over)
    return Issue(**fields)  # type: ignore[arg-type]


def issues_file(*issues: Issue) -> IssuesFile:
    return IssuesFile(
        runId="run_1",
        generatedAt=datetime(2026, 1, 15, tzinfo=UTC),
        checkersRan=["layout.alignment", "free.console", "a11y.axe"],
        checkersSkipped={"figma.position": "needs figma"},
        issues=list(issues),
    )


@pytest.fixture
def rendered(tmp_path: Path) -> str:
    ctx = load_fixture("tiny")
    composed = compose.compose(ctx, issues_file(issue()), tmp_path)
    return html.render(composed, inline=True)


def test_the_report_is_self_contained(rendered: str) -> None:
    """No CDN, no external fonts, no build step: it has to survive being emailed."""
    assert "<link" not in rendered
    assert not re.search(r'src\s*=\s*"https?://', rendered)
    assert not re.search(r"@import|fonts\.googleapis|cdn\.", rendered)
    assert rendered.count("<script") == 2  # the JSON payload and the renderer


def test_the_report_carries_the_run_header(rendered: str) -> None:
    assert "https://example.test/" in rendered
    assert "desktop_1440" in rendered
    assert "Requested changes" in rendered


def test_the_appendix_says_what_was_checked(rendered: str) -> None:
    """People need to see what was checked to trust what wasn't flagged (SPEC §12.1)."""
    assert "free.console" in rendered
    assert "a11y.axe" in rendered
    assert "ran and found nothing" in rendered
    assert "figma.position" in rendered


def test_hostile_page_content_cannot_escape_the_payload(tmp_path: Path) -> None:
    """Every string in this report comes from a site we do not control."""
    nasty = "</script><img src=x onerror=alert(1)><b>\"'&"
    ctx = load_fixture("tiny")
    composed = compose.compose(ctx, issues_file(issue(title=nasty)), tmp_path)
    document = html.render(composed, inline=True)

    payload = document.split('id="report-data">', 1)[1].split("</script>", 1)[0]
    # Nothing in the payload can close the element it sits in, so the markup is inert…
    assert "</script><img" not in document
    assert "<" not in payload
    # …and it still round-trips exactly, so the report shows what the site really said.
    assert json.loads(payload.replace("\\u003c", "<"))["issues"][0]["title"] == nasty


def test_severity_counts_come_from_the_issues(tmp_path: Path) -> None:
    ctx = load_fixture("tiny")
    composed = compose.compose(
        ctx,
        issues_file(issue(), issue(id="iss_2", severity=Severity.trivial, issueKind="other")),
        tmp_path,
    )
    assert composed.payload["counts"]["major"] == 1
    assert composed.payload["counts"]["trivial"] == 1
    assert composed.payload["totals"]["issues"] == 2


def test_evidence_needs_a_screenshot(tmp_path: Path) -> None:
    """`fixtures/tiny` has no PNGs, so there is nothing to annotate and the report says
    so by simply having no figure — never a broken image."""
    ctx = load_fixture("tiny")
    composed = compose.compose(ctx, issues_file(issue()), tmp_path)
    assert composed.media == []
    assert composed.payload["issues"][0]["evidence"] == []


def test_media_moves_out_of_the_file_when_it_gets_too_big(tmp_path: Path) -> None:
    ctx = load_fixture("tiny")
    composed = compose.compose(ctx, issues_file(issue()), tmp_path)
    composed.media.append(
        compose.Media(issue_id="iss_1", name="evidence.png", data=b"x" * 7_000_000, caption="")
    )
    assert not composed.inline


def test_the_frozen_fixture_renders_end_to_end(tmp_path: Path) -> None:
    ctx = load_fixture("broken")
    result = runner.check(ctx)
    payload = IssuesFile(
        runId=ctx.run_id,
        generatedAt=datetime(2026, 1, 15, tzinfo=UTC),
        checkersRan=result.ran,
        checkersSkipped=result.skipped,
        issues=result.issues,
    )
    document = html.render(compose.compose(ctx, payload, tmp_path), inline=True)
    assert len(document) > 50_000
    assert "82" in document or str(len(result.issues)) in document


# ------------------------------------------------------------------ end to end


@pytest.mark.browser
def test_the_report_opens_clean_and_filters(
    broken_site_url: str, browser_ready: None, tmp_path: Path
) -> None:
    """Dogfooding: capture the broken site, report on it, then open the report in a real
    browser and check it renders without a console error and that filtering works."""
    import asyncio

    from playwright.async_api import async_playwright

    from engine.artifact.context import RunContext
    from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
    from engine.capture.run import capture
    from engine.report import build

    config = RunConfig(
        viewports=[VIEWPORT_PRESETS["desktop_1440"]],
        maxPages=3,
        maxDepth=1,
        settleMs=100,
        vitalsSamples=1,
        include=[r"/broken/"],
    )
    run = asyncio.run(capture(f"{broken_site_url}broken/index.html", tmp_path, config=config))
    ctx = RunContext.open(run.paths.root)
    runner.write(run.paths, ctx, runner.check(ctx))
    report = build(run.paths.root)

    assert report.media > 0, "no annotated evidence was produced"
    assert report.inlined, "the report should be small enough to inline"

    async def open_it() -> tuple[list[str], int, int]:
        async with async_playwright() as pw:
            browser = await pw.chromium.launch()
            page = await browser.new_page(viewport={"width": 1280, "height": 900})
            errors: list[str] = []
            page.on("console", lambda m: errors.append(m.text) if m.type == "error" else None)
            page.on("pageerror", lambda e: errors.append(e.message))
            await page.goto(report.path.as_uri(), wait_until="load")
            total = await page.locator("details.issue").count()
            await page.get_by_role("button", name=re.compile("^major")).click()
            filtered = await page.locator("details.issue").count()
            await browser.close()
            return errors, total, filtered

    console_errors, total, filtered = asyncio.run(open_it())
    assert console_errors == []
    assert total > 0
    assert filtered < total, "turning a severity off should remove issues from the list"


def test_a_dismissed_issue_never_reaches_the_report(tmp_path: Path) -> None:
    """SPEC §11: filtered before the report is rendered, not greyed out inside it."""
    import json
    import shutil

    from engine.artifact.context import RunContext
    from engine.artifact.store import RunPaths
    from engine.checkers import runner
    from engine.fixtures import fixture_path
    from engine.report.build import build

    artifact = tmp_path / "run"
    shutil.copytree(fixture_path("broken"), artifact)
    ctx = RunContext.open(artifact)
    issues = runner.write(RunPaths(artifact), ctx, runner.check(ctx))

    victim = issues.issues[0]
    (artifact / "dismissed.json").write_text(json.dumps({"fingerprints": [victim.fingerprint]}))

    result = build(artifact)
    assert result.issues == len(issues.issues) - 1
    document = (artifact / "report.html").read_text()
    assert victim.title not in document
    assert victim.fingerprint not in document
