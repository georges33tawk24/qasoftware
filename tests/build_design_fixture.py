"""One-shot builder for `fixtures/design`.

Captures the clean fixture site, generates a synthetic Figma file from that capture, then
walks the design away from the page in the specific ways listed in
`tests/fixtures/figma/DELTAS.md`.

Run with: .venv/bin/python -m tests.build_design_fixture
"""

from __future__ import annotations

import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from engine.artifact.context import RunContext
from engine.artifact.models import VIEWPORT_PRESETS, RunConfig
from engine.artifact.store import RunPaths, write_bytes
from engine.capture.run import capture
from engine.figma.ingest import frame_png, ingest
from engine.matching.run import run as run_matching
from tests import figma_fixture as ff
from tests.serve_broken import serve

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "fixtures" / "design"
OTHER = ROOT / "tests" / "fixtures" / "figma" / "other.json"


def plant_deltas(raw: dict[str, Any]) -> dict[str, Any]:
    frame = raw["document"]["children"][0]["children"][0]

    heading = ff.find(frame, ff.by_text("Latest news"))
    assert heading is not None
    heading["style"]["fontSize"] = 32.0  # DELTA: font size

    intro = ff.find(frame, lambda n: (n.get("characters") or "").startswith("Three cards"))
    assert intro is not None
    intro["style"]["lineHeightPx"] = 28.0  # DELTA: line height

    cards = ff.find_all(frame, lambda n: n.get("name") == "Card")
    assert len(cards) >= 3, [n["name"] for n in ff.find_all(frame, lambda n: True)]
    cards[1]["absoluteBoundingBox"]["width"] += 12  # DELTA: size
    ff.shift(cards[2], 6, 0)  # DELTA: position
    cards[0]["effects"] = [  # DELTA: shadow
        {
            "type": "DROP_SHADOW",
            "visible": True,
            "color": {"r": 0, "g": 0, "b": 0, "a": 0.25},
            "offset": {"x": 0, "y": 8},
            "radius": 16,
            "spread": 0,
        }
    ]

    button = ff.find(frame, ff.by_name("Button/Read more"))
    assert button is not None, "the call to action should be a frame with a label"
    button["fills"] = ff.solid("rgb(28, 100, 200)")  # DELTA: background colour
    button["cornerRadius"] = 12.0  # DELTA: corner radius
    # The call to action keeps its copy: its deltas are the fill and the radius, and a
    # large copy change would (correctly) make it unmatchable rather than different.
    assert ff.find(button, ff.by_text("Read more")) is not None

    second = ff.find(frame, ff.by_text("Body copy for the second card."))
    assert second is not None
    second["characters"] = "Body copy for the 2nd card."  # DELTA: text content

    footer = ff.find(frame, ff.by_text("Fixture footer"))
    assert footer is not None
    footer["opacity"] = 0.6  # DELTA: opacity

    private = ff.find(frame, ff.by_text("Private area"))
    assert private is not None
    _remove(frame, private["id"])  # DELTA: on the page, absent from the design

    frame["children"].append(  # DELTA: in the design, missing from the page
        {
            "id": "1:9001",
            "name": "Heading/Newsletter",
            "type": "TEXT",
            "visible": True,
            "opacity": 1.0,
            "characters": "Sign up to the newsletter",
            "absoluteBoundingBox": {
                "x": ff.FRAME_ORIGIN[0] + 24,
                "y": ff.FRAME_ORIGIN[1] + 640,
                "width": 420,
                "height": 28,
            },
            "fills": ff.solid("rgb(17, 17, 17)"),
            "style": {
                "fontFamily": "Helvetica",
                "fontWeight": 600,
                "fontSize": 20.0,
                "lineHeightPx": 28.0,
                "letterSpacing": 0.0,
                "textAlignHorizontal": "LEFT",
            },
        }
    )
    return raw


