"""The self-contained HTML report — SPEC §12.1, styled per §16.

One file. No CDN, no external fonts, no build step: it has to survive being emailed.

The document is a static shell plus a JSON payload, rendered client-side with
`textContent`. That is not a style choice — every string in this report comes from a
site we do not control, and building the DOM through text nodes means a page whose
heading is `<script>alert(1)</script>` renders as those characters instead of running.
"""

from __future__ import annotations

import json
from typing import Any

from engine import branding
from engine.report.compose import Composed

_STYLE = """
:root {
  --surface: #0E0F11; --raised: #17181B; --border: #26282D;
  --text: #E6E7EA; --text-2: #9296A0; --text-3: #6A6E78;
  --accent: #3B7DD8;
  --blocker: #C7443A; --critical: #D2683C; --major: #C99A2E;
  --minor: #5C7FA8; --trivial: #6A6E78;
  --sans: ui-sans-serif, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
  --mono: ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace;
  --row: 32px;
}
@media (prefers-color-scheme: light) {
  :root {
    --surface: #FBFBFC; --raised: #FFFFFF; --border: #E3E5E9;
    --text: #16181C; --text-2: #5A606C; --text-3: #858B97;
  }
}
* { box-sizing: border-box; }
html { background: var(--surface); }
body {
  margin: 0; background: var(--surface); color: var(--text);
  font-family: var(--sans); font-size: 13px; line-height: 20px;
  font-variant-numeric: tabular-nums;
  -webkit-font-smoothing: antialiased;
}
main { max-width: 1180px; margin: 0 auto; padding: 0 24px 96px; }
h1, h2, h3 { margin: 0; font-weight: 600; letter-spacing: -0.01em; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; border-radius: 2px; }
code, .mono { font-family: var(--mono); font-size: 12px; }

.head { border-bottom: 1px solid var(--border); padding: 28px 0 20px; margin-bottom: 24px; }
.head h1 { font-size: 20px; line-height: 28px; }
.head .target { font-family: var(--mono); font-size: 13px; color: var(--text-2); }
.meta { display: flex; flex-wrap: wrap; gap: 4px 24px; margin-top: 12px; color: var(--text-2); }
.meta b { color: var(--text); font-weight: 500; }

.tally { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 20px; }
.tally .cell {
  display: flex; align-items: baseline; gap: 8px;
  border: 1px solid var(--border); border-radius: 4px;
  padding: 8px 12px; min-width: 108px; background: var(--raised);
}
.tally .n { font-size: 18px; font-weight: 600; line-height: 22px; }
.tally .k { color: var(--text-2); font-size: 12px; text-transform: lowercase; }
.tally .cell[data-sev="blocker"] .n { color: var(--blocker); }
.tally .cell[data-sev="critical"] .n { color: var(--critical); }
.tally .cell[data-sev="major"] .n { color: var(--major); }
.tally .cell[data-sev="minor"] .n { color: var(--minor); }
.tally .cell[data-sev="trivial"] .n { color: var(--trivial); }

section { margin-top: 32px; }
section > h2 {
  font-size: 12px; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--text-3); padding-bottom: 8px; border-bottom: 1px solid var(--border);
}
.empty { color: var(--text-3); padding: 12px 0; }

.asked { display: flex; flex-direction: column; gap: 8px; padding-top: 12px; }
.asked .row {
  border: 1px solid var(--border); border-left-width: 3px; border-radius: 4px;
  padding: 10px 12px; background: var(--raised);
}
.asked .row[data-verdict="applied"] { border-left-color: var(--minor); }
.asked .row[data-verdict="not-applied"] { border-left-color: var(--blocker); }
.asked .row[data-verdict="unverifiable"] { border-left-color: var(--border); }
.asked .headline { font-weight: 500; }
.asked .row[data-verdict="not-applied"] .headline { color: var(--blocker); }
.asked .why { color: var(--text-2); font-size: 12px; margin-top: 3px; }
.asked .scope { font-family: var(--mono); font-size: 11px; color: var(--text-3); }

.diff { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 12px; }
.diff .cell {
  display: flex; align-items: baseline; gap: 8px; padding: 8px 12px; min-width: 120px;
  border: 1px solid var(--border); border-radius: 4px; background: var(--raised);
}
.diff .cell[data-change="regressed"] { border-color: var(--blocker); }
.diff .cell[data-change="regressed"] .n { color: var(--blocker); }
.diff .n { font-size: 18px; font-weight: 600; line-height: 22px; }
.diff .k { color: var(--text-2); font-size: 12px; }
.regressions { padding-top: 12px; display: flex; flex-direction: column; gap: 4px; }
.regressions .row { font-size: 13px; }
.regressions .row b { color: var(--blocker); font-weight: 500; }

.filters {
  position: sticky; top: 0; z-index: 2; background: var(--surface);
  display: flex; flex-wrap: wrap; gap: 8px; align-items: center;
  padding: 12px 0; border-bottom: 1px solid var(--border); margin-bottom: 4px;
}
.filters label { color: var(--text-3); font-size: 12px; margin-right: -4px; }
.filters select, .filters input {
  background: var(--raised); color: var(--text); border: 1px solid var(--border);
  border-radius: 4px; padding: 5px 8px; font: inherit; font-size: 12px; height: var(--row);
}
.chipset { display: flex; gap: 4px; }
.chip {
  border: 1px solid var(--border); background: var(--raised); color: var(--text-2);
  border-radius: 4px; padding: 0 10px; height: var(--row); font: inherit; font-size: 12px;
  cursor: pointer; transition: background 140ms ease-out, color 140ms ease-out;
}
.chip[aria-pressed="true"] { color: var(--text); border-color: var(--text-3); }
.chip[data-sev="blocker"][aria-pressed="true"] { border-color: var(--blocker); }
.chip[data-sev="critical"][aria-pressed="true"] { border-color: var(--critical); }
.chip[data-sev="major"][aria-pressed="true"] { border-color: var(--major); }
.chip[data-sev="minor"][aria-pressed="true"] { border-color: var(--minor); }
.chip[data-sev="trivial"][aria-pressed="true"] { border-color: var(--trivial); }
.count { margin-left: auto; color: var(--text-3); font-size: 12px; }

.page-group { margin-top: 24px; }
.page-group > header {
  display: flex; align-items: baseline; gap: 12px; padding: 6px 0;
  border-bottom: 1px solid var(--border);
}
.page-group .path { font-family: var(--mono); font-size: 13px; color: var(--text); }
.page-group .title { color: var(--text-3); font-size: 12px; }

.issue {
  border: 1px solid var(--border); border-radius: 4px; background: var(--raised);
  margin-top: 8px; overflow: hidden;
}
.issue > summary {
  display: grid; grid-template-columns: 28px 76px 1fr auto; gap: 12px; align-items: baseline;
  padding: 8px 12px; cursor: pointer; list-style: none;
}
.issue > summary::-webkit-details-marker { display: none; }
.issue > summary:hover { background: color-mix(in srgb, var(--text) 4%, transparent); }
.issue .n { color: var(--text-3); font-family: var(--mono); font-size: 12px; }
.issue .sev {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em; font-weight: 600;
}
.issue[data-sev="blocker"] .sev { color: var(--blocker); }
.issue[data-sev="critical"] .sev { color: var(--critical); }
.issue[data-sev="major"] .sev { color: var(--major); }
.issue[data-sev="minor"] .sev { color: var(--minor); }
.issue[data-sev="trivial"] .sev { color: var(--trivial); }
.issue[data-sev="blocker"] { border-left: 2px solid var(--blocker); }
.issue[data-sev="critical"] { border-left: 2px solid var(--critical); }
.issue[data-sev="major"] { border-left: 2px solid var(--major); }
.issue .head-line { display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
.issue .badge {
  border: 1px solid var(--border); border-radius: 3px; padding: 0 5px;
  color: var(--text-3); font-size: 11px; font-family: var(--mono);
}
.issue .instances { color: var(--text-3); font-size: 12px; white-space: nowrap; }
.body { padding: 4px 12px 16px 128px; border-top: 1px solid var(--border); }
.body p { margin: 8px 0; color: var(--text-2); max-width: 78ch; }
.kv { display: grid; grid-template-columns: 92px 1fr; gap: 4px 12px; margin: 12px 0; }
.kv dt { color: var(--text-3); font-size: 12px; }
.kv dd { margin: 0; font-family: var(--mono); font-size: 12px; overflow-wrap: anywhere; }
.kv .was { color: var(--blocker); }
.kv .want { color: var(--text); }
figure { margin: 16px 0 0; }
figure img {
  max-width: 100%; border: 1px solid var(--border); border-radius: 4px; display: block;
}
figcaption { color: var(--text-3); font-size: 12px; margin-top: 6px; font-family: var(--mono); }
table.instances { border-collapse: collapse; width: 100%; margin-top: 12px; font-size: 12px; }
table.instances th {
  text-align: left; color: var(--text-3); font-weight: 500; padding: 4px 8px 4px 0;
  border-bottom: 1px solid var(--border);
}
table.instances td {
  padding: 4px 8px 4px 0; border-bottom: 1px solid var(--border);
  font-family: var(--mono); color: var(--text-2); overflow-wrap: anywhere;
}
details.appendix > summary { cursor: pointer; color: var(--text-2); padding: 8px 0; }
details.steps { margin-top: 12px; }
details.steps > summary { cursor: pointer; color: var(--text-2); font-size: 12px; }
details.steps ol { margin: 8px 0 0; padding-left: 20px; font-size: 12px; color: var(--text-2); }
details.steps li { margin-bottom: 2px; }
details.steps li.was { color: var(--blocker); }
details.steps .where { font-family: var(--mono); color: var(--text-3); font-size: 11px; }
p.files a { margin-right: 16px; font-family: var(--mono); font-size: 12px; }
.checkers { display: flex; flex-wrap: wrap; gap: 4px; padding: 8px 0 0; }
.checkers span {
  font-family: var(--mono); font-size: 11px; color: var(--text-3);
  border: 1px solid var(--border); border-radius: 3px; padding: 1px 6px;
}
.checkers span.reported { color: var(--text-2); border-color: var(--text-3); }
footer { margin-top: 48px; padding-top: 16px; border-top: 1px solid var(--border);
         color: var(--text-3); font-size: 12px; }

@media (prefers-reduced-motion: reduce) { * { transition: none !important; } }
@media (max-width: 720px) {
  main { padding: 0 16px 64px; }
  .issue > summary { grid-template-columns: 24px 1fr; }
  .issue .instances { display: none; }
  .body { padding-left: 12px; }
}
@media print {
  :root { --surface: #fff; --raised: #fff; --border: #ccc; --text: #000; --text-2: #333;
          --text-3: #666; }
  .filters { display: none; }
  .issue { break-inside: avoid; }
}
"""

