"""axe-core injection — SPEC §8.4 E.

The bundle is vendored rather than reimplemented (`make vendor`). When it is absent the
artifact simply has no AXE capability and the accessibility checkers self-skip, which is
exactly what `Checker.requires` is for.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import Page

BUNDLE = Path(__file__).resolve().parents[3] / "vendor" / "axe.min.js"

_RUN = """
() => axe.run(document, {
  resultTypes: ['violations', 'incomplete'],
  reporter: 'v2',
}).then((r) => {
  // Resolve each node to a box here, while there is still a browser. A checker gets the
  // artifact and nothing else, and phase 3 has to draw a ring around the offender.
  const locate = (node) => {
    const selector = Array.isArray(node.target) ? node.target[node.target.length - 1] : node.target;
    try {
      const el = document.querySelector(String(selector));
      if (!el) return node;
      const b = el.getBoundingClientRect();
      node.box = {
        x: Math.round((b.x + window.scrollX) * 100) / 100,
        y: Math.round((b.y + window.scrollY) * 100) / 100,
        w: Math.round(b.width * 100) / 100,
        h: Math.round(b.height * 100) / 100,
      };
    } catch (e) {
      /* axe emits selectors querySelector cannot always parse */
    }
    return node;
  };
  (r.violations || []).forEach((v) => v.nodes.forEach(locate));
  (r.incomplete || []).forEach((v) => v.nodes.forEach(locate));
  return {
    violations: r.violations,
    incomplete: r.incomplete,
    passes: (r.passes || []).map((p) => p.id),
    testEngine: r.testEngine,
  };
})
"""


def available() -> bool:
    return BUNDLE.is_file()


async def run(page: Page) -> dict[str, Any] | None:
    if not available():
        return None
    try:
        await page.add_script_tag(content=BUNDLE.read_text())
        result: dict[str, Any] = await page.evaluate(_RUN)
    except PlaywrightError:
        return None
    return result
