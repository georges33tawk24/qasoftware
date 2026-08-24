## Your task now: sweep

Look at the screenshot and flag anything within your remit that might be a defect. Be
suspicious. This is a cheap first pass and a stronger reviewer will judge each of your
candidates afterwards, so a wrong flag costs little and a missed one costs a lot. Flag it.

Return a JSON array and nothing else. No preamble, no explanation, no markdown fence.

```
[
  {"box": {"x": 0, "y": 0, "w": 0, "h": 0}, "kind": "short-slug", "note": "a few words", "confidence": 0.0}
]
```

- `box` is in CSS pixels in the screenshot's own coordinate space, with the origin at the
  top left of the full page. If you cannot place a box around it, leave it out.
- `kind` is a short kebab-case slug for the sort of problem, e.g. `competing-calls-to-action`.
- `note` is at most eight words. Not a sentence, not an argument.
- `confidence` is 0 to 1 and may be low. Low confidence is what this pass is for.

Return `[]` if you see nothing. An empty array is a good answer and a common one.