_SCRIPT = r"""
const DATA = JSON.parse(document.getElementById("report-data").textContent);
const SEVERITIES = ["blocker", "critical", "major", "minor", "trivial"];
const IMAGE_KINDS = ["screenshot", "crop", "side_by_side"];
const LABELS = {trace: "Playwright trace", video: "Video", steps: "steps.json"};
const state = { severity: new Set(SEVERITIES), category: "", page: "", viewport: "", q: "" };

const el = (tag, cls, text) => {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = String(text);
  return node;
};

function matches(issue) {
  if (!state.severity.has(issue.severity)) return false;
  if (state.category && issue.category !== state.category) return false;
  if (state.page && !issue.pagePaths.includes(state.page)) return false;
  if (state.viewport && !issue.viewports.includes(state.viewport)) return false;
  if (state.q) {
    const hay = [issue.title, issue.checkerId, issue.issueKind, issue.description,
                 issue.actual, issue.expected].join(" ").toLowerCase();
    if (!hay.includes(state.q)) return false;
  }
  return true;
}

function issueNode(issue) {
  const box = el("details", "issue");
  box.dataset.sev = issue.severity;

  const summary = el("summary");
  summary.append(el("span", "n", issue.n), el("span", "sev", issue.severity));

  const head = el("div", "head-line");
  head.append(el("span", "title", issue.title));
  head.append(el("span", "badge", issue.checkerId));
  head.append(el("span", "badge", issue.source));
  if (issue.confidence !== null && issue.confidence !== undefined) {
    head.append(el("span", "badge", Math.round(issue.confidence * 100) + "%"));
  }
  summary.append(head);
  summary.append(el("span", "instances",
    issue.instanceCount + (issue.instanceCount === 1 ? " instance" : " instances")));
  box.append(summary);

  const body = el("div", "body");
  if (issue.description) body.append(el("p", null, issue.description));

  const kv = el("dl", "kv");
  const pair = (k, v, cls) => {
    if (v === null || v === undefined || v === "") return;
    kv.append(el("dt", null, k), el("dd", cls, v));
  };
  pair("Expected", issue.expected, "want");
  pair("Actual", issue.actual, "was");
  pair("Pages", issue.pagePaths.join(", "));
  pair("Viewports", issue.viewports.join(", "));
  if (kv.children.length) body.append(kv);

  const pictures = issue.evidence.filter((e) => IMAGE_KINDS.includes(e.kind));
  const files = issue.evidence.filter((e) => !IMAGE_KINDS.includes(e.kind));

  if (issue.steps && issue.steps.length) {
    const details = el("details", "steps");
    details.append(el("summary", null, "Reproduction steps (" + issue.steps.length + ")"));
    const list = el("ol");
    for (const step of issue.steps) {
      const item = el("li", step.status === "failed" ? "was" : null, step.text);
      const where = el("span", "where", " " + step.url);
      item.append(where);
      list.append(item);
    }
    details.append(list);
    body.append(details);
  }

  for (const evidence of pictures.slice(0, 12)) {
    const figure = el("figure");
    const img = el("img");
    img.src = evidence.src;
    img.alt = "Evidence for issue " + issue.n + ": " + (evidence.caption || issue.title);
    img.loading = "lazy";
    figure.append(img, el("figcaption", null, evidence.caption));
    body.append(figure);
  }

  if (files.length) {
    const row = el("p", "files");
    for (const file of files) {
      const link = el("a", null, LABELS[file.kind] || file.kind);
      link.href = file.src;
      link.download = "";
      row.append(link);
    }
    body.append(row);
  }

  if (issue.instances.length) {
    const table = el("table", "instances");
    const head = el("tr");
    for (const label of ["Page", "Viewport", "Selector", "Measured"]) {
      head.append(el("th", null, label));
    }
    table.append(head);
    for (const instance of issue.instances.slice(0, 50)) {
      const row = el("tr");
      row.append(el("td", null, instance.pagePath));
      row.append(el("td", null, instance.viewport));
      row.append(el("td", null, instance.selector || "—"));
      row.append(el("td", null, instance.actual || "—"));
      table.append(row);
    }
    body.append(table);
    if (issue.instances.length > 50) {
      body.append(el("p", null, (issue.instances.length - 50) + " further instances not listed."));
    }
  }

  box.append(body);
  return box;
}

function render() {
  const list = document.getElementById("issues");
  list.replaceChildren();
  const visible = DATA.issues.filter(matches);

  const byPage = new Map();
  for (const issue of visible) {
    const key = issue.pagePaths.length === 1 ? issue.pagePaths[0] : "Across pages";
    if (!byPage.has(key)) byPage.set(key, []);
    byPage.get(key).push(issue);
  }

  const pageTitle = Object.fromEntries(DATA.pages.map((p) => [p.path, p.title || ""]));
  for (const [path, issues] of byPage) {
    const group = el("div", "page-group");
    const header = el("header");
    header.append(el("h3", "path", path));
    if (pageTitle[path]) header.append(el("span", "title", pageTitle[path]));
    header.append(el("span", "count", issues.length));
    group.append(header);
    for (const issue of issues) group.append(issueNode(issue));
    list.append(group);
  }

  if (!visible.length) {
    list.append(el("p", "empty", "No issues match these filters."));
  }
  document.getElementById("visible-count").textContent =
    visible.length + " of " + DATA.issues.length + " issues";
}

function buildFilters() {
  const bar = document.getElementById("filters");

  const chips = el("div", "chipset");
  for (const severity of SEVERITIES) {
    const chip = el("button", "chip", severity + " " + (DATA.counts[severity] || 0));
    chip.type = "button";
    chip.dataset.sev = severity;
    chip.setAttribute("aria-pressed", "true");
    chip.addEventListener("click", () => {
      const on = chip.getAttribute("aria-pressed") === "true";
      chip.setAttribute("aria-pressed", String(!on));
      if (on) state.severity.delete(severity); else state.severity.add(severity);
      render();
    });
    chips.append(chip);
  }
  bar.append(chips);

  const select = (id, label, options, key) => {
    const wrap = el("label", null, label);
    wrap.htmlFor = id;
    const node = el("select");
    node.id = id;
    node.append(new Option("all", ""));
    for (const option of options) node.append(new Option(option, option));
    node.addEventListener("change", () => { state[key] = node.value; render(); });
    bar.append(wrap, node);
  };
  select("f-cat", "category", [...new Set(DATA.issues.map((i) => i.category))].sort(), "category");
  select("f-page", "page", DATA.pages.map((p) => p.path), "page");
  select("f-vp", "viewport", DATA.run.viewports, "viewport");

  const search = el("input");
  search.type = "search";
  search.id = "f-q";
  search.placeholder = "filter…";
  search.setAttribute("aria-label", "Filter issues by text");
  search.addEventListener("input", () => { state.q = search.value.toLowerCase(); render(); });
  bar.append(search);
  bar.append(el("span", "count", "").cloneNode(true));
  const count = el("span", "count");
  count.id = "visible-count";
  bar.append(count);
}

buildFilters();
render();
"""


