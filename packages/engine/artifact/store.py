"""Read and write the run artifact directory laid out in SPEC §4.

This module is the only place that knows the directory layout. Everything else asks
`RunPaths` where a thing lives.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError

from engine.artifact.models import (
    ConsoleMessage,
    ElementRecord,
    LayoutRecord,
    Metrics,
    NetworkEntry,
    PageArtifact,
    PageRecord,
    ProbeReport,
    RunArtifact,
    RunManifest,
)

_ELEMENTS = TypeAdapter(list[ElementRecord])
_CONSOLE = TypeAdapter(list[ConsoleMessage])
_NETWORK = TypeAdapter(list[NetworkEntry])


class RunPaths:
    """Every path in SPEC §4, in one place."""

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    # run level
    @property
    def run(self) -> Path:
        return self.root / "run.json"

    @property
    def pages(self) -> Path:
        return self.root / "pages"

    @property
    def issues(self) -> Path:
        return self.root / "issues.json"

    @property
    def probes(self) -> Path:
        return self.root / "probes.json"

    @property
    def knowledge(self) -> Path:
        return self.root / "knowledge.json"

    @property
    def diff(self) -> Path:
        return self.root / "diff.json"

    @property
    def dismissed(self) -> Path:
        return self.root / "dismissed.json"

    @property
    def visual(self) -> Path:
        return self.root / "visual.json"

    @property
    def figma(self) -> Path:
        return self.root / "figma"

    @property
    def mapping(self) -> Path:
        return self.root / "mapping"

    def mapping_file(self, page_id: str, viewport: str) -> Path:
        return self.mapping / f"{page_id}.{viewport}.json"

    @property
    def figma_tokens(self) -> Path:
        return self.figma / "tokens.json"

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def flows(self) -> Path:
        return self.root / "flows"

    @property
    def api(self) -> Path:
        return self.root / "api"

    def flow_dir(self, flow_id: str) -> Path:
        return self.flows / flow_id

    def flow_steps(self, flow_id: str) -> Path:
        return self.flow_dir(flow_id) / "steps.json"

    @property
    def api_endpoints(self) -> Path:
        return self.api / "endpoints.json"

    @property
    def api_probes(self) -> Path:
        return self.api / "probes.json"

    def flow_ids(self) -> list[str]:
        if not self.flows.is_dir():
            return []
        return sorted(p.name for p in self.flows.iterdir() if (p / "steps.json").is_file())

    @property
    def report(self) -> Path:
        return self.root / "report.html"

    def media(self, issue_id: str) -> Path:
        return self.root / "media" / issue_id

    # page level
    def page_dir(self, page_id: str) -> Path:
        return self.pages / page_id

    def page(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "page.json"

    def dom(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "dom.html"

    def console(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "console.json"

    def network(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "network.json"

    def a11y(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "a11y.json"

    def axe(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "axe.json"

    def coverage(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "coverage.json"

    def vitals(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "vitals.json"

    # viewport level
    def viewports_dir(self, page_id: str) -> Path:
        return self.page_dir(page_id) / "viewports"

    def viewport_dir(self, page_id: str, viewport: str) -> Path:
        return self.viewports_dir(page_id) / viewport

    def elements(self, page_id: str, viewport: str) -> Path:
        return self.viewport_dir(page_id, viewport) / "elements.json"

    def layout(self, page_id: str, viewport: str) -> Path:
        return self.viewport_dir(page_id, viewport) / "layout.json"

    def full_png(self, page_id: str, viewport: str) -> Path:
        return self.viewport_dir(page_id, viewport) / "full.png"

    def fold_png(self, page_id: str, viewport: str) -> Path:
        return self.viewport_dir(page_id, viewport) / "fold.png"

    # discovery
    def page_ids(self) -> list[str]:
        if not self.pages.is_dir():
            return []
        return sorted(p.name for p in self.pages.iterdir() if (p / "page.json").is_file())

    def viewport_names(self, page_id: str) -> list[str]:
        d = self.viewports_dir(page_id)
        if not d.is_dir():
            return []
        return sorted(p.name for p in d.iterdir() if (p / "elements.json").is_file())


# ------------------------------------------------------------------------------ write


def write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(payload)


def _write_json(path: Path, value: Any) -> None:
    write_bytes(path, json.dumps(value, indent=2, ensure_ascii=False).encode() + b"\n")


def write_run_manifest(paths: RunPaths, manifest: RunManifest) -> None:
    write_bytes(paths.run, manifest.model_dump_json(indent=2).encode() + b"\n")


def write_probes(paths: RunPaths, probes: ProbeReport) -> None:
    write_bytes(paths.probes, probes.model_dump_json(indent=2).encode() + b"\n")


def write_run(root: Path | str, run: RunArtifact) -> RunPaths:
    """Write a whole run to disk. Overwrites what is there."""
    p = RunPaths(root)
    write_run_manifest(p, run.manifest)
    for page in run.pages:
        write_page(p, page)
    if run.probes is not None:
        write_probes(p, run.probes)
    return p


def write_page(paths: RunPaths, page: PageArtifact) -> None:
    pid = page.page.id
    write_bytes(paths.page(pid), page.page.model_dump_json(indent=2).encode() + b"\n")
    if page.dom is not None:
        write_bytes(paths.dom(pid), page.dom.encode())
    write_bytes(paths.console(pid), _CONSOLE.dump_json(page.console, indent=2) + b"\n")
    write_bytes(paths.network(pid), _NETWORK.dump_json(page.network, indent=2) + b"\n")
    if page.vitals is not None:
        write_bytes(paths.vitals(pid), page.vitals.model_dump_json(indent=2).encode() + b"\n")
    if page.axe is not None:
        _write_json(paths.axe(pid), page.axe)
    if page.a11y is not None:
        _write_json(paths.a11y(pid), page.a11y)
    if page.coverage is not None:
        _write_json(paths.coverage(pid), page.coverage)
    for viewport, elements in page.elements.items():
        write_bytes(paths.elements(pid, viewport), _ELEMENTS.dump_json(elements, indent=2) + b"\n")
    for viewport, layout in page.layout.items():
        write_bytes(paths.layout(pid, viewport), layout.model_dump_json(indent=2).encode() + b"\n")


# ------------------------------------------------------------------------------- read


def read_manifest(root: Path | str) -> RunManifest:
    return RunManifest.model_validate_json(RunPaths(root).run.read_bytes())


def read_page(root: Path | str, page_id: str) -> PageRecord:
    return PageRecord.model_validate_json(RunPaths(root).page(page_id).read_bytes())


def read_elements(root: Path | str, page_id: str, viewport: str) -> list[ElementRecord]:
    return _ELEMENTS.validate_json(RunPaths(root).elements(page_id, viewport).read_bytes())


def read_layout(root: Path | str, page_id: str, viewport: str) -> LayoutRecord:
    return LayoutRecord.model_validate_json(RunPaths(root).layout(page_id, viewport).read_bytes())


def read_console(root: Path | str, page_id: str) -> list[ConsoleMessage]:
    path = RunPaths(root).console(page_id)
    return _CONSOLE.validate_json(path.read_bytes()) if path.is_file() else []


def read_network(root: Path | str, page_id: str) -> list[NetworkEntry]:
    path = RunPaths(root).network(page_id)
    return _NETWORK.validate_json(path.read_bytes()) if path.is_file() else []


def read_vitals(root: Path | str, page_id: str) -> Metrics | None:
    path = RunPaths(root).vitals(page_id)
    return Metrics.model_validate_json(path.read_bytes()) if path.is_file() else None


def read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    loaded: Any = json.loads(path.read_text())
    if not isinstance(loaded, dict):
        raise ValueError(f"{path} is not a JSON object")
    return loaded


def read_page_artifact(root: Path | str, page_id: str) -> PageArtifact:
    p = RunPaths(root)
    return PageArtifact(
        page=read_page(root, page_id),
        dom=p.dom(page_id).read_text() if p.dom(page_id).is_file() else None,
        console=read_console(root, page_id),
        network=read_network(root, page_id),
        vitals=read_vitals(root, page_id),
        axe=read_json(p.axe(page_id)),
        a11y=read_json(p.a11y(page_id)),
        coverage=read_json(p.coverage(page_id)),
        elements={vp: read_elements(root, page_id, vp) for vp in p.viewport_names(page_id)},
        layout={
            vp: read_layout(root, page_id, vp)
            for vp in p.viewport_names(page_id)
            if p.layout(page_id, vp).is_file()
        },
    )


def read_probes(root: Path | str) -> ProbeReport | None:
    path = RunPaths(root).probes
    return ProbeReport.model_validate_json(path.read_bytes()) if path.is_file() else None


def read_run(root: Path | str) -> RunArtifact:
    p = RunPaths(root)
    return RunArtifact(
        manifest=read_manifest(root),
        pages=[read_page_artifact(root, pid) for pid in p.page_ids()],
        probes=read_probes(root),
    )


# --------------------------------------------------------------------------- validate


def validate(root: Path | str) -> list[str]:
    """Return every problem found in an artifact directory. Empty list means valid."""
    p = RunPaths(root)
    problems: list[str] = []

    if not p.run.is_file():
        return [f"missing {p.run}"]
    try:
        manifest = read_manifest(root)
    except ValidationError as exc:
        return [f"run.json: {exc}"]

    on_disk = set(p.page_ids())
    declared = set(manifest.pageIds)
    for pid in sorted(declared - on_disk):
        problems.append(f"run.json declares page {pid!r} but pages/{pid}/page.json is missing")
    for pid in sorted(on_disk - declared):
        problems.append(f"pages/{pid}/ exists but is not listed in run.json pageIds")

    expected_viewports = {v.name for v in manifest.config.viewports}

    for pid in sorted(on_disk):
        try:
            page = read_page(root, pid)
        except ValidationError as exc:
            problems.append(f"pages/{pid}/page.json: {exc}")
            continue
        if page.crawlBlocked:
            continue  # challenged pages capture nothing, by design

        viewports = p.viewport_names(pid)
        if not viewports:
            problems.append(f"pages/{pid}/ has no captured viewport")
        for missing in sorted(expected_viewports - set(viewports)):
            problems.append(f"pages/{pid}/ is missing viewport {missing!r} from run config")

        for vp in viewports:
            try:
                elements = read_elements(root, pid, vp)
            except ValidationError as exc:
                problems.append(f"pages/{pid}/viewports/{vp}/elements.json: {exc}")
                continue
            problems.extend(_check_element_graph(pid, vp, elements))

            layout_path = p.layout(pid, vp)
            if not layout_path.is_file():
                problems.append(f"pages/{pid}/viewports/{vp}/layout.json is missing")
                continue
            try:
                layout = read_layout(root, pid, vp)
            except ValidationError as exc:
                problems.append(f"pages/{pid}/viewports/{vp}/layout.json: {exc}")
                continue
            problems.extend(_check_layout_refs(pid, vp, layout, {e.id for e in elements}))

    return problems


def _check_element_graph(pid: str, vp: str, elements: list[ElementRecord]) -> list[str]:
    where = f"pages/{pid}/viewports/{vp}/elements.json"
    problems: list[str] = []
    ids = [e.id for e in elements]
    known = set(ids)
    if len(known) != len(ids):
        problems.append(f"{where}: duplicate element ids")
    for el in elements:
        if el.parentId is not None and el.parentId not in known:
            problems.append(f"{where}: {el.id} has unknown parentId {el.parentId!r}")
        for child in el.childIds:
            if child not in known:
                problems.append(f"{where}: {el.id} has unknown childId {child!r}")
    return problems


def _check_layout_refs(pid: str, vp: str, layout: LayoutRecord, known: set[str]) -> list[str]:
    where = f"pages/{pid}/viewports/{vp}/layout.json"
    problems: list[str] = []
    if layout.pageId != pid:
        problems.append(f"{where}: pageId is {layout.pageId!r}, expected {pid!r}")
    if layout.viewport != vp:
        problems.append(f"{where}: viewport is {layout.viewport!r}, expected {vp!r}")
    for group in layout.alignmentSets:
        for eid in group.elementIds:
            if eid not in known:
                problems.append(f"{where}: alignment set references unknown element {eid!r}")
    for repeated in layout.repeatedGroups:
        for eid in repeated.elementIds:
            if eid not in known:
                problems.append(f"{where}: repeated group references unknown element {eid!r}")
    return problems
