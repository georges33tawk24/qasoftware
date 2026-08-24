"use client";

import { DIFF_LABELS, type RunEvent } from "@/lib/api";

const STAGES = [
  "capture",
  "ingest",
  "match",
  "exercise",
  "check",
  "reason",
  "resolve",
  "render",
];

/**
 * Page-by-page progress — SPEC §16. Never a spinner: this is the list of what has
 * actually happened, newest last, while it happens.
 */
export function RunProgress({
  events,
  stage,
  connected,
  diff,
}: {
  events: RunEvent[];
  stage: string;
  connected: boolean;
  /** SPEC §11's counts once the run has finished. Regressions lead. */
  diff?: Record<string, number>;
}) {
  const pages = events.filter((event) => event.kind === "page");
  const issues = events.filter((event) => event.kind === "issue");
  const notes = events.filter((event) => event.kind === "note" || event.kind === "error");
  const reached = STAGES.indexOf(stage);

  return (
    <section
      aria-live="polite"
      className="border-b border-border bg-raised px-4 py-3"
    >
      <div className="flex flex-wrap items-baseline gap-x-4 gap-y-1">
        <span className="font-mono text-[11px] uppercase tracking-wider text-accent">
          {stage}
        </span>
        <span className="text-[12px] text-text-2">
          <b className="font-medium text-text">{pages.length}</b> pages ·{" "}
          <b className="font-medium text-text">{issues.length}</b> issues
        </span>
        {!connected && stage !== "done" && stage !== "failed" && (
          <span className="text-[11px] text-text-3">reconnecting…</span>
        )}
        {diff && Object.keys(diff).length > 0 && (
          <span className="flex flex-wrap gap-3 text-[12px]">
            {Object.entries(DIFF_LABELS).map(([key, label]) =>
              diff[key] ? (
                <span key={key} className={key === "regressed" ? "text-blocker" : "text-text-2"}>
                  <b className="font-medium">{diff[key]}</b> {label}
                </span>
              ) : null,
            )}
          </span>
        )}
      </div>

      <ol className="mt-2 flex flex-wrap gap-1" aria-label="Stages">
        {STAGES.map((name, index) => (
          <li
            key={name}
            className={`h-1 w-8 rounded-full transition-colors duration-150 ${
              reached >= index ? "bg-accent" : "bg-border"
            }`}
            title={name}
          />
        ))}
      </ol>

      <ul className="mt-2 max-h-28 overflow-y-auto font-mono text-[11px] leading-5 text-text-3">
        {[...pages.slice(-6), ...notes.slice(-3)].map((event, index) => (
          <li key={index} className={event.kind === "error" ? "text-blocker" : undefined}>
            {event.kind === "page"
              ? `${String(event.path)}  ${event.blocked ? "blocked" : String(event.status)}`
              : String(event.text)}
          </li>
        ))}
      </ul>
    </section>
  );
}