def _json_block(payload: dict[str, Any]) -> str:
    """`<` is escaped so no value in the payload can close this script element."""
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c")


def _requested_block(changes: list[dict[str, Any]]) -> str:
    """SPEC §10. The "NOT applied" line is the one people read the report for."""
    if not changes:
        return (
            '<p class="empty">No project knowledge was supplied for this run, so there is '
            "nothing to confirm as applied or not applied.</p>"
        )
    rows = []
    for change in changes:
        suppressed = change.get("suppressed") or 0
        hidden = f" · {suppressed} finding(s) suppressed" if suppressed else ""
        rows.append(
            f'<div class="row" data-verdict="{_text(change["verdict"])}">'
            f'<div class="headline">{_text(change["headline"])}</div>'
            f'<div class="why">{_text(change["detail"])}{_text(hidden)}</div>'
            f'<div class="scope">{_text(change["scope"])}'
            + (f" — {_text(change['note'])}" if change.get("note") else "")
            + "</div></div>"
        )
    return f'<div class="asked">{"".join(rows)}</div>'


def _flaky_block(entries: list[dict[str, Any]]) -> str:
    """Intermittent findings, kept out of the main list.

    Not hidden: a checker that fires on half the runs is worth knowing about. Just not
    mixed into a list someone is working through, where it wastes the same hour twice.
    """
    if not entries:
        return ""
    rows = "".join(
        f'<div class="row"><b>{_text(entry["severity"])}</b> {_text(entry["title"])} '
        f'<span class="scope">{_text(entry["checkerId"])} · {entry["instances"]}×</span></div>'
        for entry in entries
    )
    return f"""<section id="flaky">
    <h2>Intermittent</h2>
    <p class="empty">Seen, then not seen, then seen again across runs. Treat these as
      information about the checker or the page's own variance, not as changes.</p>
    <div class="regressions">{rows}</div>
  </section>"""


