# CLAUDE.md — standing rules for this codebase

`SPEC.md` is the source of truth. When this file and the spec disagree, the spec wins.
When this file and a prompt disagree, this file wins — the prompt is a session, this is
the codebase.

## The seven principles (SPEC §1)

1. **Capture once, check many.** One crawl produces one immutable run artifact. Every
   checker is a pure function over that artifact. Nothing re-crawls.
2. **Measure before you reason.** Anything expressible as arithmetic is arithmetic. The
   model is the last resort, never the first.
3. **Ground the model.** A model call gets the screenshot *plus* the measured numbers.
   Never the screenshot alone.
4. **Exhaustive by default.** No "enable performance checks" toggle. Every run does the
   full sweep. Clean checks stay silent.
5. **Over-report at the front, filter at the back.** High recall in detection, high
   precision in verification.
6. **One core, pluggable edges.** Checkers are modules, drivers are swappable, exporters
   are thin adapters over one neutral issue format.
7. **Dismissals are permanent.** An issue dismissed once never reappears. Ever.

## Hard rules

- **Checkers are pure functions over a run artifact.** They never touch the network or a
  browser. If a checker needs data it doesn't have, the fix is to capture more in the
  capture layer — never to fetch it at check time. A checker that opens a browser is a
  bug, not a shortcut.
- **Never hardcode a model name outside the provider config.** No `claude-…`, `gpt-…`,
  `gemini-…` string at a call site. Tier in, model out.
- **Never hardcode the product name outside `branding`.** Python: `engine.branding`.
  Web: `config/branding.ts`. The codename is disposable; the code shouldn't know it.
- **No secrets anywhere durable.** Not in the artifact, not in project JSON, not in
  traces, not in HAR, not in logs. Credentials come from env or the OS keychain, and are
  redacted on the way into anything that gets written to disk.
- **Every new checker ships with a fixture-based unit test in the same commit.** No
  exceptions. The test asserts on a frozen artifact under `fixtures/`, never a live site.
- **Prefer arithmetic to a model.** If a thing can be measured, measure it. A three-pixel
  misalignment is a subtraction problem.
- **Silence on pass.** A checker that finds nothing emits nothing. No "0 issues" records.
- **Colour comparison is CIELAB ΔE**, never raw RGB distance. Default threshold ΔE > 2.0.
- **Derive scales from the page**, don't hardcode 4px/8px. Spacing scale and type scale
  come from the page's own histogram; outliers are flagged against that.

## Stage boundary

Capture / ingest / match produce **data**. Check / exercise / reason / verify produce
**issues**. Nothing in the first group emits a `Finding`. Keep that line clean — it is
what makes the whole thing testable.

## Artifact conventions

- Artifact JSON is **camelCase**, and the Pydantic models use camelCase attributes so
  that a `model_dump()` is the on-disk shape with no alias flags to forget.
- The artifact is immutable once written. Re-running checkers over an old run must be
  possible and must give the same answer.
- Paths inside the artifact are relative to the run directory, always POSIX separators.
- Only the computed-style properties listed in SPEC §4.1 are captured. Never dump full
  computed style — it is ~340 properties per element and makes artifacts unusable.

## Fingerprints

`elementStableKey` and `issueFingerprint` (SPEC §8.2) decide whether dismissals survive.
Nothing volatile goes in: no coordinates, no nth-child indices, no measured values, no
timestamps, no run id. Changing the recipe requires bumping `STABLE_KEY_VERSION` and a
migration, because every existing dismissal hangs off it.

## Design comparison

- **A wrong match is worse than no match.** Below the confidence threshold a pair is
  `unmatched` and reported as a *possible* missing or extra element at low severity —
  never as a property diff. One wrong match produces a page of nonsense findings.
- **Never lower the matching threshold to make findings appear.** When deltas are
  missing, read `design/matching.json` and the per-surface `mapping/*.json`, find which
  signal is misleading the assignment, and fix that.
- **Every match decision is logged** with its per-signal scores. If a match cannot be
  explained from those files, the engine is missing a field, not the reader.
- **Deltas are reported in live pixels**, converted with the frame-to-viewport scale
  factor, and measured *within the matched container* so one shifted section cannot
  cascade into a finding on every element it contains.
- **Design tokens are authoritative where they exist.** They are extracted whether or not
  a frame is mapped, because their job is to let a viewport with no design frame be
  judged at all. Where a frame did match, group J measures that surface and the
  token-derived scale checkers stand aside rather than saying the same thing twice.

