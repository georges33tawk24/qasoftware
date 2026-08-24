// Second pass: which elements are covered at their own centre point (SPEC §8.4 B,
// "clickable element occluded at its centre point").
//
// elementFromPoint only answers for the current scroll position, so the caller steps
// down the page a viewport at a time and unions the answers.
(indices) => {
  const nodes = window.__bureauNodes || [];
  if (!window.__bureauIndex || window.__bureauIndex.size !== nodes.length) {
    window.__bureauIndex = new Map(nodes.map((n, i) => [n, i]));
  }
  const index = window.__bureauIndex;
  const out = {};

  // An element scrolled out of its own scroll container is not occluded, it is simply
  // not there yet. Hit-testing its centre returns whatever is painted at that point,
  // which is how a long list reports every row below the fold as "covered".
  const clipped = (el, x, y) => {
    for (let p = el.parentElement; p; p = p.parentElement) {
      const style = getComputedStyle(p);
      if (style.overflow === "visible" && style.overflowX === "visible" && style.overflowY === "visible") continue;
      const r = p.getBoundingClientRect();
      if (x < r.left || x > r.right || y < r.top || y > r.bottom) return true;
    }
    return false;
  };
  for (const i of indices) {
    const el = nodes[i];
    if (!el) continue;
    const rect = el.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) continue;
    const x = rect.x + rect.width / 2;
    const y = rect.y + rect.height / 2;
    if (x < 0 || y < 0 || x > window.innerWidth || y > window.innerHeight) continue;
    if (clipped(el, x, y)) continue;
    const hit = document.elementFromPoint(x, y);
    if (!hit || el.contains(hit) || hit.contains(el)) continue;
    out[i] = index.has(hit) ? index.get(hit) : -1;
  }
  return out;
}