def _diff_block(diff: dict[str, Any] | None) -> str:
    """SPEC §11. Regressions get their own list — those are the embarrassing ones."""
    if not diff:
        return ""
    counts = diff["counts"]
    cells = "".join(
        f'<div class="cell" data-change="{key}"><span class="n">{counts.get(key, 0)}</span>'
        f'<span class="k">{label}</span></div>'
        for key, label in (
            ("regressed", "regressed"),
            ("new", "new"),
            ("still-open", "still open"),
            ("fixed", "fixed"),
        )
    )
    regressed = [e for e in diff["entries"] if e["change"] == "regressed"]
    listing = "".join(
        f'<div class="row"><b>{_text(entry["severity"])}</b> {_text(entry["title"])}</div>'
        for entry in regressed
    )
    body = f'<div class="regressions">{listing}</div>' if listing else ""
    return f"""<section id="diff">
    <h2>Since the last run</h2>
    <div class="diff">{cells}</div>
    {body}
  </section>"""


def render(composed: Composed, *, inline: bool) -> str:
    payload = dict(composed.payload)
    if inline:
        uris = {m.path: m.data_uri() for m in composed.media}
        for issue in payload["issues"]:
            for evidence in issue["evidence"]:
                evidence["src"] = uris.get(evidence["src"], evidence["src"])

    run = payload["run"]
    counts = payload["counts"]
    totals = payload["totals"]
    duration = f"{(run['durationMs'] or 0) / 1000:.0f}s"

    tally = "".join(
        f'<div class="cell" data-sev="{name}"><span class="n">{counts[name]}</span>'
        f'<span class="k">{name}</span></div>'
        for name in ("blocker", "critical", "major", "minor", "trivial")
    )
    silent = "".join(f"<span>{checker}</span>" for checker in payload["appendix"]["silent"])
    reported = "".join(
        f'<span class="reported">{checker}</span>' for checker in payload["appendix"]["reported"]
    )
    skipped = "".join(
        f"<span>{checker} — {reason}</span>"
        for checker, reason in sorted(payload["appendix"]["skipped"].items())
    )
    agents_block = _agents_block(payload.get("agents"))
    requested_block = _requested_block(payload.get("requestedChanges") or [])
    diff_block = _diff_block(payload.get("diff"))
    flaky_block = _flaky_block(payload.get("flaky") or [])
    blocked = run["blockedPages"]
    blocked_note = (
        f"<div><b>{blocked}</b> page(s) blocked by bot protection and not checked</div>"
        if blocked
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{branding.PRODUCT_NAME} report — {_text(run["target"])}</title>
<style>{_STYLE}</style>
</head>
<body>
<main>
  <div class="head">
    <h1>{branding.PRODUCT_NAME} report</h1>
    <div class="target">{_text(run["target"])}</div>
    <div class="meta">
      <div><b>{run["pageCount"]}</b> pages</div>
      <div><b>{totals["issues"]}</b> issues from <b>{totals["instances"]}</b> instances</div>
      <div>{_text(", ".join(run["viewports"]))}</div>
      <div>{_text(run["startedAt"])}</div>
      <div>{duration}</div>
      <div>driver <b>{_text(run["driver"])}</b></div>
      {blocked_note}
    </div>
    <div class="tally">{tally}</div>
  </div>

  <section id="requested">
    <h2>Requested changes</h2>
    {requested_block}
  </section>

  {diff_block}

  {flaky_block}

  <section>
    <h2>Issues</h2>
    <div class="filters" id="filters"><label>severity</label></div>
    <div id="issues"></div>
  </section>

  <section>
    <h2>What was checked</h2>
    {agents_block}
    <details class="appendix">
      <summary>{len(payload["appendix"]["silent"])} checkers ran and found nothing;
        {len(payload["appendix"]["reported"])} reported something.</summary>
      <div class="checkers">{reported}{silent}</div>
    </details>
    {
        f'<details class="appendix"><summary>{len(payload["appendix"]["skipped"])} checkers '
        f'skipped for want of data.</summary><div class="checkers">{skipped}</div></details>'
        if skipped
        else ""
    }
  </section>

  <footer>
    Run {_text(run["runId"])} · checkers at {_text(str(run["checkersSha"])[:12])} ·
    every measurement in this report came from a single crawl.
  </footer>
</main>
<script type="application/json" id="report-data">{_json_block(payload)}</script>
<script>{_SCRIPT}</script>
</body>
</html>
"""


def _agents_block(agents: dict[str, Any] | None) -> str:
    """SPEC §9.4: an agent below a 20% confirm rate has a bad prompt and is burning
    money. That is only visible if someone shows it."""
    if not agents or not agents.get("calibration"):
        return ""
    calibration = agents["calibration"]
    cost = agents.get("cost") or {}
    tallies = calibration.get("agents") or {}
    poor = set(calibration.get("underperforming") or [])

    rows = []
    for name, stats in sorted(tallies.items()):
        rate = stats.get("confirmRate")
        kept = stats.get("confirmed", 0) + stats.get("downgraded", 0)
        flag = ' class="was"' if name in poor else ""
        rows.append(
            f"<tr><td>{_text(name)}</td><td>{stats.get('swept', 0)}</td>"
            f"<td>{kept}</td><td>{stats.get('rejected', 0)}</td>"
            f"<td{flag}>{'—' if rate is None else f'{rate:.0%}'}</td></tr>"
        )
    if not rows:
        return ""

    spent = cost.get("spentUsd")
    summary = (
        f"{len(tallies)} agents · ${spent:.3f} over {cost.get('calls', 0)} calls"
        if isinstance(spent, int | float)
        else f"{len(tallies)} agents"
    )
    warning = (
        f'<p class="empty was">Below a 20% confirm rate: {", ".join(sorted(poor))}. '
        "Those prompts need work.</p>"
        if poor
        else ""
    )
    return (
        '<details class="appendix"><summary>Agent calibration — '
        f"{_text(summary)}</summary>"
        '<table class="instances"><tr><th>Agent</th><th>Flagged</th><th>Kept</th>'
        "<th>Rejected</th><th>Confirm rate</th></tr>"
        + "".join(rows)
        + f"</table>{warning}</details>"
    )


def _text(value: str) -> str:
    """The shell has a handful of interpolations; the payload handles the rest."""
    return (
        value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )
