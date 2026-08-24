# Bureau — Autonomous QA Platform

> Working codename. Rename freely; nothing in the codebase should hardcode it outside `config/branding.ts`.

**One line:** Give it a URL (and a Figma file, if one exists). It crawls, measures, tests, probes, and reasons about the site, then hands back a triaged report of every defect it can find — with annotated screenshots, reproduction steps, and video.

---

## 1. Design principles

These are the rules that decide arguments later. When in doubt, come back here.

1. **Capture once, check many.** A single crawl produces one immutable *run artifact*. Every checker is a pure function over that artifact. Nothing re-crawls.
2. **Measure before you reason.** Anything expressible as arithmetic is arithmetic. The model is the last resort, never the first. A three-pixel misalignment is a subtraction problem, not a judgement call.
3. **Ground the model.** When a model is used, it receives the screenshot *plus* the measured numbers. Never the screenshot alone.
4. **Exhaustive by default.** There is no "enable performance checks" toggle. Every run does the full sweep. Clean checks stay silent in the report. Checks people must remember to enable are checks that don't get run.
5. **Over-report at the front, filter at the back.** High recall in detection, high precision in verification. It is better to surface fifty findings and dismiss fifteen than to miss one.
6. **One core, pluggable edges.** Checkers are modules. Browser drivers are swappable. Export targets are thin adapters over one neutral issue format. Never write a bespoke integration.
7. **Dismissals are permanent.** An issue dismissed once must never reappear. This is the single feature that decides whether the tool gets used past week two.

---

## 2. Honest scope

**What this is:** a tireless junior QA engineer who never gets bored, never skips a page, never forgets the mobile viewport, and works at 3am.

**What this is not:** a senior. It does not know that the discount should apply to the second item, that this particular client always cares about the tablet breakpoint, or that a wrong total is catastrophic while a three-pixel shift is fine. Severity assignment and exploratory intuition remain human. The tool's job is to clear the mechanical 80% so a human can spend their whole day on the 20% that matters.

Build the product to *reflect* this: severity is always editable, every AI finding is labelled as such with a confidence score, and the human's dismissal always wins.

---

## 3. Architecture

```
┌─────────────┐     ┌──────────────────────────────────────┐
│  Next.js UI │◄───►│  FastAPI control plane               │
│  (web)      │ SSE │  runs, projects, issues, board, auth  │
└─────────────┘     └───────────────┬──────────────────────┘
                                    │ enqueue
                                    ▼
                    ┌──────────────────────────────────────┐
                    │  Worker (Python, Playwright)         │
                    │                                       │
                    │  1. CAPTURE   ─► run artifact         │
                    │  2. INGEST    ─► figma artifact       │
                    │  3. MATCH     ─► node↔element map     │
                    │  4. CHECK     ─► deterministic sweep  │
                    │  5. EXERCISE  ─► flows + API probes   │
                    │  6. REASON    ─► agent swarm          │
                    │  7. VERIFY    ─► recheck pass         │
                    │  8. RESOLVE   ─► dedupe, fingerprint, │
                    │                  diff vs last run     │
                    │  9. RENDER    ─► annotated media,     │
                    │                  HTML report          │
                    └──────────────────────────────────────┘
                                    │
                    ┌───────────────┴──────────────┐
                    ▼                              ▼
            Postgres (issues,            Object store / disk
            projects, knowledge)          (artifacts, media)
```

**Stages 4–7 are the only ones that ever produce issues.** Stages 1–3 produce data. Keep that boundary clean; it is what makes the whole thing testable.

---

## 4. The run artifact

This is the contract everything else depends on. Get it right first.

```
runs/{run_id}/
  run.json                      # config, target, timings, status, git sha of checkers
  pages/
    {page_id}/
      page.json                 # url, path, title, status, redirect chain, depth, discovered_from
      dom.html                  # serialised post-hydration DOM
      console.json              # [{level, text, stack, url, line, ts}]
      network.json              # [{url, method, status, type, reqHeaders, resHeaders,
                                #   reqBody, resBodyHash, resBodySample, timing, size, initiator}]
      a11y.json                 # accessibility tree snapshot
      axe.json                  # raw axe-core results
      coverage.json             # CSS/JS byte coverage
      vitals.json               # LCP, CLS, TBT, TTFB, INP proxy
      viewports/
        desktop_1440/
          full.png              # full-page screenshot
          fold.png              # above-the-fold
          elements.json         # ◄── the important one
          layout.json           # derived groups: rows, grids, alignment sets, spacing histogram
        tablet_834/  ...
        mobile_390/  ...
  figma/
    file.json                   # raw node tree from Figma REST
    frames/{node_id}.png        # 2x exports
    tokens.json                 # derived palette, type scale, spacing scale, radii, shadows
  mapping/
    {page_id}.{viewport}.json   # [{figmaNodeId, elementId, score, method, unmatched?}]
  flows/
    {flow_id}/
      steps.json                # auto-logged human-readable steps
      trace.zip                 # Playwright trace
      video.webm
  api/
    endpoints.json              # deduped endpoints derived from network capture
    probes.json                 # probe results
  issues.json                   # neutral issue records
  media/{issue_id}/*.png        # annotated evidence
  report.html                   # self-contained
```

