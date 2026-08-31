"""The deterministic sweep against the deliberately-broken fixture — SPEC §8.

`tests/fixtures/site/DEFECTS.md` is the expected-results file and this asserts both
halves of it: every planted defect is found, and nothing else is reported. The second
half is the one that keeps the suite honest as it grows.

These run against `fixtures/broken`, a frozen run artifact. No browser: a checker is a
pure function over the artifact (CLAUDE.md), so its tests should not need one either.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest

from engine.checkers import runner
from engine.checkers.runner import CheckResult
from engine.fixtures import load_fixture
from engine.issues.models import Issue
from tests.conftest import MakeElement

DEFECTS = Path(__file__).parent / "fixtures" / "site" / "DEFECTS.md"
_ROW = re.compile(r"^\|\s*`([^`]+)`\s*\|\s*`([^`]+)`\s*\|([^|]*)\|")


def documented() -> dict[tuple[str, str], set[str]]:
    """Every `| checker | kind | pages |` row in DEFECTS.md."""
    rows: dict[tuple[str, str], set[str]] = {}
    for line in DEFECTS.read_text().splitlines():
        match = _ROW.match(line.strip())
        if not match:
            continue
        checker, kind, pages = match.groups()
        rows.setdefault((checker, kind), set()).update(
            p.strip() for p in pages.split(",") if p.strip()
        )
    return rows


@pytest.fixture(scope="module")
def checked() -> CheckResult:
    return runner.check(load_fixture("broken"))


def short_pages(issue: Issue) -> set[str]:
    return {
        instance.pagePath.removeprefix("/broken/").removesuffix(".html")
        for instance in issue.instances
    }


def found(result: CheckResult) -> dict[tuple[str, str], set[str]]:
    pairs: dict[tuple[str, str], set[str]] = {}
    for issue in result.issues:
        pairs.setdefault((issue.checkerId, issue.issueKind), set()).update(short_pages(issue))
    return pairs


def issues_of(result: CheckResult, checker_id: str) -> Iterator[Issue]:
    return (issue for issue in result.issues if issue.checkerId == checker_id)


# ------------------------------------------------------------------ the contract


def test_the_fixture_exercises_every_checker(checked: CheckResult) -> None:
    """Only the groups whose data this fixture does not carry may self-skip: no Figma
    file, no flows, no API probes, and no previous run to compare against. Anything else
    skipping would quietly stop testing a whole group, which is exactly what
    `Checker.requires` exists to make visible."""
    allowed = ("figma.", "functional.", "api.", "visual.")
    assert all(c.startswith(allowed) for c in checked.skipped), checked.skipped
    assert len(checked.ran) >= 55


def test_every_planted_defect_is_found(checked: CheckResult) -> None:
    missing = sorted(set(documented()) - set(found(checked)))
    assert not missing, "planted defects the sweep did not find: " + ", ".join(
        f"{c}/{k}" for c, k in missing
    )


def test_planted_defects_are_found_on_the_right_pages(checked: CheckResult) -> None:
    actual = found(checked)
    wrong: list[str] = []
    for key, pages in documented().items():
        if key not in actual:
            continue
        if not pages <= actual[key]:
            wrong.append(f"{key[0]}/{key[1]}: expected {sorted(pages)}, got {sorted(actual[key])}")
    assert not wrong, "\n".join(wrong)


def test_nothing_undocumented_is_reported(checked: CheckResult) -> None:
    """The false-positive bound, made exact. A new finding is either a real defect that
    belongs in DEFECTS.md or a bug in a checker — never something to leave unexplained."""
    extra = sorted(set(found(checked)) - set(documented()))
    assert not extra, "findings with no row in DEFECTS.md: " + ", ".join(
        f"{c}/{k}" for c, k in extra
    )


# ---------------------------------------------------------------- must not fire


def test_scattered_siblings_are_not_a_repeated_group(checked: CheckResult) -> None:
    signatures = {issue.data.get("signature") for issue in issues_of(checked, "layout.group-gaps")}
    assert "p" not in signatures


def test_white_is_never_off_palette(checked: CheckResult) -> None:
    for issue in issues_of(checked, "typography.palette"):
        assert "#ffffff" not in (issue.title + str(issue.actual)).lower()
        assert "#000000" not in (issue.title + str(issue.actual)).lower()


def test_injected_axe_does_not_count_as_unused_javascript(checked: CheckResult) -> None:
    assert not list(issues_of(checked, "performance.coverage"))


def test_a_redirect_to_a_captured_page_is_not_a_second_page(checked: CheckResult) -> None:
    for issue in issues_of(checked, "free.title"):
        if issue.issueKind != "duplicate-title":
            continue
        paths = [i.pagePath for i in issue.instances]
        assert len(paths) == len(set(paths)), paths


def test_an_error_page_only_reports_its_status(checked: CheckResult) -> None:
    on_error_page = {
        issue.checkerId
        for issue in checked.issues
        if any(i.pagePath == "/broken/does-not-exist.html" for i in issue.instances)
    }
    assert on_error_page == {"free.page-status"}


# ------------------------------------------------------------------- behaviour


def test_findings_are_grouped_not_repeated(checked: CheckResult) -> None:
    """SPEC §11: ten cards with the same defect is one issue with ten instances."""
    assert checked.findings > len(checked.issues) * 2


def test_issues_are_sorted_worst_first(checked: CheckResult) -> None:
    ranks = [issue.severity.rank for issue in checked.issues]
    assert ranks == sorted(ranks)


def test_widespread_findings_are_escalated(checked: CheckResult) -> None:
    """SPEC §8.3: the same finding on five or more pages is worth one more step."""
    cookies = next(i for i in issues_of(checked, "free.cookie-flags") if "secure" in i.issueKind)
    assert len(cookies.pagePaths) >= 5
    assert cookies.defaultSeverity.rank > cookies.severity.rank


def test_every_instance_carries_a_durable_fingerprint(checked: CheckResult) -> None:
    """Two instances may share a fingerprint, but only when they are genuinely the same
    component: SPEC §8.2 strips nth-child indices and coordinates on purpose, so two
    identical listing rows are one identity and a dismissal covers both."""
    for issue in checked.issues:
        by_fingerprint: dict[str, set[str]] = {}
        for instance in issue.instances:
            assert len(instance.fingerprint) == 40
            by_fingerprint.setdefault(instance.fingerprint, set()).add(instance.stableKey)
        for fingerprint, keys in by_fingerprint.items():
            assert len(keys) == 1, f"{fingerprint} spans several elements: {keys}"


# ------------------------------------------------------------------ end to end


@pytest.mark.browser
def test_a_live_capture_still_finds_the_planted_defects(
    broken_site_url: str, browser_ready: None, tmp_path: Path
) -> None:
    """`fixtures/broken` is frozen, so it cannot catch a capture regression. This can:
    it crawls the same site for real and re-runs the sweep over the result."""
    import asyncio

    from engine.artifact.context import RunContext
    from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
    from engine.capture.run import capture

    config = RunConfig(
        viewports=[VIEWPORT_PRESETS["mobile_390"], VIEWPORT_PRESETS["desktop_1440"]],
        maxPages=10,
        maxDepth=3,
        settleMs=120,
        vitalsSamples=1,
        include=[r"/broken/"],
        dictionary=["stylesheet", "noindex", "viewport"],
    )
    result = asyncio.run(capture(f"{broken_site_url}broken/index.html", tmp_path, config=config))
    assert result.problems == []

    fresh = runner.check(RunContext.open(result.paths.root))
    missing = sorted(set(documented()) - set(found(fresh)))
    assert not missing, "a live capture missed: " + ", ".join(f"{c}/{k}" for c, k in missing)


def test_casing_ignores_elements_that_name_a_value(make_element: MakeElement) -> None:
    """A brand filter holds database rows, not house style — SPEC §8.4 D.

    The filter list on a real site held junk test rows in lower case alongside real
    brands in title case, so lower case was the plurality and the *brands* came back as
    the finding. House style is a rule about the interface, not about what it displays.
    """
    from engine.artifact.models import LayoutRecord, PageRecord, Viewport
    from engine.checkers.content.casing import _control_groups
    from engine.checkers.support import Surface

    def control(eid: str, tag: str, text: str) -> object:
        return make_element(
            id=eid, tag=tag, text=text, textFull=text, role=None, clickable=True, parentId="p1"
        )

    elements = [
        control("el_1", "label", "ForgeFlex Tools"),
        control("el_2", "label", "some name"),
        control("el_3", "a", "Sign in"),
    ]
    surface = Surface(
        page=PageRecord(id="p", url="https://x.test/", path="/", status=200, depth=0),
        viewport=Viewport(name="desktop_1440", width=1440, height=900),
        elements=elements,  # type: ignore[arg-type]
        layout=LayoutRecord(pageId="p", viewport="desktop_1440"),
    )
    grouped = [e.text for members in _control_groups(surface).values() for e in members]
    assert grouped == ["Sign in"], "labels carry data, not a house style"
