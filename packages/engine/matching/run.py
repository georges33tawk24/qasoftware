"""Stage 3 of SPEC §3: node ↔ element mapping for every mapped surface.

Produces data, never issues. The deltas are group J's job; this decides only what
corresponds to what, and writes down why.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from engine.artifact.context import RunContext
from engine.artifact.store import RunPaths, write_bytes
from engine.figma.models import FigmaDocument
from engine.matching.engine import THRESHOLD, Pins, match_surface
from engine.matching.models import MappingFile


@dataclass
class MatchRun:
    mappings: list[MappingFile] = field(default_factory=list)

    @property
    def confident(self) -> list[MappingFile]:
        return [m for m in self.mappings if m.confident]


def run(
    paths: RunPaths,
    ctx: RunContext,
    document: FigmaDocument,
    frame_map: dict[str, str],
    *,
    pins: dict[str, str] | None = None,
    threshold: float = THRESHOLD,
) -> MatchRun:
    result = MatchRun()
    pinned = Pins(by_layer=dict(pins or {}))
    pages = {page.id: page for page in ctx.pages()}

    for frame_id, page_id in frame_map.items():
        frame = document.frame(frame_id)
        page = pages.get(page_id)
        if frame is None or page is None or page.crawlBlocked:
            continue
        for viewport in ctx.viewports:
            if viewport.name not in ctx.viewport_names(page.id):
                continue
            mapping = match_surface(
                document,
                frame,
                ctx.elements(page.id, viewport.name),
                viewport,
                page_id=page.id,
                pins=pinned,
                threshold=threshold,
            )
            write_bytes(
                paths.mapping_file(page.id, viewport.name),
                mapping.model_dump_json(indent=2).encode() + b"\n",
            )
            result.mappings.append(mapping)

    _write_index(paths, result)
    return result


def _write_index(paths: RunPaths, result: MatchRun) -> None:
    """`design/matching.json` — the file you live in when a false positive needs
    explaining. The per-signal scores are in the per-surface files it points at."""
    index = {
        "threshold": THRESHOLD,
        "surfaces": [
            {
                "pageId": m.pageId,
                "viewport": m.viewport,
                "frame": m.frameName,
                "scale": m.scale,
                "anchors": m.anchors,
                "matched": m.matched,
                "unmatchedNodes": m.unmatchedNodes,
                "unmatchedElements": m.unmatchedElements,
                "confident": m.confident,
                "detail": f"mapping/{m.pageId}.{m.viewport}.json",
            }
            for m in result.mappings
        ],
    }
    write_bytes(
        paths.root / "design" / "matching.json",
        json.dumps(index, indent=2).encode() + b"\n",
    )
