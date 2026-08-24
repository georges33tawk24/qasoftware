"""Capture integration — SPEC §4, §5.

Against a local static site, never the live internet: these have to be reproducible.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from engine.artifact import store
from engine.artifact.context import Capability, RunContext
from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
from engine.capture.challenge import RunBlocked
from engine.capture.run import CaptureResult, capture

pytestmark = pytest.mark.browser


def fast_config(**over: object) -> RunConfig:
    defaults: dict[str, object] = {
        "viewports": [VIEWPORT_PRESETS["desktop_1440"]],
        "maxPages": 4,
        "maxDepth": 2,
        "settleMs": 100,
        "vitalsSamples": 1,
    }
    defaults.update(over)
    return RunConfig(**defaults)  # type: ignore[arg-type]


def run_capture(url: str, out: Path, **over: object) -> CaptureResult:
    return asyncio.run(capture(url, out, config=fast_config(**over)))


@pytest.fixture(scope="module")
def captured(
    site_url: str, browser_ready: None, tmp_path_factory: pytest.TempPathFactory
) -> CaptureResult:
    return run_capture(site_url, tmp_path_factory.mktemp("capture"))


def test_the_artifact_validates(captured: CaptureResult) -> None:
    assert captured.problems == []
    assert captured.blocked == 0


def test_the_crawl_finds_the_linked_pages(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    paths = sorted(page.path for page in ctx.pages())
    assert "/index.html" in paths
    assert "/about.html" in paths
    assert "/contact.html" in paths


def test_robots_txt_is_respected_by_default(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    assert not [page for page in ctx.pages() if page.path == "/private.html"]


def test_discovered_from_explains_how_a_page_was_reached(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    seed = next(p for p in ctx.pages() if p.depth == 0)
    assert seed.discoveredFrom is None
    assert all(p.discoveredFrom for p in ctx.pages() if p.depth > 0)


def test_elements_carry_the_spec_fields(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    home = next(p for p in ctx.pages() if p.path == "/index.html")
    elements = ctx.elements(home.id, "desktop_1440")

    heading = next(e for e in elements if e.tag == "h1")
    assert heading.text == "Latest news"
    assert heading.role == "heading"
    assert heading.styles.fontSize == 36.0
    assert heading.styles.fontWeight == 700
    assert heading.contrast is not None and heading.contrast > 15
    assert heading.resolvedBackground == "rgb(255, 255, 255)"
    assert heading.nearestLandmark == "main"

    cta = next(e for e in elements if e.testId == "read-more")
    assert cta.link is not None
    assert cta.link.external is False
    assert cta.clickable and cta.focusable
    assert cta.styles.borderRadius == [6.0, 6.0, 6.0, 6.0]
    assert cta.nearestHeading == "Latest news"

    image = next(e for e in elements if e.image is not None)
    assert image.image is not None
    assert (image.image.naturalW, image.image.naturalH) == (480, 240)
    assert (image.image.renderedW, image.image.renderedH) == (240.0, 120.0)
    assert image.image.bytes and image.image.bytes > 0
    assert image.image.format == "png"


def test_only_the_spec_style_properties_are_captured(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    home = next(p for p in ctx.pages() if p.path == "/index.html")
    raw = store.RunPaths(captured.paths.root).elements(home.id, "desktop_1440").read_text()
    assert '"gridTemplateColumns"' not in raw
    assert '"webkitFontSmoothing"' not in raw


def test_layout_derivation_landed(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    home = next(p for p in ctx.pages() if p.path == "/index.html")
    layout = ctx.layout(home.id, "desktop_1440")
    assert any(g.signature == "article.card" and g.count == 3 for g in layout.repeatedGroups)
    assert layout.spacingHistogram
    assert any(t.fontSize == 36.0 for t in layout.typeInventory)
    assert any(c.property == "color" for c in layout.colourInventory)


def test_console_network_and_vitals_were_captured(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    home = next(p for p in ctx.pages() if p.path == "/index.html")

    assert any("analytics.init is not a function" in m.text for m in ctx.console(home.id))

    types = {entry.type for entry in ctx.network(home.id)}
    assert {"document", "stylesheet", "script", "image"} <= types
    assert all(entry.size.transferBytes >= 0 for entry in ctx.network(home.id))

    vitals = ctx.vitals(home.id)
    assert vitals is not None and vitals.lcp is not None and vitals.ttfb is not None


def test_screenshots_and_dom_exist(captured: CaptureResult) -> None:
    paths = store.RunPaths(captured.paths.root)
    ctx = RunContext.open(captured.paths.root)
    home = next(p for p in ctx.pages() if p.path == "/index.html")
    assert paths.full_png(home.id, "desktop_1440").stat().st_size > 0
    assert paths.fold_png(home.id, "desktop_1440").stat().st_size > 0
    assert "<h1" in (ctx.dom(home.id) or "")


def test_capabilities_reflect_the_capture(captured: CaptureResult) -> None:
    ctx = RunContext.open(captured.paths.root)
    caps = ctx.capabilities()
    assert {
        Capability.ELEMENTS,
        Capability.LAYOUT,
        Capability.DOM,
        Capability.SCREENSHOT,
        Capability.CONSOLE,
        Capability.NETWORK,
        Capability.VITALS,
        Capability.COVERAGE,
        Capability.A11Y_TREE,
    } <= caps
    assert Capability.FIGMA not in caps


def test_two_runs_of_an_unchanged_site_agree(
    site_url: str, browser_ready: None, tmp_path: Path
) -> None:
    """SPEC §20's real line. elements.json holds no timestamps, so it must be identical."""
    first = run_capture(site_url, tmp_path / "a", maxPages=2)
    second = run_capture(site_url, tmp_path / "b", maxPages=2)
    assert first.problems == second.problems == []

    a, b = store.RunPaths(first.paths.root), store.RunPaths(second.paths.root)
    assert a.page_ids() == b.page_ids()
    for pid in a.page_ids():
        for viewport in a.viewport_names(pid):
            assert (
                a.elements(pid, viewport).read_bytes() == b.elements(pid, viewport).read_bytes()
            ), f"{pid}/{viewport} drifted between runs"
            assert a.layout(pid, viewport).read_bytes() == b.layout(pid, viewport).read_bytes()


def test_robots_can_be_overridden(site_url: str, browser_ready: None, tmp_path: Path) -> None:
    result = run_capture(site_url, tmp_path, respectRobots=False, maxPages=8)
    ctx = RunContext.open(result.paths.root)
    assert [page for page in ctx.pages() if page.path == "/private.html"]


def test_a_challenge_page_aborts_the_run(
    site_url: str, browser_ready: None, tmp_path: Path
) -> None:
    """Never let a challenge page produce a 'blank page' bug."""
    with pytest.raises(RunBlocked, match="blocked by bot protection"):
        run_capture(f"{site_url}challenge.html", tmp_path, maxPages=1)
