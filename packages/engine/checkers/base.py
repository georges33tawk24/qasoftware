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
    # Deterministic unless a checker says otherwise: the default has to be the safe one,
    # or a checker that forgets to declare quietly leaves the determinism set.
    if not hasattr(instance, "deterministic"):
        instance.deterministic = True  # type: ignore[attr-defined]  # optional, see non_deterministic
    _REGISTRY[instance.id] = instance
    return cls


def registry() -> dict[str, Checker]:
    return dict(_REGISTRY)


def non_deterministic() -> set[str]:
    """Checkers whose findings two runs over an unchanged site may legitimately disagree
    on — measurements taken from the world rather than from the artifact.

    Not a Protocol member: making it one would leave two dozen existing checkers
    structurally incomplete for a flag only one of them sets. The guarantee that nobody
    joins this set unnoticed lives in `test_hardening`, which asserts its exact contents.
    """
    return {i for i, c in _REGISTRY.items() if not getattr(c, "deterministic", True)}


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