### 4.1 `elements.json` — the element record

Captured via a single `page.evaluate` walk. One entry per rendered element that is visible or has visible intent.

```jsonc
{
  "id": "el_00f31",
  "stableKey": "a3f1e9...",          // see §8.2 — survives DOM churn
  "selector": "main > section:nth-of-type(2) > div.card:nth-child(3)",
  "tag": "div",
  "role": "listitem",                 // computed ARIA role
  "classes": ["card", "card--featured"],
  "testId": "news-card",              // data-testid / data-cy / data-test if present
  "text": "Latest news",              // own text, normalised whitespace
  "textFull": "Latest news …",        // including descendants, truncated at 400
  "box":       { "x": 240, "y": 1180, "w": 320, "h": 410 },   // document coords
  "boxViewport": { "x": 240, "y": 180, "w": 320, "h": 410 },
  "visible": true,
  "occludedBy": null,                 // elementFromPoint at centre, if different
  "clickable": true,
  "focusable": true,
  "tabIndex": 0,
  "styles": {
    "color": "rgb(17,17,17)", "backgroundColor": "rgba(0,0,0,0)",
    "fontFamily": "Inter", "fontSize": 18, "fontWeight": 600,
    "lineHeight": 28, "letterSpacing": 0, "textTransform": "none",
    "textAlign": "left", "opacity": 1,
    "marginTop": 0, "marginRight": 0, "marginBottom": 24, "marginLeft": 0,
    "paddingTop": 24, "paddingRight": 24, "paddingBottom": 24, "paddingLeft": 24,
    "borderRadius": [12,12,12,12], "borderWidth": [1,1,1,1],
    "borderColor": "rgb(229,231,235)", "boxShadow": "0 1px 2px rgba(0,0,0,0.05)",
    "display": "flex", "flexDirection": "column", "gap": 16,
    "position": "static", "zIndex": "auto", "overflow": "visible"
  },
  "resolvedBackground": "rgb(255,255,255)",  // walks ancestors through transparency
  "contrast": 14.2,                          // against resolvedBackground
  "font": { "requested": "Inter", "rendered": "Inter", "fallbackUsed": false },
  "image": {
    "src": "…", "naturalW": 1600, "naturalH": 900,
    "renderedW": 320, "renderedH": 180, "bytes": 842113,
    "format": "jpeg", "loaded": true, "alt": "", "loading": "eager"
  },
  "link": { "href": "…", "resolved": "…", "target": "_blank", "rel": null, "external": false },
  "parentId": "el_00f22",
  "childIds": ["el_00f32", "el_00f33"],
  "domDepth": 7,
  "nearestHeading": "Latest news",
  "nearestLandmark": "main"
}
```

### 4.2 `layout.json` — derived structure

Computed immediately after capture, because every layout checker needs it and recomputing per checker is wasteful.

- **Alignment sets**: elements sharing a parent and near-identical `x` (or `y`), tolerance 1px. Any member deviating >1px from the set median is a candidate finding.
- **Repeated groups**: sibling elements with matching `stableKey` shape (same tag/class signature). This is what makes card grids, nav items, and listings checkable as a unit.
- **Spacing histogram**: all gaps between adjacent siblings across the page. A healthy site clusters on a scale (4/8/12/16/24/32…). Outliers are findings.
- **Type inventory**: every distinct `(fontFamily, fontSize, fontWeight, lineHeight)` tuple with usage counts. Sprawl here is a real defect.
- **Colour inventory**: every distinct colour with usage counts and nearest palette token.

---

## 5. Capture layer

**Driver abstraction.** Define `BrowserDriver` with `launch()`, `newContext()`, `newPage()`. Ship four implementations behind one config flag:

| Driver | Use |
|---|---|
| `playwright` | default |
| `patchright` | drop-in stealth Playwright, strips automation fingerprints |
| `camoufox` | hardened Firefox build, for harder bot detection |
| `remote` | Browserbase / Steel / self-hosted grid |

**Bot protection order of operations.** Documented in the UI, not just the code:
1. Ask the dev team for a WAF bypass rule on a secret header (`X-QA-Bypass: <token>`), or an IP allowlist, or point at staging. This is the only approach that stays working.
2. If that fails, headed mode + persistent profile clears a surprising amount of milder detection.
3. Only then, stealth driver.
4. If a page returns a challenge/interstitial, **abort the page and flag it loudly**. Never generate findings from a challenge page — a report you can't trust is worse than no report.

**Auth from day one.** Not a later feature. Support:
- Recorded login (Playwright `storageState` saved per project, refreshed when expired)
- Form credentials (encrypted at rest)
- Static cookie / header injection
- Basic auth
- Multiple personas per project (admin, standard user, logged-out) — needed for the IDOR probes in §9

**Crawl.** BFS from the seed. Respect `maxDepth`, `maxPages`, include/exclude regex, and a same-origin default. Discover via anchors, sitemap.xml, and any client-side router links. Deduplicate by normalised path with a configurable list of query params to ignore. Detect templated pages (`/blog/*`) and sample N rather than crawling 4,000 of them.

**Stability before snapshot.** Wait for `networkidle`, then fonts ready, then all in-viewport images decoded, then a settle delay. Scroll the full page to trigger lazy-loading, scroll back to top, freeze animations (`* { animation-play-state: paused !important; transition: none !important; }` injected only for screenshots), and mask known-volatile regions (clocks, carousels, ad slots) via per-project selectors.

**Retry.** Any page that errors is retried once with a fresh context. Any *finding* on a retried page that doesn't reproduce is dropped. Half of flaky findings vanish on the second attempt, and nothing kills trust faster than a report crying wolf.

---

## 6. Figma ingestion

Use the Figma REST API. Never screenshot-diff — you want "the button sits 4px low and the shade is off by one token", not a red blob.

- `GET /v1/files/{key}` → full node tree.
- `GET /v1/images/{key}?ids=…&scale=2` → frame exports for side-by-side evidence.
- Flatten to a comparable node record: absolute bounding box, fills, strokes, effects, corner radius, `characters`, `style` (font family/size/weight/lineHeight/letterSpacing), `layoutMode`, `itemSpacing`, padding, opacity, and `absoluteRenderBounds` (use this, not `absoluteBoundingBox`, when effects extend the visual edge).
- Normalise Figma colours (0–1 floats) to 8-bit RGB. Watch for `blendMode` and nested opacity.

**Token extraction** (`tokens.json`) — do this even when only a desktop frame exists. Derived tokens are what let you judge tablet and mobile with no design at all:
- Palette: every fill colour, clustered with a small ΔE threshold, ranked by usage.
- Type scale: distinct font size/weight/family/lineHeight combos, ranked.
- Spacing scale: distinct `itemSpacing` and padding values, ranked.
- Radii, stroke widths, shadow definitions.

**Frame → page mapping.** Offer three routes: automatic by frame name matching URL path, automatic by content similarity (heading text overlap), and manual assignment in the UI. Manual assignment is stored per project and reused on every future run.

---

## 7. The matching engine

This is the hard part and where most tools fall over. Budget accordingly.

Match Figma nodes to DOM elements with a scored, multi-signal approach. Never a single heuristic.

**Signals, combined into a weighted score:**

| Signal | Weight | Notes |
|---|---|---|
| Normalised text equality | 0.35 | Strongest signal by far. Case/whitespace/punctuation-insensitive. |
| Text similarity (Levenshtein ratio) | 0.20 | Catches copy tweaks. |
| Relative position within frame/page | 0.20 | Normalise both to 0–1 of their container. Scale-invariant. |
| Structural role (heading/button/image/input) | 0.10 | Figma layer name conventions + node type; DOM role. |
| Size ratio similarity | 0.10 | |
| Layer-name ↔ class/testid token overlap | 0.05 | Weak but free; helps with icons and images. |

**Algorithm:**
1. Scale-normalise. Figma frame width → live viewport width. Store the scale factor; all deltas are reported in *live* pixels after conversion.
2. Anchor pass: match all text nodes with unique exact text first. These become high-confidence anchors.
3. Constrain: use anchors to build a local coordinate transform. Subsequent matches are scored within the neighbourhood defined by anchors, which kills most false pairings.
4. Greedy assignment on remaining nodes, highest score first, one-to-one, threshold 0.55.
5. Anything unmatched → **candidate missing element** (in design, not on page) or **candidate extra element** (on page, not in design). These are reported as low-confidence and routed to the verifier agent, never straight to the report.

