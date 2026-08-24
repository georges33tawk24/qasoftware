"use client";

import { useEffect, useRef, useState } from "react";

import { API_ORIGIN } from "./api";
import type { RunEvent } from "./api";

/**
 * Live run progress — SPEC §16. Never a spinner: pages appear as they are checked and
 * issues appear as they are found.
 *
 * Reconnects with `after=` so a dropped connection loses nothing, and replays what a late
 * watcher missed.
 */
export function useRunEvents(runId: string | null) {
  const [events, setEvents] = useState<RunEvent[]>([]);
  const [connected, setConnected] = useState(false);
  const seen = useRef(0);

  useEffect(() => {
    if (!runId) return;
    setEvents([]);
    seen.current = 0;

    let source: EventSource | null = null;
    let closed = false;
    let retry: ReturnType<typeof setTimeout> | null = null;

    const connect = () => {
      if (closed) return;
      source = new EventSource(
        `${API_ORIGIN}/api/runs/${runId}/events?after=${seen.current}`,
      );
      source.onopen = () => setConnected(true);
      const onMessage = (raw: MessageEvent<string>) => {
        const event = JSON.parse(raw.data) as RunEvent;
        seen.current += 1;
        setEvents((current) => [...current, event]);
        if (event.kind === "stage" && (event.stage === "done" || event.stage === "failed")) {
          closed = true;
          source?.close();
          setConnected(false);
        }
      };
      for (const kind of ["stage", "page", "issue", "flow", "note", "error", "message"]) {
        source.addEventListener(kind, onMessage as EventListener);
      }
      source.onerror = () => {
        setConnected(false);
        source?.close();
        if (!closed) retry = setTimeout(connect, 1500);
      };
    };

    connect();
    return () => {
      closed = true;
      if (retry) clearTimeout(retry);
      source?.close();
    };
  }, [runId]);

  const stage = [...events].reverse().find((e) => e.kind === "stage")?.stage ?? "queued";
  return { events, stage, connected, finished: stage === "done" || stage === "failed" };
}
