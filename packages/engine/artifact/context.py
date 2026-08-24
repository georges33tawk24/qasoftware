"""`RunContext` — the read-only view of a run artifact that every checker receives.

A checker gets one of these and nothing else. No network, no browser, no clock.
"""

from __future__ import annotations

import json
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING, Any

from engine.artifact import store
from engine.artifact.models import (
    ConsoleMessage,
    ElementRecord,
    LayoutRecord,
    Metrics,
    NetworkEntry,
    PageRecord,
    ProbeReport,
    RunManifest,
    Viewport,
)

if TYPE_CHECKING:
    from engine.artifact.models import ApiReport, FlowRecord
    from engine.figma.models import FigmaDocument, Tokens
    from engine.matching.models import MappingFile


class Capability(StrEnum):
    """What an artifact contains. A checker declares the subset it needs in `requires`
    and self-skips cleanly when the data is absent."""

    ELEMENTS = "elements"
    LAYOUT = "layout"
    DOM = "dom"
    SCREENSHOT = "screenshot"
    CONSOLE = "console"
    NETWORK = "network"
    VITALS = "vitals"
    AXE = "axe"
    A11Y_TREE = "a11y_tree"
    COVERAGE = "coverage"
    FIGMA = "figma"
    MAPPING = "mapping"
    AUTH = "auth"
    FLOWS = "flows"
    API_PROBES = "api_probes"
    PROBES = "probes"
    VISUAL = "visual"


