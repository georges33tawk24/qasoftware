# Planted flow and API defects

The expected-results file for catalogue groups H and I, asserted by `tests/test_flows.py`.
Same contract as the other two: every row must be found, and nothing outside these rows
may be reported.

The defects live in `tests/fixtures/app.py`, a small stateful application with a session
store in a dictionary and no framework. `tests/fixtures/site/` is static, which is enough
for capture and the deterministic sweep but cannot fail a login.

`fixtures/exercised` is a frozen run of that application, so the group H and I checkers
are tested without a browser and without a server. Rebuild it with
`.venv/bin/python -m tests.build_flow_fixture`. Passing flows are dropped from the frozen
copy and step screenshots are downscaled; one flow keeps its real `trace.zip`.

## Functional flows — group H

| Checker | Issue kind | Planted as |
|---|---|---|
| `functional.flows` | `logout-does-not-invalidate-session` | signing out clears the browser cookie and never invalidates the session server-side, so the old token still works |
| `functional.flows` | `total-does-not-match-line-items` | the cart shows £27.00 where 2×£12.50 + 1×£4.00 is £29.00 |
| `functional.flows` | `unescaped-input-reflected` | the contact form's confirmation reflects the submitted name without escaping it |
| `functional.flows` | `submitted-markup-executed` | and therefore runs it |

## API probes — group I

| Checker | Issue kind | Planted as |
|---|---|---|
| `api.probes` | `api-cross-persona` | `/api/items` and `/api/orders` answer any token with the same data, so one account reads another's |
| `api.probes` | `api-personal-data` | an anonymous request to `/api/items` returns an email address |
| `api.probes` | `api-method-tampering` | `DELETE /api/items` is accepted on a read-only endpoint |
| `api.probes` | `api-malformed-input` | `/api/orders?page=abc` returns a 500 with a stack trace in the body |
| `api.probes` | `api-rate-limit` | the login and the API accept a burst of identical requests unrefused |
| `api.probes` | `api-cors` | `/api/me` sends `Access-Control-Allow-Origin: *` together with credentials |

## Deliberately *not* planted

The sign-in form shows a different message for an unknown email than for a wrong
password, which is a real weakness — but SPEC §8.4 H does not list it and inventing
catalogue entries is how a checker suite drifts. Recorded here so the next person knows
it was a decision rather than an oversight.

`/api/me?email=` will hand any valid token any account, and the probe engine does **not**
find it. Replaying a captured request with another persona's token is authorisation
checking; editing that request's parameters until something gives is not, and the line
between the two is the whole of the scope discipline this phase was built under. Finding
it would need a human to say "try tampering with this parameter", which is what the
record-a-flow feature in phase 9 is for.

## Must not fire

| Rule | Why it fired | Why it is wrong |
|---|---|---|
| `no-error-message` on a refused sign-in | the check looked for words like "error" or "invalid" in the page text, and "No account with that email address." contains none of them | an error is an error *element* — `[role=alert]`, `.error`, `[aria-invalid]` — not a word we happened to list |
| every form flow twice | shared journeys were built once per persona, so the contact form was exercised once as each | only the auth journeys depend on who is signed in |
| `unescaped-input-reflected` never firing | an invalid `type=email` in the same submission made the browser refuse to submit at all, silently skipping every other check in the run | format probes get their own flow; the escaping flow fills every typed field with something valid |
| `malformed-input` returning 400 on every endpoint | the probe sent a JSON body with a `GET`, which most servers leave unread and then reinterpret as the next request on the same keep-alive connection | a `GET` is tampered with in its query string and never given a body |

## Scope

Every probe replays a request the site already made during the crawl, changing one thing
about it. Nothing is fuzzed beyond malformed-input handling, nothing attempts to extract
data, and no request is sent to a host outside `authorisedHosts`. A run without an
`authorisedBy` is crawled and checked but never probed — `api.not-probed` says so out
loud, because silence about a check that did not run reads exactly like a check that
passed.
