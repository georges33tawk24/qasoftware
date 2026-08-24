"""The checker protocol and registry — SPEC §8.1.

A checker is a pure function over a run artifact. It never touches the network or a
browser. If it needs data it hasn't got, the fix is in the capture layer.
"""

from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from engine.artifact.context import Capability, RunContext
from engine.issues.models import Category, Finding, Severity


@runtime_checkable
class Checker(Protocol):
    id: str
    """Dotted and stable, e.g. `layout.alignment`. It is part of every fingerprint, so
    renaming one throws away every dismissal against it."""

    category: Category
    requires: frozenset[Capability]
    """Immutable so it can live as a plain class attribute on every checker."""

    default_severity: Severity

    def run(self, ctx: RunContext) -> Iterable[Finding]: ...


_REGISTRY: dict[str, Checker] = {}


def checker[C: type[Checker]](cls: C) -> C:
    """Register a checker class. Instantiates once — checkers hold no per-run state."""
    instance = cls()
    if instance.id in _REGISTRY:
        raise ValueError(f"duplicate checker id {instance.id!r}")
    _REGISTRY[instance.id] = instance
    return cls


def registry() -> dict[str, Checker]:
    return dict(_REGISTRY)


def discover(package: str = "engine.checkers") -> dict[str, Checker]:
    """Import every checker module so its decorators run."""
    pkg = importlib.import_module(package)
    for module in pkgutil.walk_packages(pkg.__path__, prefix=f"{package}."):
        importlib.import_module(module.name)
    return registry()


def applicable(ctx: RunContext) -> list[Checker]:
    """The registered checkers whose `requires` this artifact can satisfy.

    Everything else self-skips silently — SPEC §1.4 means there is no toggle, so a
    missing capability is the only legitimate reason a checker does not run.
    """
    available = ctx.capabilities()
    return [c for c in _REGISTRY.values() if c.requires <= available]
