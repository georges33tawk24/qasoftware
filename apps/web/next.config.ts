import type { NextConfig } from "next";

/**
 * Nothing to configure. The browser calls the control plane directly at
 * `NEXT_PUBLIC_API_ORIGIN` (see `lib/api.ts`) rather than through a rewrite proxy,
 * because a proxy buffers `text/event-stream` and live run progress is the one thing
 * this UI must not buffer.
 */
const config: NextConfig = {};

export default config;
