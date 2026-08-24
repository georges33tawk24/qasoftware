"""`layout.json` derivation — SPEC §4.2. Pure arithmetic over elements.json."""

from __future__ import annotations

from engine.artifact.models import Box, ElementRecord
from engine.capture.layout import (
    alignment_sets,
    colour_inventory,
    derive,
    repeated_groups,
    spacing_histogram,
    type_inventory,
)
from tests.conftest import MakeElement


def column(make_element: MakeElement, xs: list[float], parent: str = "el_p") -> list[ElementRecord]:
    return [
        make_element(
            id=f"el_{i}",
            parentId=parent,
            box=Box(x=x, y=100.0 + i * 100, w=200, h=60),
            boxViewport=Box(x=x, y=100.0 + i * 100, w=200, h=60),
        )
        for i, x in enumerate(xs)
    ]


def test_a_drifted_sibling_stays_in_its_alignment_set(make_element: MakeElement) -> None:
    """The whole point: cluster at 1px and the drifted element forms its own set of one,
    so nothing is ever flagged."""
    sets = alignment_sets(column(make_element, [24.0, 24.0, 27.0, 24.0]))
    x_sets = [s for s in sets if s.axis == "x"]
    assert len(x_sets) == 1
    assert len(x_sets[0].elementIds) == 4
    assert x_sets[0].median == 24.0
    assert x_sets[0].deviations["el_2"] == 3.0


def test_two_siblings_are_not_an_alignment_set(make_element: MakeElement) -> None:
    """A median of two says both moved equally, which is never the truth."""
    assert [s for s in alignment_sets(column(make_element, [24.0, 30.0])) if s.axis == "x"] == []


def test_a_genuinely_separate_column_is_its_own_set(make_element: MakeElement) -> None:
    elements = column(make_element, [24.0, 24.0, 24.0, 400.0, 400.0, 400.0])
    x_sets = sorted((s for s in alignment_sets(elements) if s.axis == "x"), key=lambda s: s.median)
    assert [s.median for s in x_sets] == [24.0, 400.0]


def test_repeated_groups_key_on_tag_and_classes(make_element: MakeElement) -> None:
    cards = [
        make_element(id=f"el_{i}", parentId="el_p", classes=["card"], tag="article")
        for i in range(3)
    ]
    odd = make_element(id="el_odd", parentId="el_p", classes=["banner"], tag="article")
    groups = repeated_groups([*cards, odd])
    assert [(g.signature, g.count) for g in groups] == [("article.card", 3)]


def test_spacing_histogram_counts_gaps_between_siblings(make_element: MakeElement) -> None:
    stack = [
        make_element(
            id=f"el_{i}",
            parentId="el_p",
            box=Box(x=0, y=y, w=200, h=40),
            boxViewport=Box(x=0, y=y, w=200, h=40),
        )
        for i, y in enumerate([0.0, 64.0, 128.0, 200.0])
    ]
    histogram = {bucket.gap: bucket.count for bucket in spacing_histogram(stack)}
    assert histogram == {24.0: 2, 32.0: 1}


def test_type_inventory_only_counts_elements_with_their_own_text(
    make_element: MakeElement,
) -> None:
    from tests.conftest import styles

    with_text = make_element(id="el_1", text="Hello", styles=styles(fontSize=36.0, fontWeight=700))
    wrapper = make_element(id="el_2", text="", styles=styles(fontSize=99.0))
    inventory = type_inventory([with_text, wrapper])
    assert [(t.fontSize, t.fontWeight, t.count) for t in inventory] == [(36.0, 700, 1)]


def test_colour_inventory_ignores_transparent_backgrounds(make_element: MakeElement) -> None:
    from tests.conftest import styles

    element = make_element(text="Hi", styles=styles(backgroundColor="rgba(0, 0, 0, 0)"))
    assert [(c.colour, c.property) for c in colour_inventory([element])] == [
        ("rgb(17, 17, 17)", "color")
    ]


def test_derive_ignores_invisible_elements(make_element: MakeElement) -> None:
    hidden = make_element(id="el_h", visible=False, parentId="el_p")
    layout = derive("p_home", "desktop_1440", [hidden])
    assert layout.pageId == "p_home"
    assert layout.viewport == "desktop_1440"
    assert layout.alignmentSets == []
    assert layout.typeInventory == []