**Escape hatch.** Allow a per-project `figma-map.json` where a human pins `layerName → cssSelector`. Once pinned, always honoured. This turns a frustrating 20-minute triage into a permanent fix.

**Tolerances** — configurable per project, these defaults:

| Property | Default tolerance |
|---|---|
| Position | 2px |
| Size | 2px or 1% |
| Colour | ΔE ≤ 2.0 |
| Font size | 0.5px |
| Line height | 1px |
| Spacing/padding | 2px |
| Border radius | 1px |
| Opacity | 0.02 |

---

## 8. Checker framework

### 8.1 Interface

```python
class Checker(Protocol):
    id: str  # "layout.alignment"
    category: Category
    requires: set[Capability]  # {FIGMA} | {NETWORK} | {AUTH} | ...
    default_severity: Severity

    def run(self, ctx: RunContext) -> Iterable[Finding]: ...
```

A checker never touches the network or the browser. It reads the artifact. This makes the entire suite unit-testable against fixture artifacts, which you will be very glad of by phase 5.

### 8.2 Fingerprinting

The whole dismissal system depends on this. A fingerprint must **survive re-renders, content changes, and coordinate shifts**, and must **not** include anything volatile.

```
elementStableKey = sha1(
    tag +
    computedRole +
    normalisedOwnText[:60] +
    ancestorShape +          # tag.class chain, nth-child indices stripped
    nearestHeadingText[:40] +
    testId or ""
)

issueFingerprint = sha1(
    checkerId +
    pagePathNormalised +     # no query, no trailing slash
    viewportName +
    elementStableKey +
    issueKind                # e.g. "color-mismatch" — not the values
)
```

Note: the *values* are excluded. If a colour is wrong and stays wrong with a different wrong value, it's the same issue. Store `stableKeyV` alongside so you can migrate the hash later without losing everyone's dismissals.

### 8.3 Severity model

Assign automatically, always editable, and never let AI raise severity above `major` on its own.

| Severity | Meaning | Examples |
|---|---|---|
| `blocker` | Core journey impossible | Login broken, checkout 500s, page won't load |
| `critical` | Data or money or security wrong | Wrong total, exposed PII, IDOR, auth bypass |
| `major` | Feature broken or WCAG A/AA fail or content wrong on production | Form validation missing, contrast fail, duplicate listings, mobile horizontal scroll |
| `minor` | Visual/design deviation, single instance | 4px misalignment, off-token colour |
| `trivial` | Cosmetic, polish | Widow line, one-off shadow variance |

Auto-escalation rules: same finding on ≥5 pages → +1 severity. Anything on a checkout/auth/payment path → +1. Anything the AI layer flags but the verifier rejects → dropped, not downgraded.

### 8.4 Full checker catalogue

Every one of these runs on every run, at every viewport where applicable. Silence on pass.

**A. Free findings from capture**
Console errors · uncaught promise rejections · failed subresource requests (4xx/5xx) · mixed content · broken internal links · broken external links · broken images (`naturalWidth === 0`) · redirect chains and loops · 404 page returning 200 · duplicate or missing `<title>` / meta description · missing or wrong canonical · stray `noindex` on production · missing favicon/manifest · certificate and HSTS · security headers (CSP, X-Frame-Options, X-Content-Type-Options, Referrer-Policy) · cookie flags (Secure, HttpOnly, SameSite) · source maps exposed in production · `.env` / `.git` / debug route light probe.

**B. Layout**
Sibling alignment drift · inconsistent gaps within a repeated group · spacing off the derived scale · horizontal overflow of `<body>` · element overflowing its container · text clipped without ellipsis · overlapping interactive elements · clickable element occluded at its centre point · sticky header covering anchor targets · image aspect-ratio distortion · image upscaled beyond natural size · inconsistent radius/shadow/border within a repeated group · zero-height containers with padding · element present at desktop but absent at mobile.

**C. Typography and tokens**
Font size off the type scale · font family outside the allowed set · fallback font actually rendering (FOUT/FOIT detection) · weight outside allowed set · line-height ratio out of range · colour off palette (report nearest token + ΔE) · token sprawl (>N distinct sizes or colours on one page) · line length outside 45–95 characters for body copy.

**D. Content and copy**
Duplicate items in a listing (match on title hash, href, and image src independently) · empty or placeholder cards · lorem ipsum, `TODO`, `TBD`, `asdf`, `test test` · spelling and grammar with a per-project dictionary · inconsistent casing across nav or buttons · inconsistent terminology (`Sign in` vs `Log in` on the same site) · raw i18n keys leaking (`home.hero.title`) · mixed date/currency/number formats · mojibake and unescaped entities · dead-end pages with no outbound navigation.

