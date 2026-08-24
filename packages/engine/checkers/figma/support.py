"""Shared plumbing for the design comparison — SPEC §7, §8.4 J."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from engine.artifact.context import RunContext
from engine.artifact.models import ElementRecord, FigmaTolerances
from engine.checkers.support import Surface, surfaces
from engine.figma.models import FigmaDocument, FigmaNode
from engine.matching.models import MappingFile


@dataclass(frozen=True)
class Pair:
    """One matched node/element pair, with everything a delta check needs."""

    surface: Surface
    mapping: MappingFile
    node: FigmaNode
    element: ElementRecord
    containerNode: FigmaNode | None
    containerElement: ElementRecord | None
    absorbed: bool = False
    """The design draws this node inside the element it matched — a button's label. Only
    its text properties mean anything; its box is the container's box."""

    @property
    def scale(self) -> float:
        return self.mapping.scale

    def live(self, design_px: float) -> float:
        """Design pixels to live pixels. Every delta is reported after this conversion
        (SPEC §7 step 1)."""
        return design_px * self.mapping.scale


def boxed(match: SurfaceMatch) -> list[Pair]:
    """Pairs whose geometry is worth comparing."""
    return [pair for pair in match.pairs if not pair.absorbed]


@dataclass(frozen=True)
class SurfaceMatch:
    surface: Surface
    mapping: MappingFile
    document: FigmaDocument
    pairs: list[Pair]


def matched_surfaces(ctx: RunContext) -> Iterator[SurfaceMatch]:
    """Only surfaces whose mapping is confident.

    SPEC §7: one wrong match produces a page of nonsense findings, which is the fastest
    way to lose a user permanently. A mapping that did not take is reported once, by
    `figma.no-match`, and produces no deltas at all.
    """
    document = ctx.figma()
    if document is None:
        return
    for surface in surfaces(ctx):
        mapping = ctx.mapping(surface.page.id, surface.viewport.name)
        if mapping is None or not mapping.confident:
            continue
        index = surface.by_id
        pairs: list[Pair] = []
        for record in mapping.matches:
            if record.unmatched or not record.figmaNodeId or not record.elementId:
                continue
            node = document.nodes.get(record.figmaNodeId)
            element = index.get(record.elementId)
            if node is None or element is None:
                continue
            pairs.append(
                Pair(
                    surface=surface,
                    mapping=mapping,
                    node=node,
                    element=element,
                    containerNode=document.nodes.get(record.containerNodeId or ""),
                    containerElement=index.get(record.containerElementId or ""),
                    absorbed=record.method == "absorbed",
                )
            )
        yield SurfaceMatch(surface=surface, mapping=mapping, document=document, pairs=pairs)


def tolerances(ctx: RunContext) -> FigmaTolerances:
    return ctx.manifest.config.figmaTolerances


def px(value: float) -> str:
    return f"{value:g}px"
