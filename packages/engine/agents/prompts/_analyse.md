## Your task now: write this one up

A first pass flagged the region described below. Look at the screenshot, decide what is
actually there, and write it up for a developer who has not seen the page.

Return one JSON object and nothing else. No preamble, no markdown fence.

```
{
  "title": "one line, specific, no more than twelve words",
  "description": "two or three sentences: what is wrong and why it matters here",
  "expected": "what a reader would expect instead",
  "actual": "what the page does",
  "severity": "major | minor | trivial",
  "confidence": 0.0
}
```

- Write about this page, not about web design. A description that would fit any site is
  not worth the reader's time.
- If, looking properly, there is no defect here, return
  `{"title": "", "confidence": 0}` and nothing else. Withdrawing a candidate is a correct
  and useful outcome.
- Never claim a measurement. If the finding needs a number to make sense, it is not yours.
