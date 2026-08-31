# Planted defects

This is the expected-results file for the checker suite, and `tests/test_checkers.py`
asserts against it. Every row is a defect deliberately planted in `broken/`, or a
deliberate property of the fixture server in `tests/serve_broken.py`.

**Two assertions run against this file.** Every row must be found, and nothing outside
these rows may be reported. That second half is the one that matters: it is what stops
the suite drifting into noise as it grows.

When a real false positive turns up, it goes in **Must not fire** with the measurement
that caused it. When a checker legitimately starts finding something new here, add the
row — but only after checking the fixture really does contain that defect.

The clean pages (`index.html`, `about.html`, `contact.html`) are the capture fixture and
are not covered by this file.

## Layout — group B

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `layout.alignment` | `misaligned-x` | index | `.stack > .row:nth-child(3)` is indented 5px |
| `layout.group-gaps` | `uneven-gap-in-group` | index | `.stack > .row:nth-child(4)` uses a 40px gap where the group uses 24px |
| `layout.group-consistency` | `inconsistent-radius` | index | `.tiles > .tile:nth-child(3)` has a 24px radius, the rest 8px |
| `layout.spacing-scale` | `off-spacing-scale` | index, a11y | the 40px gap, and the unstyled form's 21.5px gap |
| `layout.clipped` | `text-clipped` | index | `.clip` is 120px wide, `overflow: hidden`, no `text-overflow` |
| `layout.zero-height` | `zero-height-with-padding` | index | `.collapsed` has `height: 0` and 12px of vertical padding |
| `layout.horizontal-overflow` | `body-horizontal-scroll` | index, mobile | `heavy.png` at 700px and `.too-wide` at 620px, in a 390px viewport |
| `layout.container-overflow` | `overflows-container` | index, mobile | the same two elements against their own containers |
| `layout.image-geometry` | `image-aspect-distorted` | index | `square.png` is 300×300, rendered 300×120 |
| `layout.overlapping-clickables` | `overlapping-clickables` | deep | `.under` and `.over` buttons overlap by 30×30px, clear of either centre |
| `layout.occluded-clickable` | `occluded-clickable` | deep | `.lid` covers a button completely |

## Typography and tokens — group C

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `typography.line-height` | `line-height-out-of-range` | index | `.cramped` sets 14px leading on 16px text |
| `typography.measure` | `measure-too-wide` | deep | `.wide` sits outside the wrap and runs the full viewport width |
| `typography.palette` | `off-palette-color` | a11y | inline `#cccccc` text |
| `typography.palette` | `off-palette-backgroundColor` | deep | `.lid` uses `#eef` |

## Content and copy — group D

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `content.placeholder` | `placeholder-text` | index | a lorem ipsum paragraph |
| `content.i18n-key` | `raw-i18n-key` | index | `home.hero.subtitle` rendered as body copy |
| `content.encoding` | `mojibake` | index | `Weâ€™re` — UTF-8 read as Latin-1 |
| `content.spelling` | `possible-misspelling` | index | `recieve`, plus the lorem ipsum words |
| `content.duplicate-listing` | `duplicate-listing-title` | index | the listing repeats "Annual report 2024" |
| `content.duplicate-listing` | `duplicate-listing-link` | index | the same two items share an href |
| `content.empty-card` | `empty-repeated-item` | index | the fourth `.tile` has no content |
| `content.terminology` | `inconsistent-terminology` | index | a "Sign in" button beside a "Log in" link |
| `content.casing` | `inconsistent-casing` | index | nav reads Home / about us / CONTACT / Responsive |
| `content.date-format` | `mixed-date-formats` | deep, mobile | `01/03/2024` on one page, `2024-03-01` on another |
| `content.dead-end` | `dead-end-page` | deep | no outbound links |

## Accessibility — group E

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `a11y.axe` | `axe-image-alt` | a11y | `<img>` with no `alt` |
| `a11y.axe` | `axe-label` | a11y | `<input>` with no label |
| `a11y.axe` | `axe-heading-order` | a11y | `h1` followed by `h4` |
| `a11y.axe` | `axe-html-has-lang` | a11y | `<html>` with no `lang` |
| `a11y.axe` | `axe-color-contrast` | a11y | `#cccccc` on white |
| `a11y.axe` | `axe-document-title` | seo | no `<title>` |
| `a11y.axe` | `axe-region` | a11y, deep, index, mobile, seo | content outside any landmark |
| `a11y.axe` | `axe-landmark-one-main` | a11y, deep, index, mobile, seo | no `<main>` anywhere in the fixture |
| `a11y.tap-target` | `tap-target-too-small` | a11y, deep, index, mobile, seo | `.tiny-button` at 28px, and nav links 18px tall at mobile |

