# When the site fights back

Read this in order. It is written the way it is because the order matters more than any
of the individual options: **an allowlist keeps working and evasion breaks every few
weeks.** A stealth driver that clears Cloudflare today is a support ticket next month.

## 1. Ask for a bypass — the only approach that stays working

In descending order of how easy they are to get:

- **A secret header on a WAF bypass rule.** `X-QA-Bypass: <token>`, scoped to the QA
  token and nothing else. Configure it as a persona header:

  ```json
  {"headers": {"X-QA-Bypass": "env:CLIENT_WAF_TOKEN"}}
  ```

  The value is a *reference*. The token itself lives in the environment or the keychain
  and never reaches project config, an artifact, or a log.

- **An IP allowlist** for wherever the worker runs.
- **Point at staging.** A pre-production environment usually has no bot protection at
  all, and testing the thing you are about to deploy is better than testing the thing you
  deployed last week.

Any of these takes one conversation with the client's platform team. All of them survive
the vendor's next rule update. Nothing below does.

## 2. Headed mode with a persistent profile

```json
{"driver": "playwright_headed"}
```

Headless is the single loudest signal a browser emits, and a warm profile with cookies
and history clears a surprising amount of milder detection. Needs a display, so a CI
runner wants `xvfb-run`. Try this before any stealth driver: it is the same browser,
honestly configured, and it does not break when a fingerprinting script is updated.

## 3. Only then, a stealth driver

```bash
pip install "bureau-engine[stealth]"
patchright install chromium   # for the patchright driver
camoufox fetch                # for the camoufox driver
```

| Driver | What it is | When |
|---|---|---|
| `patchright` | A fork of Playwright with the automation fingerprints removed | First thing to try. Same browser, same API |
| `camoufox` | A hardened Firefox build with a consistent synthetic fingerprint | When patchright is detected |
| `remote` | Browserbase, Steel, browserless, or a self-hosted grid | When residential IPs or a managed solver are the actual blocker |

The remote driver takes its endpoint from the environment, because the URL usually
carries the API key:

```bash
export BUREAU_REMOTE_WS="wss://connect.browserbase.com?apiKey=…"
export BUREAU_REMOTE_KIND=cdp    # or `ws` for a Playwright server
```

### What we will not do

No CAPTCHA solving, no residential proxy rotation to defeat rate limits, and no attempt
to defeat a challenge the site meant for us. If a page returns a challenge or an
interstitial, the run **abandons that page and says so loudly** — a report built from a
challenge page is worse than no report, because someone will believe it.

## 4. When a page is blocked anyway

The run continues, the page is recorded as `crawlBlocked`, and both the report header and
the run summary say how many pages were skipped. Nothing is checked from a page that did
not load properly, and no finding is ever generated from a challenge screen.
