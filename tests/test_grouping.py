"""Grouping, severity and the derived scales — SPEC §8.3, §11. No artifact needed."""

from __future__ import annotations

import pytest

from engine.artifact.models import LayoutRecord, SpacingBucket, TypeStyleUsage
from engine.checkers import colour, scales
from engine.issues.group import group
from engine.issues.models import Category, Finding, Severity
from engine.issues.severity import escalate


def finding(**over: object) -> Finding:
    fields: dict[str, object] = {
        "checkerId": "layout.alignment",
        "issueKind": "misaligned-x",
        "category": Category.layout,
        "severity": Severity.minor,
        "title": "Left edge is off",
        "expected": "24px",
        "actual": "27px",
        "pageId": "p_home",
        "pagePath": "/",
        "viewport": "desktop_1440",
        "stableKey": "key-a",
    }
    fields.update(over)
    return Finding(**fields)  # type: ignore[arg-type]


# ------------------------------------------------------------------- grouping


def test_same_defect_on_one_page_is_one_issue() -> None:
    issues = group([finding(stableKey=f"key-{i}") for i in range(10)], run_id="run_1")
    assert len(issues) == 1
    assert issues[0].instanceCount == 10


def test_a_different_actual_value_is_a_different_issue() -> None:
    issues = group([finding(), finding(actual="31px")], run_id="run_1")
    assert len(issues) == 2


def test_group_as_overrides_the_measured_values() -> None:
    """Ten tap targets are one issue even though every measurement differs."""
    findings = [finding(actual=f"{i}px", groupAs="drift", stableKey=f"k{i}") for i in range(10)]
    assert len(group(findings, run_id="run_1")) == 1


def test_a_repeated_component_merges_across_pages() -> None:
    """The same nav item on three pages is one issue, not three."""
    issues = group(
        [finding(pagePath=p, pageId=f"p{i}") for i, p in enumerate(["/", "/a", "/b"])],
        run_id="run_1",
    )
    assert len(issues) == 1
    assert issues[0].pagePaths == ["/", "/a", "/b"]


def test_different_components_on_different_pages_stay_apart() -> None:
    issues = group(
        [
            finding(pagePath="/", stableKey="one"),
            finding(pagePath="/a", pageId="p2", stableKey="two"),
        ],
        run_id="run_1",
    )
    assert len(issues) == 2


def test_sorting_puts_the_worst_first() -> None:
    issues = group(
        [
            finding(severity=Severity.trivial, issueKind="a"),
            finding(severity=Severity.blocker, issueKind="b"),
            finding(severity=Severity.minor, issueKind="c"),
        ],
        run_id="run_1",
    )
    assert [i.severity for i in issues] == [Severity.blocker, Severity.minor, Severity.trivial]


# ------------------------------------------------------------------- severity


def test_a_finding_on_five_pages_is_escalated() -> None:
    assert escalate(Severity.major, paths=["/a", "/b", "/c", "/d"]) is Severity.major
    assert escalate(Severity.major, paths=["/a", "/b", "/c", "/d", "/e"]) is Severity.critical


def test_checkout_and_auth_paths_are_escalated() -> None:
    assert escalate(Severity.major, paths=["/checkout"]) is Severity.critical
    assert escalate(Severity.major, paths=["/account/login"]) is Severity.critical
    assert escalate(Severity.major, paths=["/blog/checkout-tips"]) is Severity.major


def test_volume_of_a_cosmetic_problem_is_still_cosmetic() -> None:
    """A 5px misalignment on fifty pages is a 5px misalignment.

    Letting the page-count rule promote it to `critical` corrupts the sort order, and
    the sort order is the only thing that makes a 329-issue report readable.
    """
    everywhere = ["/a", "/b", "/c", "/d", "/e", "/checkout"]
    assert escalate(Severity.minor, paths=everywhere) is Severity.minor
    assert escalate(Severity.trivial, paths=everywhere) is Severity.trivial


def test_escalation_never_reaches_blocker() -> None:
    """Both rules can fire at once, and two steps from major lands on blocker — which
    would let a tap target 2px too small on five checkout pages outrank a login that does
    not work. `blocker` is a claim about a journey, so only a checker that ran one sets
    it."""
    widespread_and_sensitive = ["/checkout", "/a", "/b", "/c", "/d"]
    assert escalate(Severity.major, paths=widespread_and_sensitive) is Severity.critical
    assert escalate(Severity.critical, paths=widespread_and_sensitive) is Severity.critical
    assert escalate(Severity.blocker, paths=widespread_and_sensitive) is Severity.blocker


# --------------------------------------------------------------------- scales


def layout(gaps: list[tuple[float, int]], sizes: list[tuple[float, int]]) -> LayoutRecord:
    return LayoutRecord(
        pageId="p",
        viewport="desktop_1440",
        spacingHistogram=[SpacingBucket(gap=g, count=c) for g, c in gaps],
        typeInventory=[
            TypeStyleUsage(fontFamily="Inter", fontSize=s, fontWeight=400, count=c)
            for s, c in sizes
        ],
    )


def test_the_scale_comes_from_the_page_not_a_constant() -> None:
    """A site on a 5px rhythm is unusual, not broken."""
    derived = scales.derive([layout([(5, 20), (10, 18), (15, 12), (7, 1)], [(15, 30), (30, 8)])])
    assert derived.spacing == [5.0, 10.0, 15.0]
    assert derived.fontSizes == [15.0, 30.0]
    assert scales.off_scale(7.0, derived.spacing, 1.0)
    assert not scales.off_scale(10.4, derived.spacing, 1.0)
    assert scales.nearest_step(7.0, derived.spacing) == 5.0


def test_a_page_with_nothing_to_measure_has_no_scale() -> None:
    assert not scales.derive([layout([(24, 1)], [(16, 1)])]).usable


# --------------------------------------------------------------------- colour


def test_delta_e_uses_cielab_not_rgb_distance() -> None:
    """Sharma's CIEDE2000 reference pair: two blues 3 RGB units apart, ΔE 2.04."""
    assert colour.delta_e((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485)) == pytest.approx(
        2.0425, abs=5e-4
    )


def test_nearest_names_the_token_it_missed() -> None:
    match = colour.nearest("rgb(60, 126, 217)", ["#3b7dd8", "#c7443a"])
    assert match is not None
    token, delta = match
    assert token == "#3b7dd8"
    assert delta < colour.DEFAULT_DELTA_E


def test_translucent_colours_are_composited_before_comparison() -> None:
    assert colour.distance("rgba(0, 0, 0, 0)", "rgb(255, 255, 255)") == 0.0