## Responsive — group F

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `responsive.table-overflow` | `table-overflows-mobile` | mobile | a five-column `nowrap` table at 390px |
| `responsive.content-parity` | `content-missing-at-viewport` | mobile | `.desktop-only` is `display: none` under 500px |

## Performance — group G

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `performance.page-weight` | `image-over-budget` | index | `heavy.png` is 841KB |
| `performance.image-delivery` | `legacy-image-format` | index | the same PNG, uncompressed |
| `performance.image-delivery` | `below-fold-not-lazy` | index | images below the fold with no `loading="lazy"` |
| `performance.cache-headers` | `static-asset-not-cached` | a11y, deep, index, mobile, seo | the fixture server sends no `Cache-Control` |

## Free findings — group A

| Checker | Issue kind | Page | Planted as |
|---|---|---|---|
| `free.console` | `console-error` | index | `app.js` logs an error |
| `free.broken-image` | `broken-image` | index | `missing-image.png` does not exist |
| `free.subresource` | `request-error-status` | index | the same image, 404 |
| `free.broken-link` | `broken-internal-link` | index | a link to `does-not-exist.html` |
| `free.page-status` | `page-error-status` | does-not-exist | that link's target, crawled and 404 |
| `free.redirects` | `long-redirect-chain` | deep | `/broken/redirect/3` takes three hops |
| `free.not-found` | `soft-404` | index | the server answers extensionless unknown paths with 200 |
| `free.exposed-paths` | `exposed-path` | index | the server serves `/.env`, and 200s the other probes |
| `free.source-maps` | `source-map-exposed` | index | `app.js` carries a `sourceMappingURL` |
| `free.title` | `missing-title` | seo | no `<title>` |
| `free.title` | `duplicate-title` | a11y, deep | `deep.html` copies `a11y.html`'s title |
| `free.meta-description` | `missing-meta-description` | seo | no description |
| `free.meta-description` | `duplicate-meta-description` | a11y, deep | `deep.html` copies `a11y.html`'s description |
| `free.canonical` | `canonical-points-elsewhere` | seo | canonical points at `index.html` |
| `free.noindex` | `noindex-on-production` | seo | `<meta name="robots" content="noindex">` |
| `free.viewport-meta` | `missing-viewport-meta` | seo | no viewport meta |
| `free.favicon` | `missing-favicon` | seo | no icon link |
| `free.certificate` | `not-https` | index | the fixture is served over HTTP |
| `free.cookie-flags` | `cookie-not-secure` | a11y, deep, index, mobile, seo | the server sets a cookie with no flags |
| `free.cookie-flags` | `cookie-not-httponly` | a11y, deep, index, mobile, seo | as above |
| `free.cookie-flags` | `cookie-no-samesite` | a11y, deep, index, mobile, seo | as above |
| `free.security-headers` | `missing-header-content-security-policy` | a11y, deep, index, mobile, seo | the server sends no security headers |
| `free.security-headers` | `missing-header-x-frame-options` | a11y, deep, index, mobile, seo | as above |
| `free.security-headers` | `missing-header-x-content-type-options` | a11y, deep, index, mobile, seo | as above |
| `free.security-headers` | `missing-header-referrer-policy` | a11y, deep, index, mobile, seo | as above |

## Must not fire

Each of these was a real false positive. The measurement that caused it is recorded so
the fix is not quietly reverted by a threshold change.

| Rule | Why it fired | Why it is wrong |
|---|---|---|
| `layout.group-gaps` on signature `p` | `<p>` siblings separated by a heading and a card grid gave "gaps" of 441px and 16px | a repeated group is a *contiguous* run of siblings; scattered ones are not a listing |
| `typography.palette` on `#ffffff` | white did not clear the 5% usage bar for the derived palette | white and black are structure, not brand, and a colour used all over the site is a decision |
| `performance.coverage` anywhere in this fixture | the injected 580KB axe-core bundle counted as unused page JavaScript | coverage is taken before axe is injected, and only scripts the page fetched are counted |
| `free.title` `duplicate-title` involving `/broken/index.html` twice | `/broken/redirect/3` redirected to index and was captured as a second page | a page's identity is where it ended up; a redirect to something already captured is not a new page |
| Any finding on `/broken/does-not-exist.html` other than `free.page-status` | an unstyled 404 page reported a missing title, an off-scale font and a dead end | an error page is not the site |

## Not covered by this fixture

`free.mixed-content` needs an HTTPS origin. `free.certificate` expiry needs a real
certificate. `performance.vitals`, `performance.dom-size` and `performance.coverage`
need a site slow or large enough to breach a budget, which a local static fixture is not.
Groups H, I and J arrive with phases 6 and 4.
