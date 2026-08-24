"""SPEC §8.1 — the checker protocol and decorator registry."""

from __future__ import annotations

from collections.abc import Iterable, Iterator

import pytest

from engine.artifact.context import Capability, RunContext
from engine.checkers import base
from engine.checkers.base import Checker, applicable, checker, discover, registry
from engine.fixtures import load_fixture
from engine.issues.models import Category, Finding, Severity


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    saved = dict(base._REGISTRY)
    base._REGISTRY.clear()
    yield
    base._REGISTRY.clear()
    base._REGISTRY.update(saved)


class Quiet:
    id = "test.quiet"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        return []


def test_no_checkers_are_registered_yet() -> None:
    """Phase 0 ships the framework, not the catalogue."""
    base._REGISTRY.update(discover())
    assert registry() == {}


def test_the_decorator_registers_by_id() -> None:
    checker(Quiet)
    assert list(registry()) == ["test.quiet"]
    assert isinstance(registry()["test.quiet"], Checker)


def test_the_decorator_returns_the_class_unchanged() -> None:
    assert checker(Quiet) is Quiet


def test_duplicate_ids_are_refused() -> None:
    checker(Quiet)
    with pytest.raises(ValueError, match="duplicate checker id"):
        checker(Quiet)


def test_applicable_filters_on_requires() -> None:
    class NeedsFigma(Quiet):
        id = "test.needs-figma"
        requires = frozenset({Capability.ELEMENTS, Capability.FIGMA})

    checker(Quiet)
    checker(NeedsFigma)

    ctx = load_fixture("tiny")
    assert [c.id for c in applicable(ctx)] == ["test.quiet"]
