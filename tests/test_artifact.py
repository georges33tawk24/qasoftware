"""The artifact is the contract everything else depends on (SPEC §4).

If a write→read→write cycle is not byte-stable, every downstream promise about
reproducible runs is worthless.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from engine.artifact import store
from engine.artifact.models import RunArtifact
from engine.fixtures import fixture_path


def tree(root: Path) -> dict[str, bytes]:
    return {
        str(p.relative_to(root)): p.read_bytes() for p in sorted(root.rglob("*")) if p.is_file()
    }


@pytest.fixture
def tiny() -> RunArtifact:
    return store.read_run(fixture_path("tiny"))


def test_round_trip_is_identical(tiny: RunArtifact, tmp_path: Path) -> None:
    store.write_run(tmp_path, tiny)
    assert store.read_run(tmp_path) == tiny


def test_round_trip_is_byte_identical(tmp_path: Path, tiny: RunArtifact) -> None:
    store.write_run(tmp_path, tiny)
    assert tree(tmp_path) == tree(fixture_path("tiny"))


def test_the_frozen_fixture_validates() -> None:
    assert store.validate(fixture_path("tiny")) == []


def test_unknown_fields_are_rejected(tmp_path: Path, tiny: RunArtifact) -> None:
    """extra=forbid: schema drift is an error, not a shrug."""
    store.write_run(tmp_path, tiny)
    run_json = store.RunPaths(tmp_path).run
    payload = json.loads(run_json.read_text())
    payload["totallyNewField"] = 1
    run_json.write_text(json.dumps(payload))
    problems = store.validate(tmp_path)
    assert problems and "totallyNewField" in problems[0]


def test_validate_catches_a_missing_page(tmp_path: Path, tiny: RunArtifact) -> None:
    paths = store.write_run(tmp_path, tiny)
    paths.page("p_home").unlink()
    assert store.validate(tmp_path) == [
        "run.json declares page 'p_home' but pages/p_home/page.json is missing"
    ]


def test_validate_catches_a_missing_viewport(tmp_path: Path, tiny: RunArtifact) -> None:
    paths = store.write_run(tmp_path, tiny)
    paths.elements("p_home", "desktop_1440").unlink()
    problems = store.validate(tmp_path)
    assert any("missing viewport 'desktop_1440'" in p for p in problems)


def test_validate_catches_a_dangling_element_reference(tmp_path: Path, tiny: RunArtifact) -> None:
    tiny.pages[0].elements["desktop_1440"][1].parentId = "el_ghost"
    store.write_run(tmp_path, tiny)
    problems = store.validate(tmp_path)
    assert any("unknown parentId 'el_ghost'" in p for p in problems)


def test_validate_catches_a_layout_referencing_a_dead_element(
    tmp_path: Path, tiny: RunArtifact
) -> None:
    tiny.pages[0].layout["desktop_1440"].alignmentSets[0].elementIds.append("el_ghost")
    store.write_run(tmp_path, tiny)
    problems = store.validate(tmp_path)
    assert any("alignment set references unknown element 'el_ghost'" in p for p in problems)


def test_only_the_spec_style_properties_are_captured() -> None:
    """SPEC §4.1 — never the ~340-property full computed style."""
    from engine.artifact.models import ElementStyles

    assert len(ElementStyles.model_fields) < 40  # the full dump is ~340


def test_issues_round_trip_too() -> None:
    """`issues.json` is written by `bureau check` and read back by the run diff.
    Derived values must stay derived — a serialised copy would break `extra=forbid`."""
    from engine.issues.models import Category, Finding, Instance, Issue, Severity

    issue = Issue(
        id="iss_001",
        fingerprint="f00",
        checkerId="layout.alignment",
        issueKind="misaligned-sibling",
        category=Category.layout,
        severity=Severity.minor,
        defaultSeverity=Severity.minor,
        title="Card is 3px off its siblings",
        instances=[
            Instance(
                fingerprint="f00-1",
                pageId="p_home",
                pagePath="/",
                viewport="desktop_1440",
                stableKey="abc",
            )
        ],
    )
    assert Issue.model_validate_json(issue.model_dump_json()) == issue
    assert issue.instanceCount == 1
    assert issue.pagePaths == ["/"]

    finding = Finding(
        checkerId="layout.alignment",
        issueKind="misaligned-sibling",
        category=Category.layout,
        severity=Severity.minor,
        title="Card is 3px off its siblings",
        pageId="p_home",
        pagePath="/",
        viewport="desktop_1440",
        stableKey="abc",
    )
    assert Finding.model_validate_json(finding.model_dump_json()) == finding
    assert len(finding.fingerprint) == 40