**E. Accessibility**
Full axe-core ruleset · text and non-text contrast · missing/duplicate/decorative alt misuse · heading level skips · inputs without associated labels · missing focus indicator · full keyboard tab walk with trap detection · tap targets under 44×44 · invalid ARIA roles and attributes · missing landmarks · missing `lang` · `prefers-reduced-motion` respected · 200% zoom without content loss.

**F. Responsive**
Everything above, at `mobile_390`, `tablet_834`, `desktop_1440`, plus `desktop_1920` and `mobile_320` on demand. Plus: hamburger menu opens and is operable · tap targets not overlapping at mobile · tables handled (scroll container or stacked) · content parity across viewports · no viewport-specific console errors.

**G. Performance**
LCP, CLS, TBT, TTFB, INP proxy · total page weight budget · per-asset budget (default: no single image over 300KB) · images not in a modern format · render-blocking resources · unused CSS/JS ratio from coverage · below-fold images missing `loading="lazy"` · `font-display` missing · DOM node count · cache headers on static assets.

**H. Functional flows (Playwright)**
Every discovered link reachable · every form: required-field validation, inline error copy, invalid formats, 5,000-character strings, emoji and RTL text, `<script>alert(1)</script>`, `' OR 1=1 --` · login: valid, wrong password, unknown user, empty, rate-limit present, session persists on refresh, logout actually clears session · signup: duplicate email, weak password, mismatch confirmation · password reset end to end · search: no results state, special characters, pagination · filters and sort: combinations, reset, URL state survives refresh · cart and checkout: add, update, remove, quantity 0 and negative, valid/invalid/expired coupon, **line-item arithmetic verified independently** · pagination boundaries · browser back/forward mid-flow · deep-link and refresh mid-flow · two-tab session behaviour · file upload type and size limits.

**I. API probes** (derived free from network capture — you already know every endpoint the site called)
Replay with no auth · with an expired token · with *another persona's* token (IDOR — this is why multi-persona auth matters) · missing required fields · wrong types · boundary and huge payloads · method tampering · rate limiting present · error responses leaking stack traces or SQL · CORS wildcard with credentials · response schema drift vs the first captured run · slow endpoints over budget · PII in responses.

**J. Figma comparison** (when a design exists)
Position · size · text/background/border colour · font family, size, weight, line-height, letter-spacing · padding and margin · gap · border radius and width · shadow · opacity · text content mismatch · element in design but missing on page · element on page but absent from design · sibling order changed · image asset mismatch.

**K. AI layer** — see §9.

---

## 9. The AI layer

### 9.1 Two-tier economics

- **Sweep**: a cheap fast vision model over every page/viewport. Instructed to be *suspicious* and over-flag. Returns candidates only, no prose.
- **Analysis**: a strong vision model, only on flagged pages. Writes the title, description, and expected/actual.
- **Verify**: a strong model judging "is this actually a defect, given these measurements?" Judging is a far easier task than spotting, so the verifier is where your precision comes from and it's cheap.

Bias recall at the front, precision at the back. Budget lands around $1–2 for a 20-page site with five agents; parallelise pages so wall-clock stays sane.

### 9.2 Distinct mandates, not N copies

Running the same model five times gives you the same blind spots five times. Each agent gets a different lens, a different system prompt, and ideally a different model family:

| Agent | Mandate |
|---|---|
| `layout-critic` | Visual hierarchy, balance, rhythm, alignment the rules missed, awkward whitespace |
| `typography-critic` | Type hierarchy, widows and orphans, awkward wraps, measure, emphasis misuse |
| `copy-critic` | Tone consistency, clarity, jargon, CTA wording, error-message quality, placeholder text that reads plausible |
| `a11y-critic` | Things axe cannot see: link text that means nothing out of context, colour-only meaning, focus order that makes no sense |
| `impatient-customer` | Walks the site trying to complete the primary goal. Reports confusion, dead ends, unclear next steps, anything that would make a real user leave |
| `brand-critic` | *(Figma mode)* Does the built page feel like the design, beyond what the deltas caught |

### 9.3 Grounding contract

Every agent call receives:
1. Full-page screenshot at that viewport
2. The measured facts for that page: type inventory, colour inventory, spacing histogram, alignment sets, derived tokens
3. Any project knowledge entries relevant to the page (§10)
4. A hard instruction: *report only what you can point at with a bounding box; if you cannot localise it, do not report it*