## The agent layer

- **Never hardcode a model name at a call site.** A call site names a *tier*
  (`cheap`, `strong`, `verify`); `agents/config.py` decides what that means. A model with
  no verified price does not go in the catalogue — a wrong price silently mis-reports
  every run's spend, and the ceiling is the only thing between a large site and a large
  bill.
- **The grounding contract is enforced in code, not hoped for in a prompt.** A
  `Grounding` will not construct without the screenshot and the measured facts, and the
  two hard instructions cannot be edited out of it. There is no code path that reaches a
  model with a picture and no numbers.
- **Agents never report anything measurable.** Spacing, colour, size, alignment and
  contrast are arithmetic and already checked. A model guessing at a pixel value is how
  this product loses its credibility.
- **A finding that cannot be localised does not exist.** No bounding box, no candidate.
- **Rejected candidates are dropped silently and logged for calibration**, never shown to
  the user, and never "downgraded to trivial" as a consolation.
- **The AI never raises severity above `major` on its own** (SPEC §8.3), and never
  overwrites a human's severity.
- **Different agents get different facts.** Handing every mandate the same measurements
  produces one agent's opinion five times; `mandates.distinct()` asserts it and a test
  fails if two ever converge.
- **Never silently overspend.** On a ceiling breach the run stops, returns what it has,
  and says so loudly.

## Flows and API probes

- **Every action goes through the step wrapper.** Reproduction steps are never written by
  hand; they fall out of the log. A flow that reaches for `flow.page` directly is a flow
  whose failure arrives with a gap in its instructions.
- **A step describes the action, never the value.** A filled password must not appear in
  a step list, and no field value is captured into the artifact at all — only the field's
  contract.
- **Flows retry twice before becoming an Issue** (SPEC §5). Roughly half of flaky findings
  vanish on the second attempt, and nothing destroys trust faster than crying wolf. A flow
  that only passed on a retry is reported quietly rather than silently.
- **Verify arithmetic independently.** A displayed total is a claim. Do the sum.
- **Nothing is probed without an `authorisedBy` and a host allowlist.** This is regression
  testing of a system the user is contracted to test, with credentials they own. No
  payload fuzzing beyond malformed-input handling, no attempt to extract data, and no
  request to a host outside the list. A run without authorisation is crawled and checked,
  and says out loud that it was not probed.
- **Replaying a captured request with another persona's token is authorisation checking.
  Editing that request's parameters until something gives is not.** That line is the whole
  of the scope discipline; when a probe needs to cross it, a human asks for it explicitly.
- **Probes record what happened; checkers decide what it means.** Severity judgements live
  in the checker, which keeps groups H and I pure functions over the artifact like every
  other group.

## Control plane and UI

- **The index is an index.** `apps/api` stores what the board needs to query — state,
  assignee, comments, counts — and the run artifact stays the source of truth for
  everything else. A field copied into the database is a field that can disagree with the
  evidence.
- **A dismissal outlives its run.** `dismissed` and `wont_fix` carry across re-indexing by
  fingerprint; every other state is recomputed from the new artifact.
- **The terminal event goes last.** A browser told a run is `done` immediately asks for
  the issues, so `done` is published after they are indexed and queryable, not when the
  engine stops. `RunRequest.terminal=False` is how the worker takes that responsibility.
- **Never a spinner** (SPEC §16). Pages and issues stream over SSE as they are found, and
  the stream reconnects with `after=` so a dropped connection loses nothing.
- **The event stream is not proxied.** A rewrite proxy buffers `text/event-stream` and
  delivers the whole run in one lump at the end. The browser calls the API origin
  directly — one origin for fetches, events and evidence images.
- **We pass our own checks.** `make dogfood` runs the a11y, layout and typography sweep
  against our own UI and fails the build on anything `major` or worse. When it fires, the
  fix is ours: either the interface is wrong, or the checker has a false-positive class
  worth killing for every client too.
- **Never let escalation invent a `blocker`.** Page counts and URL patterns cannot tell a
  broken journey from a widespread cosmetic problem, so `escalate` is capped at
  `critical`. A checker that ran the journey and watched it fail sets `blocker` itself.

## Knowledge, lifecycle and the board

- **Nothing parsed is ever applied.** A note becomes entries, entries are shown back, a
  human confirms. `apply` ignores an unconfirmed note entirely, and no code path sets
  `confirmed` except the one a person clicks. A guess that silences findings is worse
  than no knowledge at all.
