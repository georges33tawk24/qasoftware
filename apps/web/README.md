# web

Next.js App Router UI. SPEC §16 (design direction) and §17 (stack). The product name
lives in `config/branding.ts` and nowhere else.

## The one environment variable

```
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:8000
```

The browser calls the control plane **directly** — fetches, the SSE event stream and
evidence images all go to that origin. There is no rewrite proxy, on purpose: a proxy
buffers `text/event-stream`, so live run progress arrives in one lump when the run ends,
which is the spinner SPEC §16 forbids. The API sets CORS for exactly this.

`next build` inlines the value, so it has to be set **for the build**, not just at
runtime, and it is the URL as seen from the browser rather than from inside a container
network. Empty means same-origin, which is what you want behind a single reverse proxy.

## Running it

```bash
NEXT_PUBLIC_API_ORIGIN=http://127.0.0.1:8000 npm run build && npm run start
```

`docker compose up` does the same with `API_ORIGIN` (default `http://localhost:8000`).

## Checks

`npm run lint` is `tsc --noEmit`. The accessibility, layout and typography sweep against
this UI is `make dogfood` from the repo root — it builds the app, serves it beside a
seeded API, crawls it with our own engine in both colour schemes, and fails on anything
`major` or worse.