Every agent returns strict JSON: `{box, kind, title, description, confidence}`. No prose outside JSON. Parse defensively and strip fences.

### 9.4 Verifier

Input: the candidate finding, the cropped region at 2x, the measured facts for that element, and the project knowledge. Output: `{verdict: confirm|reject|downgrade, reasoning, severity}`. Rejected candidates are dropped silently and logged for calibration, never shown.

Track sweep→verify confirm rate per agent per project. If an agent's confirm rate falls below ~20%, its prompt needs work — surface that in an internal dashboard rather than quietly wasting money.

---

## 10. Project knowledge store

The feature that makes people trust it. Clients change things verbally, and the Figma never gets updated.

**Input surfaces:**
- A free-text box on the run form: *"Paste anything you were told about this project."*
- Developer comments on issues in the board (§12).
- Prior dismissals with a reason.

**Processing.** A model converts free text into structured entries, which the user **reviews and confirms before the run starts**. Never silently accept parsed intent.

```jsonc
{
  "id": "kn_014",
  "raw": "client asked for the CTA to be green not blue, and the testimonials section is removed for now",
  "entries": [
    { "kind": "override", "scope": "selector:.btn-primary", "property": "backgroundColor",
      "expected": "#1DB954", "note": "client request, not in Figma", "assert": true },
    { "kind": "removal", "scope": "figma:Testimonials", "note": "deferred", "assert": true }
  ],
  "createdBy": "joujou", "createdAt": "…", "confirmed": true
}
```

**Each entry does double duty.** It suppresses the false positive *and* becomes its own assertion. The report then says either "Requested change confirmed present: CTA is green" or, far more usefully, "Requested change NOT applied: CTA is still blue." That second line is often the most valuable thing in the whole report.

Entries persist per project and accumulate into a running changelog with dates and authors — so nobody retypes them, and six months later you can see when a decision was made and who relayed it.

---

## 11. Issue lifecycle and run diffing

```
new ──► confirmed ──► fixed          (verified absent on a later run)
  │            │
  ├──► dismissed  (intentional; never shown again, any run, forever)
  └──► wont_fix   (real but accepted; hidden by default, counted in totals)
```

**Grouping.** Ten cards with the same wrong background is **one issue with ten instances**, not ten findings. Group by `(checkerId, issueKind, expected, actual)` within a page, and again across pages when the same repeated component is at fault. Report the count; list instances in an expandable.

**Run diff.** Every run after the first is presented as **New / Still open / Fixed / Regressed**, defaulting to New. Anything previously dismissed is filtered before the report is even rendered. A "regressed" label (fixed, then returned) is worth calling out loudly — those are the ones that embarrass teams.

**Sorting.** Severity first, then instance count, then page depth. The broken login is never below a shadow variance.

---

## 12. Reporting

### 12.1 The HTML report

Single self-contained file. Images inline as base64 or a sibling `media/` folder if size demands. Not PDF — people need to click into traces and video.

Structure:
- **Header**: target, run time, duration, viewport set, Figma file (if any), pass/fail counts by severity, diff summary vs previous run.
- **Body**: issues grouped by page, sorted by severity. Each issue is a card:
  - Title, severity chip, source badge (`measured` / `ai` / `verified`), confidence if AI
  - Description, expected vs actual
  - Evidence: annotated screenshot; side-by-side live vs Figma when applicable
  - Reproduction steps
  - Trace/video link for functional issues
  - Selector and instance list
- **Appendix**: everything that passed, collapsed. People need to see what *was* checked to trust what wasn't flagged.

### 12.2 Annotation

Nearly free — you already have bounding boxes on both sides.

- **Live**: inject an absolutely-positioned overlay `<div>` with a 3px outline plus a numbered label, then screenshot. Falls back to drawing with Pillow on the captured PNG when injection isn't possible.
- **Figma**: draw the same box on the exported frame image using the scale factor from §7.
- **Side by side**: composite the two crops with a shared label, matched heights, a 24px gutter, and small captions (`Live` / `Design`). Crop to the region plus 15% context — a full-page screenshot with a tiny circle is useless.
- Colour code by severity. Number annotations to match the issue list.

### 12.3 Reproduction steps

Never write these by hand. Every action in every flow goes through a logging wrapper:

```python
async def step(self, description: str, fn):
    self.log.append(
        {"n": len(self.log) + 1, "text": description, "ts": now(), "url": self.page.url}
    )
    await self.page.screenshot(path=f"{self.dir}/step_{len(self.log)}.png")
    return await fn()


await ctx.step("Click 'Sign in'", lambda: page.click("text=Sign in"))
await ctx.step("Enter an invalid email", lambda: page.fill("#email", "not-an-email"))
```

