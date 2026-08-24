"""Figma ingestion, matching and catalogue group J — SPEC §6, §7, §8.4 J.

Runs against `fixtures/design`, a frozen artifact whose Figma file was generated from the
capture beside it and then deliberately walked away from. No network, no browser.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

import pytest

from engine.artifact.models import Viewport
from engine.artifact.store import RunPaths, write_bytes
from engine.checkers import runner
from engine.checkers.runner import CheckResult
from engine.figma import frames as frame_mapping
from engine.figma import normalise, tokens
from engine.figma.client import FigmaClient, FigmaError
from engine.figma.models import FigmaDocument, NodeRole
from engine.fixtures import fixture_path, load_fixture
from engine.matching.assign import solve
from engine.matching.engine import match_surface
from engine.matching.models import MappingFile
from engine.matching.run import run as run_matching
from engine.matching.signals import WEIGHTS, combine, levenshtein_ratio, normalise_text

DELTAS = Path(__file__).parent / "fixtures" / "figma" / "DELTAS.md"
OTHER = Path(__file__).parent / "fixtures" / "figma" / "other.json"
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|")


def documented() -> set[tuple[str, str]]:
    rows: set[tuple[str, str]] = set()
    for line in DELTAS.read_text().splitlines():
        match = _ROW.match(line.strip())
        if match and match.group(1).startswith("figma."):
            rows.add((match.group(1), match.group(2)))
    return rows


@pytest.fixture(scope="module")
def design() -> FigmaDocument:
    document = load_fixture("design").figma()
    assert document is not None
    return document


@pytest.fixture(scope="module")
def checked() -> CheckResult:
    return runner.check(load_fixture("design"))


# --------------------------------------------------------------- normalisation


def test_colours_come_back_as_css(design: FigmaDocument) -> None:
    """Figma speaks 0–1 floats and everything downstream speaks CSS (SPEC §6)."""
    assert normalise.colour({"r": 1.0, "g": 0.0, "b": 0.5, "a": 1.0}) == "rgb(255, 0, 128)"
    assert normalise.colour({"r": 0, "g": 0, "b": 0, "a": 0.5}) == "rgba(0, 0, 0, 0.5)"
    assert normalise.colour(None) is None
    assert all(
        node.fill is None or node.fill.startswith(("rgb(", "rgba("))
        for node in design.nodes.values()
    )


def test_opacity_is_multiplied_down_the_tree() -> None:
    """Opacity nests in Figma and does not in CSS."""
    raw = {
        "document": {
            "type": "DOCUMENT",
            "children": [
                {
                    "id": "0:1",
                    "type": "CANVAS",
                    "name": "Page",
                    "children": [
                        {
                            "id": "1:1",
                            "type": "FRAME",
                            "name": "F",
                            "opacity": 0.5,
                            "absoluteBoundingBox": {"x": 0, "y": 0, "width": 10, "height": 10},
                            "children": [
                                {
                                    "id": "1:2",
                                    "type": "RECTANGLE",
                                    "name": "R",
                                    "opacity": 0.5,
                                    "absoluteBoundingBox": {
                                        "x": 0,
                                        "y": 0,
                                        "width": 5,
                                        "height": 5,
                                    },
                                }
                            ],
                        }
                    ],
                }
            ],
        }
    }
    document = normalise.normalise(raw, "k")
    assert document.nodes["1:2"].opacity == 0.25


def test_a_container_borrows_its_label(design: FigmaDocument) -> None:
    """A designer draws a button as a filled frame with a label inside; the DOM keeps
    both on one element."""
    button = next(n for n in design.nodes.values() if n.name == "Button/Read more")
    assert button.characters is None
    assert button.text == "Read more"
    assert button.role is NodeRole.button


def test_roles_come_from_layer_names_and_node_types(design: FigmaDocument) -> None:
    roles = {node.name: node.role for node in design.nodes.values()}
    assert roles["Heading/Latest news"] is NodeRole.heading
    assert roles["Button/Home"] is NodeRole.button
    assert roles["Image/logo"] is NodeRole.image


# --------------------------------------------------------------------- tokens


def test_tokens_are_extracted_from_the_file(design: FigmaDocument) -> None:
    """Extracted even when only a desktop frame exists — they are what let a mobile
    viewport with no frame be judged at all (SPEC §6)."""
    extracted = tokens.extract(design)
    assert extracted.palette
    assert 32.0 in extracted.typeScale
    assert extracted.fontWeights
    assert extracted.spacing
    assert 12.0 in extracted.radii


def test_the_palette_is_clustered_by_delta_e() -> None:
    """Two fills a designer considers the same colour should not become two tokens."""
    palette, usage = tokens.cluster_palette(
        Counter({"rgb(59, 125, 216)": 10, "rgb(59, 126, 216)": 3, "rgb(199, 68, 58)": 4})
    )
    assert palette == ["rgb(59, 125, 216)", "rgb(199, 68, 58)"]
    assert usage["rgb(59, 125, 216)"] == 13


def test_design_tokens_take_over_the_derived_scale() -> None:
    from engine.checkers import scales

    ctx = load_fixture("design")
    page = ctx.pages()[0]
    layouts = [ctx.layout(page.id, "desktop_1440")]
    assert scales.derive(layouts).source == "page"
    with_design = scales.derive(layouts, ctx.tokens())
    assert with_design.source == "design"
    assert with_design.fontSizes == sorted(ctx.tokens().typeScale)  # type: ignore[union-attr]


# ------------------------------------------------------------------ assignment


def test_assignment_beats_greedy() -> None:
    """Greedy takes the best pair first and forces the second into the wrong partner."""
    cost = [[1.0, 2.0], [2.0, 9.0]]
    pairs = solve(cost)
    assert sum(cost[r][c] for r, c in pairs) == 4.0  # greedy would score 10


def test_assignment_handles_more_nodes_than_elements() -> None:
    """Rows beyond the columns go unassigned, which is exactly an unmatched node."""
    assert len(solve([[1.0, 9.0], [9.0, 1.0], [5.0, 5.0]])) == 2
    assert len(solve([[1.0, 5.0, 9.0], [9.0, 1.0, 5.0]])) == 2
    assert solve([]) == []


# --------------------------------------------------------------------- signals


def test_the_weights_are_the_ones_in_the_spec() -> None:
    assert WEIGHTS == {
        "text": 0.35,
        "textSimilarity": 0.20,
        "position": 0.20,
        "role": 0.10,
        "size": 0.10,
        "name": 0.05,
    }


def test_text_matching_ignores_case_whitespace_and_punctuation() -> None:
    assert normalise_text("  Latest,  News! ") == normalise_text("latest news")
    assert levenshtein_ratio("read more", "read more") == 1.0
    assert 0.5 < levenshtein_ratio("read more", "read more now") < 1.0


def test_a_pair_with_no_text_can_still_clear_the_threshold() -> None:
    """0.55 of the weight is text. Without renormalising, no rectangle could ever match a
    div however perfectly it lined up."""
    perfect = {
        "text": 0.0,
        "textSimilarity": 0.0,
        "position": 1.0,
        "role": 1.0,
        "size": 1.0,
        "name": 1.0,
    }
    assert combine(perfect, has_text=False) == 1.0
    assert combine(perfect, has_text=True) < 0.55


# -------------------------------------------------------------------- matching


@pytest.fixture(scope="module")
def mapping() -> MappingFile:
    ctx = load_fixture("design")
    document = ctx.figma()
    assert document is not None
    page = ctx.pages()[0]
    viewport = ctx.viewports[0]
    return match_surface(
        document,
        document.frames[0],
        ctx.elements(page.id, viewport.name),
        viewport,
        page_id=page.id,
    )


def test_the_frame_matches_the_page(mapping: MappingFile) -> None:
    assert mapping.anchors >= 8
    assert mapping.matched >= 15
    assert mapping.confident
    assert mapping.scale == 1.0


def test_every_match_records_why(mapping: MappingFile) -> None:
    """You live in this file when a false positive needs explaining."""
    for record in mapping.matches:
        if record.unmatched:
            assert record.rejectedBecause
        elif record.method == "assignment":
            assert set(record.signals) == set(WEIGHTS)
            assert record.score >= 0.55


def test_a_label_absorbed_into_its_container_is_marked(mapping: MappingFile) -> None:
    absorbed = [r for r in mapping.matches if r.method == "absorbed"]
    assert len(absorbed) == 1
    assert absorbed[0].nodeText == "Read more"


def test_the_planted_absence_and_addition_are_the_only_unmatched(
    mapping: MappingFile,
) -> None:
    assert mapping.unmatchedNodes == 1
    assert mapping.unmatchedElements == 1


def test_the_scale_factor_converts_a_narrower_frame() -> None:
    """SPEC §7 step 1: deltas are reported in live pixels after conversion."""
    ctx = load_fixture("design")
    document = ctx.figma()
    assert document is not None
    frame = document.frames[0]
    narrow = Viewport(name="half", width=int(frame.box.w // 2), height=900)
    page = ctx.pages()[0]
    result = match_surface(
        document, frame, ctx.elements(page.id, "desktop_1440"), narrow, page_id=page.id
    )
    assert result.scale == pytest.approx(0.5, abs=0.01)


# ------------------------------------------------------------- catalogue group J


def found(result: CheckResult) -> set[tuple[str, str]]:
    return {
        (issue.checkerId, issue.issueKind)
        for issue in result.issues
        if issue.checkerId.startswith("figma.")
    }


def test_every_planted_delta_is_found(checked: CheckResult) -> None:
    missing = sorted(documented() - found(checked))
    assert not missing, "planted deltas not found: " + ", ".join(f"{c}/{k}" for c, k in missing)


def test_nothing_undocumented_is_reported(checked: CheckResult) -> None:
    extra = sorted(found(checked) - documented())
    assert not extra, "deltas with no row in DELTAS.md: " + ", ".join(f"{c}/{k}" for c, k in extra)


def test_deltas_are_reported_in_live_pixels(checked: CheckResult) -> None:
    issue = next(i for i in checked.issues if i.issueKind == "design-position-x")
    assert issue.instanceCount == 1, "a shifted container must not cascade to its children"
    assert issue.data["deltaPx"] == -6.0


def test_presence_findings_stay_low_and_hedged(checked: CheckResult) -> None:
    """SPEC §7: never a property diff, always a possible missing or extra element."""
    for issue in checked.issues:
        if issue.checkerId != "figma.presence":
            continue
        assert issue.severity.value in ("minor", "trivial")
        assert "possib" in issue.title.lower()


def test_the_scale_checkers_step_aside_where_a_frame_matched(checked: CheckResult) -> None:
    noisy = {"typography.scale", "typography.palette", "layout.spacing-scale"}
    assert not {i.checkerId for i in checked.issues} & noisy


# ---------------------------------------------------------------- failure mode


def test_a_design_for_another_product_is_not_suggested(tmp_path: Path) -> None:
    ctx = load_fixture("design")
    document = normalise.normalise(json.loads(OTHER.read_text()), "other")
    best = frame_mapping.best_per_frame(frame_mapping.propose(document, ctx))
    assert not any(p.suggested for p in best.values())
    assert frame_mapping.resolve(document, ctx, confirmed={}, accept_suggested=True) == {}


def test_forcing_the_wrong_design_says_so_and_reports_no_deltas(tmp_path: Path) -> None:
    """The failure mode, deliberately: four hundred plausible deltas would be far worse
    than one honest "could not match"."""
    import shutil

    root = tmp_path / "wrong"
    shutil.copytree(fixture_path("design"), root)
    shutil.rmtree(root / "mapping", ignore_errors=True)
    (root / "issues.json").unlink(missing_ok=True)
    write_bytes(root / "figma" / "file.json", OTHER.read_bytes())

    from engine.artifact.context import RunContext

    ctx = RunContext.open(root)
    document = ctx.figma()
    assert document is not None
    page = ctx.pages()[0]
    run_matching(RunPaths(root), ctx, document, {document.frames[0].id: page.id})

    result = runner.check(RunContext.open(root))
    design_issues = [i for i in result.issues if i.checkerId.startswith("figma.")]
    assert [i.checkerId for i in design_issues] == ["figma.no-match"]
    assert "Could not match" in design_issues[0].title


# --------------------------------------------------------------------- client


def test_the_client_caches_by_file_version(tmp_path: Path) -> None:
    """The API is slow and rate-limited; a version already on disk is the same file."""
    calls: list[str] = []

    def fetch(url: str, headers: dict[str, str]) -> bytes:
        calls.append(url)
        if "depth=1" in url:
            return b'{"version": "7", "name": "F"}'
        return b'{"version": "7", "name": "F", "document": {"children": []}}'

    client = FigmaClient("t", cache_dir=tmp_path, fetch=fetch)
    client.cached_file("abc")
    first = len(calls)
    client.cached_file("abc")
    assert len(calls) == first + 1, "the second call should only check the version"


def test_api_errors_are_reported_not_swallowed(tmp_path: Path) -> None:
    def fetch(url: str, headers: dict[str, str]) -> bytes:
        return b'{"status": 403, "err": "Invalid token"}'

    with pytest.raises(FigmaError, match="Invalid token"):
        FigmaClient("t", fetch=fetch).file("abc")


def test_the_token_travels_in_the_header() -> None:
    seen: dict[str, str] = {}

    def fetch(url: str, headers: dict[str, str]) -> bytes:
        seen.update(headers)
        return b"{}"

    FigmaClient("sekrit", fetch=fetch).file("abc")
    assert seen["X-Figma-Token"] == "sekrit"


# -------------------------------------------------------------------- evidence


def test_side_by_side_evidence_is_produced(tmp_path: Path) -> None:
    """Design frame and live page, ringed on both, matched heights (SPEC §12.2)."""
    from PIL import Image

    from engine.report import compose

    ctx = load_fixture("design")
    issues = runner.check(ctx)
    payload = runner.write(RunPaths(tmp_path / "out"), ctx, issues)
    delta = next(i for i in payload.issues if i.checkerId == "figma.colour")
    media = compose.design_evidence(ctx, delta, 1, tmp_path)
    assert media is not None
    assert media.caption.endswith("live vs Home / Desktop")

    path = tmp_path / "sbs.png"
    path.write_bytes(media.data)
    with Image.open(path) as image:
        assert image.width > image.height  # two panes side by side
