"use client";

import Link from "next/link";

import { branding } from "@/config/branding";
import type { Project, Run } from "@/lib/api";

const STATE_CLASS: Record<Run["state"], string> = {
  queued: "text-text-3",
  running: "text-accent",
  complete: "text-text-2",
  failed: "text-blocker",
  aborted: "text-critical",
};

/** The persistent left rail — SPEC §16. Projects, then this project's runs. */
export function Rail({
  projects,
  current,
  runs,
  activeRunId,
  onSelectRun,
  onNewRun,
  busy,
}: {
  projects: Project[];
  current: Project | null;
  runs: Run[];
  activeRunId: string | null;
  onSelectRun: (run: Run) => void;
  onNewRun: () => void;
  busy: boolean;
}) {
  return (
    <nav
      aria-label="Projects and runs"
      className="flex w-60 shrink-0 flex-col border-r border-border bg-surface max-md:w-full max-md:border-b"
    >
      <div className="border-b border-border px-3 py-3">
        {/* 44px of thumb on a phone, the rail's own density on a desktop. */}
        <Link
          href="/"
          className="flex min-h-11 items-center text-[13px] font-semibold tracking-tight md:min-h-0"
        >
          {branding.productName}
        </Link>
        <p className="mt-0.5 truncate font-mono text-[11px] text-text-3">
          {current?.target ?? branding.description}
        </p>
      </div>

      <div className="border-b border-border px-2 py-2">
        <h2 className="px-1 pb-1 text-[11px] uppercase tracking-wider text-text-3">Projects</h2>
        {projects.map((project) => (
          <Link
            key={project.id}
            href={`/projects/${project.id}`}
            aria-current={project.id === current?.id ? "page" : undefined}
            className={`flex h-11 items-center justify-between rounded px-2 hover:bg-raised md:h-8 ${
              project.id === current?.id ? "bg-raised text-text" : "text-text-2"
            }`}
          >
            <span className="truncate">{project.name}</span>
            <span className="ml-2 shrink-0 font-mono text-[11px] text-text-3">
              {project.openIssues}
            </span>
          </Link>
        ))}
      </div>

      <div className="min-h-0 flex-1 overflow-y-auto px-2 py-2">
        <div className="flex items-center justify-between px-1 pb-1">
          <h2 className="text-[11px] uppercase tracking-wider text-text-3">Runs</h2>
          <button
            type="button"
            onClick={onNewRun}
            disabled={busy || !current}
            className="h-11 rounded border border-border px-2 text-[11px] text-text-2 transition-colors duration-150 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-6"
          >
            New run
          </button>
        </div>
        {runs.map((run) => (
          <button
            key={run.id}
            type="button"
            onClick={() => onSelectRun(run)}
            aria-current={run.id === activeRunId ? "true" : undefined}
            className={`flex h-11 w-full items-center justify-between rounded px-2 text-left hover:bg-raised md:h-8 ${
              run.id === activeRunId ? "bg-raised" : ""
            }`}
          >
            <span className="font-mono text-[11px] text-text-2">
              {new Date(run.queuedAt).toLocaleString(undefined, {
                month: "short",
                day: "numeric",
                hour: "2-digit",
                minute: "2-digit",
              })}
            </span>
            <span className={`font-mono text-[11px] ${STATE_CLASS[run.state]}`}>
              {run.state === "complete" ? `${run.issues}` : run.state}
            </span>
          </button>
        ))}
        {runs.length === 0 && (
          <p className="px-2 py-2 text-[12px] text-text-3">No runs yet.</p>
        )}
      </div>
    </nav>
  );
}