When something fails you already have the exact step list, a screenshot per step, plus Playwright's trace and video. Attach all of it.

---

## 13. Built-in task board

Findings should arrive as tasks already written. That's the value — not the board itself.

**Deliberately lightweight.** Do not build a Jira clone. It is a *view over the issue records* plus:
- Kanban columns per project (New / Confirmed / In progress / Fixed / Dismissed)
- Assignee, due date, labels
- **Comments** — the key feature. A developer opens the board, comments *"this was changed by the client, ignore"*, and that comment is written back into the project knowledge store (§10). The next run already knows. The loop closes itself.
- Full issue detail: evidence, repro, trace, history across runs

Anyone with a project link can comment without a full account — developers will not sign up for another tool.

---

## 14. Export adapters

One neutral issue format, thin adapters on top. Never write a bespoke integration.

```python
class IssueExporter(Protocol):
    def map(self, issue: Issue) -> dict: ...
    def push(self, payloads: list[dict]) -> list[ExportResult]: ...
```

Ship: **Jira** (REST v3, ADF description, attachments), **OpenProject** (API v3), **Azure DevOps**, **Linear**, **GitHub Issues**, **CSV**, **Markdown**. Field mapping (severity → priority, category → labels) is configurable per project. Store the remote key on the issue so a second export updates rather than duplicates.

---

## 15. Scheduling, CI, notifications

- **Cron per project**: hourly through monthly, plus manual. Store as a crontab expression with a timezone.
- **Digest**: notify **only when something new or regressed appears**. A scheduled run that finds nothing new sends nothing. People mute noisy tools within a fortnight.
- **Channels**: email, Slack, webhook.
- **CI hook**: `POST /api/runs` with a target URL and a `baseRunId`. Returns new-issue counts by severity and a report URL. Exit non-zero above a configurable threshold so it can gate a deploy. Ideal for staging on every merge.
- **Record-a-flow**: `playwright codegen` wrapped in the UI. The user clicks through a journey once; it's saved as a named regression test with auto-generated step descriptions and runs on every future run forever.

---

## 16. UI design direction

**Reference point: Linear.** Dense, muted, confident. Screenshots are the hero — the interface gets out of their way.

**Explicitly forbidden:** neon or acid accents, purple-to-pink gradients, glassmorphism, glow effects, floating 3D blobs, emoji in UI chrome, oversized rounded corners, centred marketing-style hero layouts inside the app. These are the tells of AI-generated design and this tool is going to be seen by other QA engineers, who are professionally paid to notice details.

**Tokens:**

```
Surface        #0E0F11   base           (dark default; light theme mirrors it)
Surface raised #17181B
Border         #26282D   hairline, 1px, used liberally
Text primary   #E6E7EA
Text secondary #9296A0
Text tertiary  #6A6E78
Accent         #3B7DD8   restrained blue — used for focus, links, active state only
Severity:      blocker #C7443A · critical #D2683C · major #C99A2E
               minor  #5C7FA8 · trivial #6A6E78
```

- **Type**: Inter (or Geist) for UI at 13/14px base with a tight scale; JetBrains Mono for selectors, hex values, and measurements. Numbers tabular-aligned everywhere — this is a tool full of measurements and misaligned digits look terrible.
- **Density**: 32px row height, 8px grid, generous horizontal padding and tight vertical. Assume power users on large monitors.
- **Layout**: persistent left rail (projects), centre list (issues), right detail pane. Keyboard-first: `j`/`k` to move, `x` to dismiss, `c` to comment, `⌘K` command palette.
- **Motion**: 120–160ms ease-out on state changes only. Nothing decorative. Respect `prefers-reduced-motion`.
- **The one signature element**: the evidence viewer. A slider-wipe comparison between live and design, with the measured deltas overlaid as thin annotated leader lines on hover — like a technical drawing rather than a photo filter. Spend the design budget here and keep everything around it quiet.
- **Progress**: runs take minutes. Stream page-by-page progress over SSE with a live-updating list of pages as they're checked, and issues appearing as they're found. Never a spinner. This is much harder to retrofit, so build it in from the first UI commit.
- **Quality floor, unannounced**: responsive to mobile, visible keyboard focus, WCAG AA contrast on your own UI. A QA tool that fails its own accessibility checks is indefensible.

---

## 17. Stack

