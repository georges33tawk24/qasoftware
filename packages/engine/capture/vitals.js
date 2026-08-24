// Web vitals from the platform's own PerformanceObserver (SPEC §4 vitals.json).
//
// ponytail: this is what the web-vitals library wraps. Vendoring a bundle we cannot
// verify offline buys attribution data no checker reads yet. Upgrade path: swap in
// web-vitals if per-element LCP attribution is ever needed.
(() => {
  if (window.__bureauVitals) return;
  const v = { lcp: null, cls: 0, tbt: 0, ttfb: null, inp: null };
  window.__bureauVitals = v;

  const observe = (type, cb, extra) => {
    try {
      new PerformanceObserver(cb).observe({ type, buffered: true, ...(extra || {}) });
    } catch (e) {
      /* unsupported entry type in this browser */
    }
  };

  observe("largest-contentful-paint", (list) => {
    for (const e of list.getEntries()) v.lcp = e.startTime;
  });

  observe("layout-shift", (list) => {
    for (const e of list.getEntries()) if (!e.hadRecentInput) v.cls += e.value;
  });

  observe("longtask", (list) => {
    for (const e of list.getEntries()) v.tbt += Math.max(0, e.duration - 50);
  });

  observe("event", (list) => {
    for (const e of list.getEntries()) v.inp = Math.max(v.inp || 0, e.duration);
  }, { durationThreshold: 40 });

  observe("navigation", (list) => {
    for (const e of list.getEntries()) v.ttfb = e.responseStart;
  });
})();
