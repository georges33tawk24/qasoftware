"""Deterministic checkers — SPEC §8.4. One package per catalogue group."""

from engine.checkers.base import Checker, applicable, checker, discover, registry

__all__ = ["Checker", "applicable", "checker", "discover", "registry"]
