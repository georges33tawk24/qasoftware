"""The grounding contract — SPEC §9.3.

Mandatory, and enforced here rather than hoped for in a prompt: every agent call carries
the screenshot, the measured facts for that surface, any project knowledge that applies,
and the two hard instructions. A `Grounding` that is missing any of them will not
construct, so there is no code path that reaches a model with a picture and no numbers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine.artifact.context import RunContext
from engine.artifact.models import ElementRecord
from engine.checkers.support import Surface
from engine.matching.signals import normalise_text

LOCALISE = (
    "Report only what you can point at with a bounding box in the screenshot. "
    "If you cannot localise it, do not report it."
)

NOTHING_MEASURABLE = (
    "Do not report anything measurable. Spacing, colour, size, alignment and contrast "
    "are already checked by exact arithmetic elsewhere in this run, and a model guessing "
    "at a pixel value is how this product loses its credibility. Report the things "
    "arithmetic cannot see."
)

CONTRACT = f"{LOCALISE}\n\n{NOTHING_MEASURABLE}"

MAX_TEXT_SAMPLES = 40
MAX_INVENTORY = 24


class GroundingError(ValueError):
    pass


@dataclass(frozen=True)
class Grounding:
    """What every agent call gets. All four parts, every time."""

    screenshot: bytes
    facts: dict[str, Any]
    knowledge: list[str] = field(default_factory=list)
    contract: str = CONTRACT

    def __post_init__(self) -> None:
        if not self.screenshot:
            raise GroundingError("a grounded call needs the screenshot (SPEC §9.3)")
        if not self.facts:
            raise GroundingError("a grounded call needs the measured facts (SPEC §9.3)")
        if LOCALISE not in self.contract or NOTHING_MEASURABLE not in self.contract:
            raise GroundingError("the grounding contract may not be edited out of a call")

    def as_prompt(self) -> str:
        import json

        blocks = [
            "## Measured facts for this page",
            "These are exact. Do not restate them and do not contradict them.",
            "```json",
            json.dumps(self.facts, indent=1, sort_keys=True, default=str),
            "```",
        ]
        if self.knowledge:
            blocks += [
                "",
                "## What the team has told us about this project",
                "Treat these as true. A difference that one of these explains is not a defect.",
                *(f"- {entry}" for entry in self.knowledge),
            ]
        blocks += ["", "## Rules", self.contract]
        return "\n".join(blocks)


# ------------------------------------------------------------------ fact blocks


def _text_elements(surface: Surface) -> list[ElementRecord]:
    return [e for e in surface.laid_out if e.text.strip()]


def type_inventory(surface: Surface) -> Any:
    return [
        {
            "family": style.fontFamily,
            "size": style.fontSize,
            "weight": style.fontWeight,
            "lineHeight": style.lineHeight,
            "uses": style.count,
        }
        for style in surface.layout.typeInventory[:MAX_INVENTORY]
    ]


def colour_inventory(surface: Surface) -> Any:
    return [
        {"colour": usage.colour, "property": usage.property, "uses": usage.count}
        for usage in surface.layout.colourInventory[:MAX_INVENTORY]
    ]


def spacing_histogram(surface: Surface) -> Any:
    return [
        {"gap": bucket.gap, "uses": bucket.count}
        for bucket in surface.layout.spacingHistogram[:MAX_INVENTORY]
    ]


def alignment_sets(surface: Surface) -> Any:
    return [
        {"axis": group.axis, "edge": group.median, "members": len(group.elementIds)}
        for group in surface.layout.alignmentSets[:MAX_INVENTORY]
    ]


def repeated_groups(surface: Surface) -> Any:
    return [
        {"signature": group.signature, "count": group.count}
        for group in surface.layout.repeatedGroups[:MAX_INVENTORY]
    ]


def text_inventory(surface: Surface) -> Any:
    """Headings, controls and body copy, in reading order and without geometry."""
    out = []
    for element in _text_elements(surface)[:MAX_TEXT_SAMPLES]:
        role = "heading" if element.role == "heading" else element.role or element.tag
        out.append({"role": role, "text": element.text[:160]})
    return out


def link_inventory(surface: Surface) -> Any:
    seen: set[str] = set()
    out = []
    for element in surface.laid_out:
        if not element.link or element.link.href in seen:
            continue
        seen.add(element.link.href)
        out.append(
            {
                "text": element.text[:80] or "(no text)",
                "href": element.link.href[:120],
                "external": element.link.external,
            }
        )
        if len(out) >= MAX_TEXT_SAMPLES:
            break
    return out


def structure(surface: Surface) -> Any:
    """Landmarks, headings and tab order — what a screen reader walks."""
    headings = [
        {"level": e.tag, "text": e.text[:100]}
        for e in surface.laid_out
        if e.role == "heading" or e.tag in ("h1", "h2", "h3", "h4", "h5", "h6")
    ][:MAX_INVENTORY]
    focusable = [
        {"role": e.role or e.tag, "text": e.text[:60] or "(no text)"}
        for e in surface.laid_out
        if e.focusable
    ][:MAX_TEXT_SAMPLES]
    landmarks = sorted({e.nearestLandmark for e in surface.laid_out if e.nearestLandmark})
    return {"headings": headings, "tabOrder": focusable, "landmarks": landmarks}


def measure(surface: Surface) -> Any:
    """Characters per line for the page's body copy — the fact a typography critic needs
    to talk about rag and widows without guessing at pixels."""
    out = []
    for element in _text_elements(surface):
        styles = element.styles
        if element.textLength < 120 or not styles.lineHeight:
            continue
        lines = max(1, round(element.box.h / styles.lineHeight))
        out.append(
            {
                "text": element.text[:60],
                "characters": element.textLength,
                "lines": lines,
                "perLine": round(element.textLength / lines),
            }
        )
        if len(out) >= MAX_INVENTORY:
            break
    return out


def site_map(ctx: RunContext) -> Any:
    from engine.checkers.support import live_pages

    return [
        {"path": page.path, "title": page.title, "depth": page.depth} for page in live_pages(ctx)
    ][:MAX_TEXT_SAMPLES]


def design_tokens(ctx: RunContext) -> Any:
    tokens = ctx.tokens()
    if tokens is None:
        return None
    return {
        "palette": tokens.palette[:MAX_INVENTORY],
        "typeScale": tokens.typeScale,
        "spacing": tokens.spacing,
        "radii": tokens.radii,
    }


def design_deltas(ctx: RunContext, surface: Surface) -> Any:
    mapping = ctx.mapping(surface.page.id, surface.viewport.name)
    if mapping is None or not mapping.confident:
        return None
    return {
        "frame": mapping.frameName,
        "matched": mapping.matched,
        "unmatchedNodes": mapping.unmatchedNodes,
        "unmatchedElements": mapping.unmatchedElements,
    }


BLOCKS = {
    "typeInventory": lambda ctx, surface: type_inventory(surface),
    "colourInventory": lambda ctx, surface: colour_inventory(surface),
    "spacingHistogram": lambda ctx, surface: spacing_histogram(surface),
    "alignmentSets": lambda ctx, surface: alignment_sets(surface),
    "repeatedGroups": lambda ctx, surface: repeated_groups(surface),
    "textInventory": lambda ctx, surface: text_inventory(surface),
    "linkInventory": lambda ctx, surface: link_inventory(surface),
    "structure": lambda ctx, surface: structure(surface),
    "measure": lambda ctx, surface: measure(surface),
    "siteMap": lambda ctx, surface: site_map(ctx),
    "designTokens": lambda ctx, surface: design_tokens(ctx),
    "designDeltas": lambda ctx, surface: design_deltas(ctx, surface),
}


def facts_for(ctx: RunContext, surface: Surface, wanted: tuple[str, ...]) -> dict[str, Any]:
    """Only the blocks this mandate asked for.

    Different subsets are half of what makes the agents different (SPEC §9.2). Handing
    every agent the same facts is how five agents produce one agent's opinion five times.
    """
    facts: dict[str, Any] = {
        "page": surface.page.path,
        "title": surface.page.title,
        "viewport": {
            "name": surface.viewport.name,
            "width": surface.viewport.width,
            "height": surface.viewport.height,
        },
    }
    for name in wanted:
        block = BLOCKS[name](ctx, surface)
        if block:
            facts[name] = block
    return facts


def relevant_knowledge(entries: list[str], surface: Surface) -> list[str]:
    """Project knowledge that mentions this page, plus everything unscoped (SPEC §9.3)."""
    path = surface.page.path.casefold()
    out = []
    for entry in entries:
        lowered = normalise_text(entry)
        if not lowered:
            continue
        scoped = "/" in entry
        if not scoped or path.strip("/") in lowered or path in entry.casefold():
            out.append(entry)
    return out
