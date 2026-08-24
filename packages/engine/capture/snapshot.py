"""The per-viewport snapshot — SPEC §4.1.

One `page.evaluate` walk, one stepped occlusion pass, two screenshots. Everything a
checker will ever need about the rendered page comes from here, because a checker may
never open a browser.
"""

from __future__ import annotations

from pathlib import Path
from statistics import median
from typing import Any

from playwright.async_api import Locator, Page

from engine.artifact.models import ElementRecord, Metrics
from engine.issues.fingerprint import element_stable_key

_HERE = Path(__file__).parent
_SNAPSHOT_JS = (_HERE / "snapshot.js").read_text()
_OCCLUSION_JS = (_HERE / "occlusion.js").read_text()

MAX_ELEMENTS = 5000
"""ponytail: a hard cap so one pathological page cannot produce a 200MB artifact.
Recorded in the manifest when hit; raise it per project if a real page needs more."""


async def capture_elements(page: Page, *, max_elements: int = MAX_ELEMENTS) -> list[ElementRecord]:
    raw: list[dict[str, Any]] = await page.evaluate(_SNAPSHOT_JS, {"maxElements": max_elements})
    elements = [ElementRecord.model_validate(record) for record in raw]
    for element in elements:
        element.stableKey = element_stable_key(element)
    return elements


async def capture_occlusion(page: Page, elements: list[ElementRecord]) -> None:
    """Fill in `occludedBy` by stepping down the page a viewport at a time.

    Done here rather than in a checker because `elementFromPoint` needs a live browser,
    and checkers do not get one.
    """
    height: float = await page.evaluate("() => document.body.scrollHeight")
    viewport = page.viewport_size or {"height": 900}
    band = max(1, viewport["height"])
    offset = 0
    while offset < height:
        indices = [
            i
            for i, el in enumerate(elements)
            if el.visible and offset <= el.box.y + el.box.h / 2 < offset + band
        ]
        if indices:
            await page.evaluate("(y) => window.scrollTo(0, y)", offset)
            await page.wait_for_timeout(60)
            hits: dict[str, int] = await page.evaluate(_OCCLUSION_JS, indices)
            for raw_index, occluder in hits.items():
                if 0 <= occluder < len(elements):
                    elements[int(raw_index)].occludedBy = elements[occluder].id
        offset += band
    await page.evaluate("() => window.scrollTo(0, 0)")


async def capture_screenshots(
    page: Page, *, full: Path, fold: Path, mask: list[Locator] | None = None
) -> None:
    full.parent.mkdir(parents=True, exist_ok=True)
    await page.screenshot(path=str(full), full_page=True, animations="disabled", mask=mask)
    await page.screenshot(path=str(fold), full_page=False, animations="disabled", mask=mask)


async def read_vitals(page: Page) -> dict[str, float | None]:
    raw: dict[str, float | None] | None = await page.evaluate("() => window.__bureauVitals || null")
    return raw or {}


VITAL_NAMES = ("lcp", "cls", "tbt", "ttfb", "inp")


def summarise_vitals(samples: list[dict[str, float | None]]) -> Metrics:
    """Median per metric, with the best and worst seen beside it.

    Median rather than mean because one slow load — a cold cache, a GC pause, a shared
    runner having a bad second — should not drag the number it reports.
    """
    metrics = Metrics(sampleCount=len(samples))
    for name in VITAL_NAMES:
        values = sorted(v for s in samples if (v := s.get(name)) is not None)
        if not values:
            continue
        setattr(metrics, name, median(values))
        metrics.low[name] = values[0]
        metrics.high[name] = values[-1]
    return metrics