def _remove(node: dict[str, Any], node_id: str) -> None:
    children = node.get("children")
    if not children:
        return
    node["children"] = [c for c in children if c.get("id") != node_id]
    for child in node["children"]:
        _remove(child, node_id)


def other_site_file() -> dict[str, Any]:
    """A design for a completely different product, to prove the failure mode: the tool
    must say "could not match", not invent four hundred deltas."""

    def text(
        node_id: str, name: str, characters: str, x: float, y: float, size: float
    ) -> dict[str, Any]:
        return {
            "id": node_id,
            "name": name,
            "type": "TEXT",
            "visible": True,
            "opacity": 1.0,
            "characters": characters,
            "absoluteBoundingBox": {"x": x, "y": y, "width": 400, "height": size * 1.4},
            "fills": ff.solid("rgb(20, 20, 20)"),
            "style": {
                "fontFamily": "Inter",
                "fontWeight": 600,
                "fontSize": size,
                "lineHeightPx": size * 1.4,
                "letterSpacing": 0.0,
                "textAlignHorizontal": "LEFT",
            },
        }

    frame = {
        "id": "2:1",
        "name": "Pricing",
        "type": "FRAME",
        "visible": True,
        "opacity": 1.0,
        "absoluteBoundingBox": {"x": 0, "y": 0, "width": 1440, "height": 1200},
        "fills": ff.solid("rgb(255, 255, 255)"),
        "children": [
            text("2:10", "Heading/Plans", "Choose the plan that fits your team", 80, 120, 44),
            text("2:11", "Body/Starter", "Starter — five seats, one project", 80, 260, 18),
            text("2:12", "Body/Growth", "Growth — twenty seats, unlimited projects", 80, 320, 18),
            text("2:13", "Button/Trial", "Start a 14 day trial", 80, 420, 16),
            text("2:14", "Body/Invoicing", "Annual invoicing available on request", 80, 500, 14),
        ],
    }
    return {
        "name": "Other product",
        "version": "1",
        "lastModified": "2026-01-15T09:00:00Z",
        "document": {
            "id": "0:0",
            "name": "Document",
            "type": "DOCUMENT",
            "children": [{"id": "0:1", "name": "Page 1", "type": "CANVAS", "children": [frame]}],
        },
    }


async def build() -> None:
    server, base = serve(ROOT / "tests" / "fixtures" / "site")
    try:
        config = RunConfig(
            viewports=[VIEWPORT_PRESETS["desktop_1440"]],
            maxPages=1,
            maxDepth=0,
            settleMs=150,
            include=[r"/index\.html"],
        )
        result = await capture(base + "index.html", OUT.parent / "_design_tmp", config=config)
    finally:
        server.shutdown()
        server.server_close()

    paths = RunPaths(result.paths.root)
    ctx = RunContext.open(paths.root)
    page = ctx.pages()[0]
    elements = ctx.elements(page.id, "desktop_1440")

    raw = plant_deltas(ff.build_file(elements, frame_name="Home / Desktop", frame_width=1440.0))
    write_bytes(paths.figma / "file.json", json.dumps(raw, indent=2).encode() + b"\n")
    preview = frame_png(paths, "1:1")
    preview.parent.mkdir(parents=True, exist_ok=True)
    ff.render_frame(raw["document"]["children"][0]["children"][0], preview)
    result_ingest = ingest(paths, ctx, confirmed={"Home / Desktop": page.path})
    run_matching(paths, ctx, result_ingest.document, result_ingest.frameMap)

    if OUT.exists():
        shutil.rmtree(OUT)
    shutil.copytree(paths.root, OUT)
    shutil.rmtree(OUT.parent / "_design_tmp", ignore_errors=True)

    OTHER.parent.mkdir(parents=True, exist_ok=True)
    OTHER.write_text(json.dumps(other_site_file(), indent=2) + "\n")
    print(f"wrote {OUT} and {OTHER}")


if __name__ == "__main__":
    asyncio.run(build())
