"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useRef, useState } from "react";

import { Detail } from "@/components/Detail";
import { IssueList } from "@/components/IssueList";
import { Palette, type Command } from "@/components/Palette";
import { Rail } from "@/components/Rail";
import { RunDialog } from "@/components/RunDialog";
import { RunProgress } from "@/components/RunProgress";
import { api, mediaUrl, SEVERITIES, type Issue, type Severity } from "@/lib/api";
import { useRunEvents } from "@/lib/useEvents";

const TYPING = new Set(["INPUT", "TEXTAREA", "SELECT"]);

export default function ProjectPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const client = useQueryClient();
  const router = useRouter();

  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const runs = useQuery({
    queryKey: ["runs", id],
    queryFn: () => api.runs(id),
    refetchInterval: (query) =>
      (query.state.data ?? []).some((run) => run.state === "queued" || run.state === "running")
        ? 2000
        : false,
  });
  const issues = useQuery({ queryKey: ["issues", id], queryFn: () => api.issues(id) });

  const [watching, setWatching] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [runOpen, setRunOpen] = useState(false);
  const [filters, setFilters] = useState({
    severity: new Set<Severity>(SEVERITIES),
    category: "",
    query: "",
  });
  const listRef = useRef<HTMLDivElement>(null);

  const live = useRunEvents(watching);
  useEffect(() => {
    if (live.finished) {
      client.invalidateQueries({ queryKey: ["issues", id] });
      client.invalidateQueries({ queryKey: ["runs", id] });
    }
  }, [live.finished, client, id]);

  const startRun = useMutation({
    mutationFn: () => api.startRun(id, { triggeredBy: "ui" }),
    onSuccess: (run) => {
      setWatching(run.id);
      setRunOpen(false);
      client.invalidateQueries({ queryKey: ["runs", id] });
    },
  });

  const update = useMutation({
    mutationFn: ({ issueId, body }: { issueId: string; body: Record<string, unknown> }) =>
      api.updateIssue(issueId, body),
    onSuccess: () => client.invalidateQueries({ queryKey: ["issues", id] }),
  });

  const comment = useMutation({
    mutationFn: ({
      issueId,
      body,
      intoKnowledge,
    }: {
      issueId: string;
      body: string;
      intoKnowledge: boolean;
    }) => api.addComment(issueId, { author: "you", body, intoKnowledge }),
    onSuccess: (created) => {
      client.invalidateQueries({ queryKey: ["comments", created.issueId] });
      client.invalidateQueries({ queryKey: ["knowledge", id] });
    },
  });

  const all = issues.data ?? [];
  const counts = useMemo(() => {
    const out: Record<string, number> = {};
    for (const issue of all) out[issue.severity] = (out[issue.severity] ?? 0) + 1;
    return out;
  }, [all]);
  const categories = useMemo(
    () => [...new Set(all.map((issue) => issue.category))].sort(),
    [all],
  );

  const visible = useMemo(() => {
    const needle = filters.query.trim().toLowerCase();
    return all.filter((issue) => {
      if (!filters.severity.has(issue.severity)) return false;
      if (filters.category && issue.category !== filters.category) return false;
      if (needle && !`${issue.title} ${issue.checkerId}`.toLowerCase().includes(needle)) {
        return false;
      }
      return true;
    });
  }, [all, filters]);

  const current = visible.find((issue) => issue.id === selected) ?? null;

  const move = useCallback(
    (delta: number) => {
      if (visible.length === 0) return;
      const index = visible.findIndex((issue) => issue.id === selected);
      const next = visible[Math.max(0, Math.min(visible.length - 1, index + delta))] ?? visible[0];
      setSelected(next.id);
      listRef.current
        ?.querySelector(`[data-issue="${next.id}"]`)
        ?.scrollIntoView({ block: "nearest" });
    },
    [visible, selected],
  );

  const dismiss = useCallback(
    (issue: Issue) => {
      const reason = window.prompt(`Dismiss “${issue.title}”. Why?`) ?? undefined;
      if (reason === undefined) return;
      update.mutate({ issueId: issue.id, body: { state: "dismissed", reason, by: "you" } });
    },
    [update],
  );

  const focusComment = useCallback(() => {
    document
      .querySelector<HTMLTextAreaElement>("aside[aria-label='Issue detail'] textarea")
      ?.focus();
  }, []);

  const addComment = useCallback(
    (issue: Issue, body: string, intoKnowledge: boolean) => {
      comment.mutate({ issueId: issue.id, body, intoKnowledge });
    },
    [comment],
  );

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setPaletteOpen((open) => !open);
        return;
      }
      const target = event.target as HTMLElement | null;
      if (event.metaKey || event.ctrlKey || event.altKey) return;
      if (target && (TYPING.has(target.tagName) || target.isContentEditable)) return;
      if (event.key === "j") move(1);
      else if (event.key === "k") move(-1);
      else if (event.key === "x" && current) dismiss(current);
      else if (event.key === "c" && current) focusComment();
      else return;
      event.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, current, dismiss, focusComment]);

  const commands: Command[] = useMemo(
    () => [
      { id: "run", label: "Start a run…", hint: "with project knowledge", run: () => setRunOpen(true) },
      { id: "board", label: "Open the board", run: () => router.push(`/projects/${id}/board`) },
      {
        id: "settings",
        label: "Exports, schedules and notifications",
        run: () => router.push(`/projects/${id}/settings`),
      },
      ...(current
        ? [
            { id: "dismiss", label: `Dismiss “${current.title}”`, hint: "x", run: () => dismiss(current) },
            { id: "comment", label: "Comment on this issue", hint: "c", run: focusComment },
            {
              id: "confirm",
              label: "Mark confirmed",
              run: () => update.mutate({ issueId: current.id, body: { state: "confirmed" } }),
            },
          ]
        : []),
      ...SEVERITIES.map((severity) => ({
        id: `only-${severity}`,
        label: `Show only ${severity}`,
        run: () => setFilters({ ...filters, severity: new Set([severity]) }),
      })),
      {
        id: "all",
        label: "Show every severity",
        run: () => setFilters({ ...filters, severity: new Set(SEVERITIES) }),
      },
      ...(runs.data?.[0]?.reportUrl
        ? [
            {
              id: "report",
              label: "Open the latest report",
              run: () => window.open(mediaUrl(runs.data![0].reportUrl!), "_blank"),
            },
          ]
        : []),
    ],
    [current, dismiss, addComment, startRun, update, filters, runs.data],
  );

  const running = runs.data?.some((run) => run.state === "queued" || run.state === "running");

  return (
    <div className="flex h-dvh flex-col">
      <div className="flex min-h-0 flex-1 max-md:flex-col">
        <Rail
          projects={projects.data ?? []}
          current={project.data ?? null}
          runs={runs.data ?? []}
          activeRunId={watching}
          onSelectRun={(run) => setWatching(run.id)}
          onNewRun={() => setRunOpen(true)}
          busy={startRun.isPending || Boolean(running)}
        />

        <div className="flex min-h-0 min-w-0 flex-1 flex-col">
          {watching && (
            <RunProgress
              events={live.events}
              stage={live.stage}
              connected={live.connected}
              diff={runs.data?.find((run) => run.id === watching)?.diff}
            />
          )}
          <div className="flex min-h-0 flex-1 max-lg:flex-col">
            <IssueList
              ref={listRef}
              issues={visible}
              selected={selected}
              onSelect={(issue) => setSelected(issue.id)}
              filters={filters}
              onFilters={setFilters}
              categories={categories}
              counts={counts}
              steppedAside={Boolean(current)}
            />
            <Detail
              issue={current}
              onBack={() => setSelected(null)}
              onDismiss={dismiss}
              onComment={addComment}
            />
          </div>
        </div>
      </div>

      {runOpen && (
        <RunDialog
          projectId={id}
          busy={startRun.isPending}
          onClose={() => setRunOpen(false)}
          onStart={() => startRun.mutate()}
        />
      )}

      <footer className="flex h-11 shrink-0 items-center gap-4 border-t border-border px-3 font-mono text-[11px] text-text-3 md:h-7">
        <span>j/k move</span>
        <span>x dismiss</span>
        <span>c comment</span>
        <span>⌘K commands</span>
        <Link
          href={`/projects/${id}/board`}
          className="-mx-3 flex h-11 items-center px-3 hover:text-text"
        >
          board
        </Link>
        <Link
          href={`/projects/${id}/settings`}
          className="flex h-11 items-center px-3 hover:text-text"
        >
          settings
        </Link>
        <span className="ml-auto">{visible.length} shown</span>
      </footer>

      <Palette open={paletteOpen} onClose={() => setPaletteOpen(false)} commands={commands} />
    </div>
  );
}
