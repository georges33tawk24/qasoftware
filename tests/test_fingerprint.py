"""SPEC §8.2. Every dismissal in the product hangs off these hashes.

The rule: re-renders, content edits and coordinate shifts must NOT change the hash;
a different checker or a different kind of defect MUST.
"""

from __future__ import annotations

from engine.artifact.models import Box
from engine.issues.fingerprint import (
    ancestor_shape,
    element_stable_key,
    issue_fingerprint,
    normalise_path,
    normalise_text,
)
from engine.issues.models import Category, Finding, Severity
from tests.conftest import MakeElement, styles


def fp(**over: object) -> str:
    args: dict[str, object] = {
        "checker_id": "layout.alignment",
        "page_path": "/products",
        "viewport": "desktop_1440",
        "stable_key": "abc123",
        "issue_kind": "misaligned-sibling",
    }
    args.update(over)
    return issue_fingerprint(**args)  # type: ignore[arg-type]


# --------------------------------------------------------------- must NOT change


def test_nth_child_index_change_keeps_the_key(make_element: MakeElement) -> None:
    third = make_element(selector="main > section:nth-of-type(2) > div.card:nth-child(3)")
    ninth = make_element(selector="main > section:nth-of-type(5) > div.card:nth-child(9)")
    assert element_stable_key(third) == element_stable_key(ninth)


def test_positional_pseudo_classes_are_stripped(make_element: MakeElement) -> None:
    a = make_element(selector="main > ul > li.item:first-child")
    b = make_element(selector="main > ul > li.item:nth-last-child(2)")
    assert element_stable_key(a) == element_stable_key(b)


def test_coordinate_shift_keeps_the_key(make_element: MakeElement) -> None:
    before = make_element(box=Box(x=240, y=1180, w=320, h=410))
    after = make_element(box=Box(x=980, y=42, w=1, h=1), boxViewport=Box(x=0, y=0, w=1, h=1))
    assert element_stable_key(before) == element_stable_key(after)


def test_own_class_toggle_keeps_the_key(make_element: MakeElement) -> None:
    plain = make_element(selector="main > div.card", classes=["card"])
    active = make_element(
        selector="main > div.card.card--featured.is-active",
        classes=["card", "card--featured", "is-active"],
    )
    assert element_stable_key(plain) == element_stable_key(active)


def test_text_whitespace_and_case_are_normalised(make_element: MakeElement) -> None:
    tidy = make_element(text="Sign in")
    messy = make_element(text="  Sign\n  In  ")
    assert element_stable_key(tidy) == element_stable_key(messy)


def test_styles_are_not_in_the_key(make_element: MakeElement) -> None:
    a = make_element()
    b = make_element(styles=styles(color="rgb(255, 0, 0)", fontSize=99.0))
    assert element_stable_key(a) == element_stable_key(b)


def test_fingerprint_ignores_the_values(make_element: MakeElement) -> None:
    """A colour that is wrong and stays wrong with a *different* wrong value is the
    same issue — the values are deliberately not in the hash."""
    assert fp() == fp()

    def finding(actual: str) -> Finding:
        return Finding(
            checkerId="typography.colour",
            issueKind="off-palette-colour",
            category=Category.typography,
            severity=Severity.minor,
            title="Colour off palette",
            expected="#111111",
            actual=actual,
            pageId="p_home",
            pagePath="/products",
            viewport="desktop_1440",
            stableKey="abc123",
        )

    assert finding("#ff0000").fingerprint == finding("#00ff00").fingerprint


def test_page_path_is_normalised() -> None:
    assert normalise_path("https://x.test/products/?sort=asc#top") == "/products"
    assert normalise_path("/products/") == "/products"
    assert normalise_path("/") == "/"
    assert normalise_path("") == "/"
    assert fp(page_path="/products") == fp(page_path="https://x.test/products/?page=2")


# ------------------------------------------------------------------- MUST change


def test_checker_id_changes_the_fingerprint() -> None:
    assert fp() != fp(checker_id="layout.overflow")


def test_issue_kind_changes_the_fingerprint() -> None:
    assert fp() != fp(issue_kind="overlapping-sibling")


def test_viewport_changes_the_fingerprint() -> None:
    assert fp() != fp(viewport="mobile_390")


def test_stable_key_changes_the_fingerprint() -> None:
    assert fp() != fp(stable_key="def456")


def test_tag_role_testid_and_heading_change_the_key(make_element: MakeElement) -> None:
    base = element_stable_key(make_element())
    assert base != element_stable_key(make_element(tag="span"))
    assert base != element_stable_key(make_element(role="button"))
    assert base != element_stable_key(make_element(testId="news-card"))
    assert base != element_stable_key(make_element(nearestHeading="Older news"))
    assert base != element_stable_key(make_element(text="Latest posts"))


def test_ancestor_shape_change_changes_the_key(make_element: MakeElement) -> None:
    a = make_element(selector="main > section > div.card")
    b = make_element(selector="aside > section > div.card")
    assert element_stable_key(a) != element_stable_key(b)


def test_parts_cannot_bleed_into_each_other() -> None:
    """Concatenation without a separator would let ('di','va') collide with ('div','a')."""
    assert fp(checker_id="a.b", issue_kind="cd") != fp(checker_id="a.bc", issue_kind="d")


# ------------------------------------------------------------------------ helpers


def test_ancestor_shape_drops_indices_and_the_element_itself() -> None:
    assert (
        ancestor_shape("main > section:nth-of-type(2) > div.card:nth-child(3)") == "main > section"
    )
    assert ancestor_shape("body") == ""
    assert ancestor_shape("html body main h1") == "html body main"


def test_normalise_text_truncates_after_normalising() -> None:
    assert normalise_text("  A   B  ", 60) == "a b"
    assert normalise_text("x" * 100, 60) == "x" * 60
    assert normalise_text(None, 40) == ""