class RunContext:
    """Lazily loads artifact files and caches them for the life of the run."""

    def __init__(self, root: Path | str, manifest: RunManifest) -> None:
        self.root = Path(root)
        self.manifest = manifest
        self.paths = store.RunPaths(self.root)
        self._cache: dict[tuple[str, ...], Any] = {}

    @classmethod
    def open(cls, root: Path | str) -> RunContext:
        return cls(root, store.read_manifest(root))

    def _cached(self, key: tuple[str, ...], load: Any) -> Any:
        if key not in self._cache:
            self._cache[key] = load()
        return self._cache[key]

    # ------------------------------------------------------------------ structure

    @property
    def run_id(self) -> str:
        return self.manifest.runId

    @property
    def viewports(self) -> list[Viewport]:
        return self.manifest.config.viewports

    def page_ids(self) -> list[str]:
        return self.paths.page_ids()

    def pages(self) -> list[PageRecord]:
        loaded: list[PageRecord] = self._cached(
            ("pages",), lambda: [store.read_page(self.root, pid) for pid in self.page_ids()]
        )
        return loaded

    def page(self, page_id: str) -> PageRecord:
        return next(p for p in self.pages() if p.id == page_id)

    def viewport_names(self, page_id: str) -> list[str]:
        return self.paths.viewport_names(page_id)

    # ----------------------------------------------------------------- page data

    def elements(self, page_id: str, viewport: str) -> list[ElementRecord]:
        loaded: list[ElementRecord] = self._cached(
            ("elements", page_id, viewport),
            lambda: store.read_elements(self.root, page_id, viewport),
        )
        return loaded

    def element_index(self, page_id: str, viewport: str) -> dict[str, ElementRecord]:
        loaded: dict[str, ElementRecord] = self._cached(
            ("element_index", page_id, viewport),
            lambda: {e.id: e for e in self.elements(page_id, viewport)},
        )
        return loaded

    def layout(self, page_id: str, viewport: str) -> LayoutRecord:
        loaded: LayoutRecord = self._cached(
            ("layout", page_id, viewport),
            lambda: store.read_layout(self.root, page_id, viewport),
        )
        return loaded

    def console(self, page_id: str) -> list[ConsoleMessage]:
        loaded: list[ConsoleMessage] = self._cached(
            ("console", page_id), lambda: store.read_console(self.root, page_id)
        )
        return loaded

    def network(self, page_id: str) -> list[NetworkEntry]:
        loaded: list[NetworkEntry] = self._cached(
            ("network", page_id), lambda: store.read_network(self.root, page_id)
        )
        return loaded

    def vitals(self, page_id: str) -> Metrics | None:
        loaded: Metrics | None = self._cached(
            ("vitals", page_id), lambda: store.read_vitals(self.root, page_id)
        )
        return loaded

    def axe(self, page_id: str) -> dict[str, Any] | None:
        loaded: dict[str, Any] | None = self._cached(
            ("axe", page_id), lambda: store.read_json(self.paths.axe(page_id))
        )
        return loaded

    def a11y(self, page_id: str) -> dict[str, Any] | None:
        loaded: dict[str, Any] | None = self._cached(
            ("a11y", page_id), lambda: store.read_json(self.paths.a11y(page_id))
        )
        return loaded

    def coverage(self, page_id: str) -> dict[str, Any] | None:
        loaded: dict[str, Any] | None = self._cached(
            ("coverage", page_id), lambda: store.read_json(self.paths.coverage(page_id))
        )
        return loaded

    def figma(self) -> FigmaDocument | None:
        """The normalised design, derived on first use.

        SPEC §4 stores the raw REST response; normalising is a pure function over it, so
        there is no reason to keep a second copy on disk.
        """
        loaded: FigmaDocument | None = self._cached(("figma",), self._load_figma)
        return loaded

    def _load_figma(self) -> FigmaDocument | None:
        from engine.figma.normalise import normalise as normalise_figma

        path = self.paths.figma / "file.json"
        if not path.is_file():
            return None
        raw = json.loads(path.read_text())
        return normalise_figma(raw, self.manifest.config.figmaFileKey or "local")

    def tokens(self) -> Tokens | None:
        from engine.figma.models import Tokens as TokensModel

        path = self.paths.figma_tokens
        loaded: Tokens | None = self._cached(
            ("figma-tokens",),
            lambda: TokensModel.model_validate_json(path.read_bytes()) if path.is_file() else None,
        )
        return loaded

    def mapping(self, page_id: str, viewport: str) -> MappingFile | None:
        from engine.matching.models import MappingFile as MappingModel

        path = self.paths.mapping_file(page_id, viewport)
        loaded: MappingFile | None = self._cached(
            ("mapping", page_id, viewport),
            lambda: MappingModel.model_validate_json(path.read_bytes()) if path.is_file() else None,
        )
        return loaded

    def flows(self) -> list[FlowRecord]:
        loaded: list[FlowRecord] = self._cached(("flows",), self._load_flows)
        return loaded

    def _load_flows(self) -> list[FlowRecord]:
        from engine.artifact.models import FlowRecord as Record

        return [
            Record.model_validate_json(self.paths.flow_steps(flow_id).read_bytes())
            for flow_id in self.paths.flow_ids()
        ]

    def api(self) -> ApiReport | None:
        from engine.artifact.models import ApiReport as Report

        path = self.paths.api_probes
        loaded: ApiReport | None = self._cached(
            ("api",),
            lambda: Report.model_validate_json(path.read_bytes()) if path.is_file() else None,
        )
        return loaded

    def probes(self) -> ProbeReport | None:
        """Link, not-found and well-known-path results, resolved at capture time."""
        loaded: ProbeReport | None = self._cached(("probes",), lambda: store.read_probes(self.root))
        return loaded

    def dom(self, page_id: str) -> str | None:
        path = self.paths.dom(page_id)
        loaded: str | None = self._cached(
            ("dom", page_id), lambda: path.read_text() if path.is_file() else None
        )
        return loaded

    # --------------------------------------------------------------- capabilities

    def capabilities(self) -> set[Capability]:
        """What this artifact actually has, derived from what is on disk."""
        caps: set[Capability] = self._cached(("capabilities",), self._derive_capabilities)
        return caps

    def _derive_capabilities(self) -> set[Capability]:
        caps: set[Capability] = set()
        p = self.paths
        for pid in self.page_ids():
            for vp in p.viewport_names(pid):
                caps.add(Capability.ELEMENTS)
                if p.layout(pid, vp).is_file():
                    caps.add(Capability.LAYOUT)
                if p.full_png(pid, vp).is_file():
                    caps.add(Capability.SCREENSHOT)
            for cap, path in (
                (Capability.DOM, p.dom(pid)),
                (Capability.CONSOLE, p.console(pid)),
                (Capability.NETWORK, p.network(pid)),
                (Capability.VITALS, p.vitals(pid)),
                (Capability.AXE, p.axe(pid)),
                (Capability.A11Y_TREE, p.a11y(pid)),
                (Capability.COVERAGE, p.coverage(pid)),
            ):
                if path.is_file():
                    caps.add(cap)
        if p.probes.is_file():
            caps.add(Capability.PROBES)
        if p.visual.is_file():
            caps.add(Capability.VISUAL)
        if (p.figma / "file.json").is_file():
            caps.add(Capability.FIGMA)
        if p.mapping.is_dir() and any(p.mapping.glob("*.json")):
            caps.add(Capability.MAPPING)
        if p.flow_ids():
            caps.add(Capability.FLOWS)
        if (p.api / "probes.json").is_file():
            caps.add(Capability.API_PROBES)
        if [persona for persona in self.manifest.config.personas if persona != "anonymous"]:
            caps.add(Capability.AUTH)
        return caps

    def has(self, *required: Capability) -> bool:
        return set(required) <= self.capabilities()
