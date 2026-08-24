from __future__ import annotations

import pytest

from engine.artifact.context import Capability
from engine.fixtures import available, fixture_path, load_fixture


def test_the_tiny_fixture_loads() -> None:
    assert "tiny" in available()
    ctx = load_fixture("tiny")
    assert ctx.run_id == "run_tiny"
    assert [p.id for p in ctx.pages()] == ["p_home"]
    assert [v.name for v in ctx.viewports] == ["desktop_1440"]
    assert len(ctx.elements("p_home", "desktop_1440")) == 3


def test_a_missing_fixture_says_what_is_available() -> None:
    with pytest.raises(FileNotFoundError, match="tiny"):
        fixture_path("does-not-exist")


def test_capabilities_come_from_what_is_on_disk() -> None:
    ctx = load_fixture("tiny")
    caps = ctx.capabilities()
    assert caps == {
        Capability.ELEMENTS,
        Capability.LAYOUT,
        Capability.DOM,
        Capability.CONSOLE,
        Capability.NETWORK,
        Capability.VITALS,
    }
    assert ctx.has(Capability.ELEMENTS, Capability.LAYOUT)
    assert not ctx.has(Capability.FIGMA)
    assert not ctx.has(Capability.SCREENSHOT)


def test_reads_are_cached_not_re_read() -> None:
    ctx = load_fixture("tiny")
    assert ctx.elements("p_home", "desktop_1440") is ctx.elements("p_home", "desktop_1440")


def test_page_side_data_loads() -> None:
    ctx = load_fixture("tiny")
    assert [m.level for m in ctx.console("p_home")] == ["error"]
    assert ctx.network("p_home")[0].size.transferBytes == 4210
    vitals = ctx.vitals("p_home")
    assert vitals is not None and vitals.lcp == 1180.0
    assert ctx.axe("p_home") is None
    assert ctx.element_index("p_home", "desktop_1440")["el_0002"].tag == "h1"
