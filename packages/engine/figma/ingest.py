"""Figma ingestion — SPEC §6, stage 2 of §3.

Produces data, never issues: the raw file, the derived tokens, and the frame exports the
side-by-side evidence needs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from engine.artifact.context import RunContext
from engine.artifact.store import RunPaths, write_bytes
from engine.figma import frames as frame_mapping
from engine.figma import normalise, tokens
from engine.figma.client import FigmaClient, FigmaError
from engine.figma.models import FigmaDocument, Tokens


@dataclass
class IngestResult:
    document: FigmaDocument
    tokens: Tokens
    frameMap: dict[str, str] = field(default_factory=dict)
    proposals: list[frame_mapping.Proposal] = field(default_factory=list)
    images: int = 0
    notes: list[str] = field(default_factory=list)


def read_raw(paths: RunPaths) -> dict[str, object] | None:
    path = paths.figma / "file.json"
    if not path.is_file():
        return None
    loaded: dict[str, object] = json.loads(path.read_text())
    return loaded


def ingest(
    paths: RunPaths,
    ctx: RunContext,
    *,
    file_key: str | None = None,
    client: FigmaClient | None = None,
    confirmed: dict[str, str] | None = None,
    accept_suggested: bool = False,
) -> IngestResult:
    raw = None
    if client is not None and file_key:
        raw = client.cached_file(file_key)
        write_bytes(paths.figma / "file.json", json.dumps(raw, indent=2).encode() + b"\n")
    else:
        raw = read_raw(paths)
    if raw is None:
        raise FigmaError("no Figma file: pass a file key and a token, or place figma/file.json")

    document = normalise.normalise(raw, file_key or str(raw.get("name", "unknown")))
    derived = tokens.extract(document)
    write_bytes(paths.figma / "tokens.json", derived.model_dump_json(indent=2).encode() + b"\n")

    proposals = frame_mapping.propose(document, ctx)
    mapping = frame_mapping.resolve(
        document, ctx, confirmed=confirmed or {}, accept_suggested=accept_suggested
    )

    result = IngestResult(document=document, tokens=derived, frameMap=mapping, proposals=proposals)
    if not mapping:
        result.notes.append(
            "no frame is mapped to a route yet, so no design comparison ran. "
            "Confirm a mapping (bureau figma --frame '<frame>=<path>') and re-run."
        )
    if client is not None and file_key and mapping:
        result.images = _export_frames(paths, client, file_key, list(mapping), result.notes)
    return result


def _export_frames(
    paths: RunPaths, client: FigmaClient, file_key: str, ids: list[str], notes: list[str]
) -> int:
    """2x exports for the side-by-side evidence in SPEC §12.2."""
    try:
        urls = client.images(file_key, ids, scale=2)
    except FigmaError as exc:
        notes.append(f"frame exports unavailable ({exc}); evidence will be live-only")
        return 0

    written = 0
    for node_id, url in urls.items():
        try:
            payload = client.download(url)
        except FigmaError:
            continue
        write_bytes(frame_png(paths, node_id), payload)
        written += 1
    return written


def frame_png(paths: RunPaths, node_id: str) -> Path:
    """Node ids contain a colon, which is legal on POSIX and not on Windows."""
    return paths.figma / "frames" / f"{node_id.replace(':', '-')}.png"
