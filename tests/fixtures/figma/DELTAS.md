# Planted design deltas

The expected-results file for the design comparison, asserted by `tests/test_figma.py`.
Same contract as `tests/fixtures/site/DEFECTS.md`: every row must be found, and nothing
outside these rows may be reported.

The design in `fixtures/design/figma/file.json` is generated from a real capture of the
clean fixture site by `tests/build_design_fixture.py`, then walked away from in exactly
these ways. Generating the design *from* the page is deliberate — it means every
difference reported is one this file put there on purpose, and a false positive has
nowhere to hide.

Rebuild with `.venv/bin/python -m tests.build_design_fixture`.

## Deltas

| Checker | Issue kind | Planted as |
|---|---|---|
| `figma.geometry` | `design-position-x` | the third card is shifted 6px right in the design, children and all |
| `figma.geometry` | `design-size-w` | the second card is 12px wider in the design |
| `figma.typography` | `design-font-size` | the `h1` is 32px in the design and 36px on the page |
| `figma.typography` | `design-line-height` | the intro paragraph's leading is 28px in the design and 24px on the page |
| `figma.colour` | `design-background-colour` | the call to action is `#1C64C8` in the design and `#3B7DD8` on the page (ΔE 9.2) |
| `figma.decoration` | `design-radius` | the call to action has a 12px radius in the design and 6px on the page |
| `figma.decoration` | `design-shadow` | the first card carries a drop shadow in the design and none on the page |
| `figma.decoration` | `design-opacity` | the footer is at 60% in the design and fully opaque on the page |
| `figma.content` | `design-text-content` | the second card reads "the 2nd card" in the design and "the second card" on the page |
| `figma.presence` | `possible-missing-element` | a "Sign up to the newsletter" heading exists only in the design |
| `figma.presence` | `possible-extra-element` | the "Private area" link exists only on the page |

## Must not fire

| Rule | Why it fired | Why it is wrong |
|---|---|---|
| `design-position-x` on a shifted card's children | moving the card node without moving its children made every descendant look 6px out | in Figma a frame moves with its contents, and position is compared *within* the matched container so one shifted section cannot cascade |
| `design-padding-*` on the call to action | the design's label node was matched to the same element as its parent frame, and a text node has no padding | a node absorbed into its container describes that element's *text*; only the container's box is comparable |
| `design-text-content` twice for one copy change | a container's label text is used to make matching possible and was then also reported as its own copy | only nodes with real `characters` carry copy |
| `typography.scale` / `typography.palette` alongside a design delta | the design tokens are authoritative, so the page's 36px heading was off the scale *and* off the matched node | where a frame matched, group J measures it precisely; the token-derived scales exist for the viewports with no frame at all |

## The failure mode

`tests/fixtures/figma/other.json` is a design for a completely different product. Pointed
at this site it must produce exactly one finding — `figma.no-match` — and no deltas at
all, whether the mapping is left to the automatic guess (which declines to suggest
anything) or forced by hand.

SPEC §7: one wrong match produces a page of nonsense findings, which is the fastest way
to lose a user permanently.

## Not covered here

Multiple frames, multiple viewports against one frame, and the `figmaPins` escape hatch
are exercised by unit tests rather than by this fixture. Image asset comparison
(`design-image-missing`) needs an image fill the page does not render; the fixture's
logo matches, so the check stays silent, which is the correct outcome.