- **Every entry does double duty** (SPEC §10): it suppresses what it explains *and* gets
  checked in its own right. "Requested change NOT applied" is the line people read the
  report for, so an entry that cannot be checked says `unverifiable` rather than
  pretending. An `ignore` never claims to have been confirmed.
- **Suppression is scope *and* property.** The `EXPLAINS` table decides which checkers an
  override can account for. "The CTA is green now" must never also hide "the CTA is too
  small to tap", so widening that table is a decision, not a tweak.
- **Knowledge is applied after checking, never during.** Checkers stay pure functions
  over the artifact; `knowledge.json` and `diff.json` are written beside `issues.json` so
  re-reading an old run gives the same answer.
- **The engine can diff two runs; only the index remembers history.** `regressed` means
  fixed once and back again, which no pair of artifacts can know, so the caller passes
  the fingerprints it has seen fixed.
- **A comment is a draft, not a rule.** SPEC §13's loop ends at a knowledge draft; §10
  still wants someone to confirm it. Same for a dismissal reason, and only when the
  person ticked the box — "duplicate of the one above" is not project knowledge.
- **The board is a view, not a tracker.** Columns, assignee, labels, due date, comments.
  No sprints, no epics, no burndown. The value is that the cards arrive already written.

## Exports, scheduling and CI

- **Adding a tracker is one new file under `exporters/` and no change to anything else.**
  If a tracker needs a special case in `base.py`, the abstraction is wrong and the fix is
  in `base.py` — not a branch in the adapter.
- **A credential is named, never stored.** `Target.token_env` and `Channel.url_env` hold
  the *name* of an environment variable. A Slack webhook URL is itself a credential.
- **A second export updates, never duplicates.** The remote key lives on the issue and is
  handed to the adapter, so no adapter has to search a tracker for its own past work.
- **A ticket arrives with a picture.** Flows carry their step screenshots and trace; a
  measured finding gets the annotated crop the report would have shown. A ticket with
  only a selector gets argued about.
- **A run that finds nothing new sends nothing.** `digest.worth_sending` is the only
  place that decides, so a new channel cannot get it wrong. The exception is a first run,
  which has no baseline and where the whole list is the point.
- **A missed schedule fires once, not once per window missed**, and never while a run for
  that project is already in flight.
- **A recorded journey is stored as steps, never as generated code.** Every action still
  goes through the step wrapper, so a failure arrives with its reproduction. A recorded
  password is a reference to the persona, resolved at replay and never written down.

## Hardening

- **Two runs on an unchanged site produce byte-identical issue sets.** SPEC §20's line,
  and `tests/test_hardening.py` runs the fixture app twice with flows and probes on to
  prove it. Anything that makes a finding wobble on its own — an unmasked timestamp, a
  value in a fingerprint, a threshold straddled by timing — is a bug in this product,
  not a fact about the web.
- **An allowlist keeps working; evasion breaks every few weeks.** `docs/bot-protection.md`
  leads with the WAF bypass header and staging, then headed mode, then stealth. Keep that
  order in the docs and in anything the UI says. No CAPTCHA solving, ever, and a
  challenge page is abandoned rather than checked.
- **A stealth driver is an optional extra, not a stub.** It is implemented; the browser it
  drives is installed with `pip install bureau-engine[stealth]`, and the error says so.
- **Flaky is a property of history, so only the index can see it.** Present, absent,
  present is `flaky` forever after: grouped apart, never counted as a regression, never
  called fixed, and never in a digest. A regression is something a person did.
- **Visual regression is SSIM plus a structural element diff, never a raw pixel diff.**
  A raw diff fires on an antialiased glyph, gets switched off in a week, and then it is
  protecting nothing. Masked regions are blanked on *both* sides at compare time, so
  adding a mask fixes old runs too.
- **Issues are kept forever; screenshots are not.** Pruning strips media beyond the last
  N runs and leaves every measurement, so an old run stays readable, re-checkable and
  diffable.

## Stubs

A `NotImplementedError` is acceptable only when it belongs to a later phase, and it must
say which:

```python
raise NotImplementedError("phase 10: camoufox driver")
```

Anything the current phase is meant to deliver gets implemented for real.

## Style

- Python 3.12, `ruff` and `mypy --strict` both clean. No `# type: ignore` without a reason
  on the same line.
- Boring over clever. The person reading this at 3am is on call.
- No abstraction with one implementation, no config for a value that never changes, no
  scaffolding "for later".
- Deliberate simplifications get a `# ponytail:` comment naming the ceiling and the
  upgrade path.
