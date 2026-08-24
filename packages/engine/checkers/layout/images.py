"""Group B, image geometry — SPEC §8.4 B."""

from __future__ import annotations

from collections.abc import Iterable

from engine.artifact.context import Capability, RunContext
from engine.checkers.base import checker
from engine.checkers.support import element_finding, surfaces
from engine.issues.models import Category, Finding, Severity

ASPECT_TOLERANCE = 0.02
"""2%. Below that it is rounding, above it people can see the squash."""

UPSCALE_TOLERANCE_PX = 1.0


@checker
class ImageGeometry:
    id = "layout.image-geometry"
    category = Category.layout
    requires = frozenset({Capability.ELEMENTS})
    default_severity = Severity.minor

    def run(self, ctx: RunContext) -> Iterable[Finding]:
        for surface in surfaces(ctx):
            for element in surface.laid_out:
                image = element.image
                if (
                    element.tag != "img"
                    or image is None
                    or not image.loaded
                    or image.naturalW <= 0
                    or image.naturalH <= 0
                    or image.renderedW <= 0
                    or image.renderedH <= 0
                ):
                    continue

                natural = image.naturalW / image.naturalH
                shown = image.renderedW / image.renderedH
                if abs(shown - natural) / natural > ASPECT_TOLERANCE:
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="image-aspect-distorted",
                        title="Image is stretched out of its natural aspect ratio",
                        expected=f"{natural:.3f} ({image.naturalW}×{image.naturalH})",
                        actual=f"{shown:.3f} ({image.renderedW:g}×{image.renderedH:g})",
                        groupAs="aspect",
                        data={"src": image.src, "natural": natural, "rendered": shown},
                    )

                if image.renderedW > image.naturalW + UPSCALE_TOLERANCE_PX:
                    yield element_finding(
                        self,
                        surface,
                        element,
                        kind="image-upscaled",
                        title="Image is displayed larger than it actually is",
                        description="It will look soft, and worse on a high-density screen.",
                        expected=f"at most {image.naturalW}px wide",
                        actual=f"{image.renderedW:g}px wide",
                        groupAs="upscaled",
                        severity=Severity.trivial,
                        data={"src": image.src, "naturalW": image.naturalW},
                    )