| Layer | Choice | Why |
|---|---|---|
| Worker | Python 3.12, Playwright, Pillow, axe-core, `pyspellchecker`/LanguageTool | Playwright and image work live happily together here |
| API | FastAPI + SQLModel, SSE for progress | Async, typed, trivial streaming |
| Queue | Redis + RQ (Celery if it outgrows it) | Runs are long; never block a request |
| DB | Postgres | JSONB for issue payloads, real queries for the board |
| Storage | Local disk, S3-compatible interface from day one | Artifacts get large |
| UI | Next.js (App Router) + Tailwind + shadcn/ui + TanStack Query | Polished without designing from scratch |
| Models | Provider-agnostic wrapper; cheap tier + strong tier configurable | Never hardcode a model name |
| Packaging | Docker Compose: api, worker, redis, postgres, web | One command to run |

---

## 18. Repo layout

```
bureau/
  apps/
    web/                     # Next.js
    api/                     # FastAPI
  packages/
    engine/
      capture/               # driver, crawler, snapshot, auth, stability
      figma/                 # client, normalise, tokens, export
      matching/              # scoring, anchors, assignment
      checkers/
        free/ layout/ typography/ content/ a11y/ responsive/
        performance/ functional/ api/ figma/
      agents/                # sweep, mandates, verifier, prompts/
      issues/                # fingerprint, group, severity, diff
      report/                # annotate, compose, html
      exporters/
      artifact/              # schema, read/write, validation
  fixtures/                  # frozen run artifacts for checker unit tests
  docker-compose.yml
  SPEC.md
```

---

## 19. Phased build

Each phase ends in something runnable. Do not begin the next until the previous is demonstrably working.

| Phase | Deliverable | Done when |
|---|---|---|
| **0** | Repo, Docker Compose, artifact schema + validator, fixture loader | `pytest` green on an empty checker suite; schema round-trips |
| **1** | Capture: driver abstraction, crawler, auth, multi-viewport snapshot, `elements.json`, `layout.json` | CLI: `bureau capture <url>` produces a valid artifact for a real site |
| **2** | Checker framework + categories A, B, C, D, E, F, G | CLI: `bureau check <run>` emits `issues.json` with real findings |
| **3** | Annotation + self-contained HTML report + fingerprinting + grouping | A shareable report file a human would actually read |
| **4** | Figma ingest, token extraction, matching engine, category J | Design deltas reported with correct side-by-side evidence |
| **5** | Agent layer: sweep, mandates, verifier, grounding, calibration log | AI findings appear, labelled, with confirm-rate tracked |
| **6** | Functional flows with step logging + API probe engine (H, I) | Trace and video attached to a real login failure |
| **7** | API + UI: projects, runs, SSE progress, issue list, evidence viewer | Full run triggerable and watchable from the browser |
| **8** | Issue lifecycle, run diff, board with comments, project knowledge store | Dismissal survives a re-run; a comment changes the next run |
| **9** | Export adapters + scheduling + digest + CI endpoint + record-a-flow | Issues land in Jira; a cron run notifies only on new |
| **10** | Hardening: stealth drivers, retry, flake suppression, volatile masking | Two consecutive runs on an unchanged site produce identical issue sets |
| **11** | *(Later)* Mobile: Appium driver, mitmproxy capture, same artifact format | Android run produces an artifact the existing checkers consume |

**Mobile note for phase 11:** design comparison actually gets *easier* — mobile frames are fixed width, so no responsive guesswork. Appium's accessibility tree maps closely onto `elements.json`, so most deterministic checkers port over unchanged. The two real costs are network capture (needs mitmproxy in the middle, not free like CDP) and iOS generally (Mac, simulators, signing). Ship web fully first.

---

## 20. Definition of done for v1

- Point it at a URL with no Figma and no credentials → get a real, useful, triaged report.
- Point it at a URL with a Figma file and a login → get design deltas, functional failures, and API findings in one report.
- Dismiss fifteen issues, re-run, see zero of them again.
- A developer comments on an issue; the next run accounts for it.
- Two consecutive runs on an unchanged site produce byte-identical issue sets, for every
  checker that reads only the artifact. **Web vitals are excluded and are the only
  exclusion.** They are measured from the world rather than derived from the capture, and
  an unchanged site genuinely does load differently twice — on one real site LCP moved
  850ms and CLS moved 0.09 between consecutive runs, enough to walk findings across their
  budgets. They are sampled `vitalsSamples` times (default 3), reported as a median with
  the observed range, and only ever reported when the *whole* range is past budget, which
  removes the straddling case without pretending the measurement is stable. The exclusion
  is declared in code — `checkers.base.non_deterministic()` — and `tests/test_hardening.py`
  asserts its exact contents, so nothing joins it quietly.
- The whole thing runs from one `docker compose up`.
