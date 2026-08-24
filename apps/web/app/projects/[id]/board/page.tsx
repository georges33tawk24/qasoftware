"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";

import { CommentBox } from "@/components/CommentBox";
import { api, SEVERITY_CLASS, type Issue, type IssueState } from "@/lib/api";

/**
 * The board — SPEC §13.
 *
 * A view over the issue records plus columns, assignee, labels and comments. Not a Jira
 * clone: no sprints, no epics, no burndown. The value is that the cards arrive already
 * written, so the only things worth adding are the ones that move work along.
 */
export default function BoardPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const client = useQueryClient();
  const [open, setOpen] = useState<string | null>(null);

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const board = useQuery({ queryKey: ["board", id], queryFn: () => api.board(id) });
  const knowledge = useQuery({
    queryKey: ["knowledge", id],
    queryFn: () => api.knowledge(id),
  });

  const move = useMutation({
    mutationFn: ({ issueId, state }: { issueId: string; state: IssueState }) =>
      api.updateIssue(issueId, { state, by: "you" }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["board", id] }),
  });

  const assign = useMutation({
    mutationFn: ({ issueId, assignee }: { issueId: string; assignee: string }) =>
      api.updateIssue(issueId, { assignee }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["board", id] }),
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
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["knowledge", id] });
      client.invalidateQueries({ queryKey: ["comments"] });
    },
  });

  const confirmKnowledge = useMutation({
    mutationFn: (knowledgeId: string) => api.updateKnowledge(knowledgeId, { confirm: true }),
    onSuccess: () => client.invalidateQueries({ queryKey: ["knowledge", id] }),
  });

  const columns = board.data?.columns ?? [];
  const pending = (knowledge.data ?? []).filter((entry) => !entry.confirmed);

  return (
    <div className="flex h-dvh flex-col">
      <header className="flex shrink-0 flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-border px-4 py-3">
        <h1 className="text-[13px] font-semibold">{project.data?.name ?? "Board"}</h1>
        <Link
          href={`/projects/${id}`}
          className="flex min-h-11 items-center text-[12px] text-text-2 hover:text-text md:min-h-0"
        >
          ← issues
        </Link>
        <span className="ml-auto font-mono text-[11px] text-text-3">
          {columns.reduce((total, column) => total + column.issues.length, 0)} cards
        </span>
      </header>

      {pending.length > 0 && (
        <section
          aria-label="Unconfirmed project knowledge"
          className="shrink-0 border-b border-border bg-raised px-4 py-3"
        >
          <h2 className="text-[11px] uppercase tracking-wider text-text-3">
            Waiting to be confirmed
          </h2>
          <ul className="mt-2 flex flex-col gap-2">
            {pending.map((entry) => (
              <li key={entry.id} className="flex flex-wrap items-baseline gap-2 text-[12px]">
                <span className="text-text">{entry.raw}</span>
                <span className="font-mono text-[11px] text-text-3">
                  {entry.entries.map((one) => one.scope).join(" · ") || "nothing checkable"}
                </span>
                <button
                  type="button"
                  onClick={() => confirmKnowledge.mutate(entry.id)}
                  className="ml-auto h-11 rounded border border-border px-2 text-[11px] text-text-2 hover:border-text-3 hover:text-text md:h-7"
                >
                  Confirm for the next run
                </button>
              </li>
            ))}
          </ul>
        </section>
      )}

      <div className="flex min-h-0 flex-1 gap-3 overflow-x-auto p-3">
        {columns.map((column) => (
          <section
            key={column.state}
            aria-label={column.title}
            className="flex min-h-0 w-72 shrink-0 flex-col rounded border border-border bg-surface"
          >
            <h2 className="flex items-baseline justify-between border-b border-border px-3 py-2 text-[11px] uppercase tracking-wider text-text-3">
              {column.title}
              <span className="font-mono">{column.issues.length}</span>
            </h2>
            <ul className="min-h-0 flex-1 overflow-y-auto p-2">
              {column.issues.map((issue) => (
                <li key={issue.id} className="mb-2">
                  <Card
                    issue={issue}
                    open={open === issue.id}
                    onToggle={() => setOpen(open === issue.id ? null : issue.id)}
                    onMove={(state) => move.mutate({ issueId: issue.id, state })}
                    onAssign={(assignee) => assign.mutate({ issueId: issue.id, assignee })}
                    onComment={(body, intoKnowledge) =>
                      comment.mutate({ issueId: issue.id, body, intoKnowledge })
                    }
                  />
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </div>
  );
}

const MOVES: { state: IssueState; label: string }[] = [
  { state: "new", label: "New" },
  { state: "confirmed", label: "Confirmed" },
  { state: "fixed", label: "Fixed" },
  { state: "wont_fix", label: "Won't fix" },
  { state: "dismissed", label: "Dismissed" },
];

function Card({
  issue,
  open,
  onToggle,
  onMove,
  onAssign,
  onComment,
}: {
  issue: Issue;
  open: boolean;
  onToggle: () => void;
  onMove: (state: IssueState) => void;
  onAssign: (assignee: string) => void;
  onComment: (body: string, intoKnowledge: boolean) => void;
}) {
  const comments = useQuery({
    queryKey: ["comments", issue.id],
    queryFn: () => api.comments(issue.id),
    enabled: open,
  });

  return (
    <article className="rounded border border-border bg-raised">
      <button
        type="button"
        onClick={onToggle}
        aria-expanded={open}
        className="flex w-full flex-col gap-1 p-2 text-left"
      >
        <span className="flex items-baseline gap-2">
          <span
            className={`font-mono text-[10px] uppercase ${SEVERITY_CLASS[issue.severity]}`}
          >
            {issue.severity}
          </span>
          <span className="font-mono text-[10px] text-text-3">{issue.instanceCount}×</span>
          {issue.flaky ? (
            <span
              className="rounded border border-border px-1 font-mono text-[10px] text-text-3"
              title="Seen, then not seen, then seen again. Not counted as a regression."
            >
              flaky
            </span>
          ) : null}
          {issue.assignee ? (
            <span className="ml-auto text-[11px] text-text-2">{issue.assignee}</span>
          ) : null}
        </span>
        <span className="text-[12px] text-text">{issue.title}</span>
        {issue.labels.length > 0 && (
          <span className="flex flex-wrap gap-1">
            {issue.labels.map((label) => (
              <span
                key={label}
                className="rounded border border-border px-1 font-mono text-[10px] text-text-3"
              >
                {label}
              </span>
            ))}
          </span>
        )}
      </button>

      {open && (
        <div className="border-t border-border p-2">
          <p className="font-mono text-[11px] text-text-3">{issue.checkerId}</p>
          {issue.dismissedReason ? (
            <p className="mt-1 text-[11px] text-text-2">{issue.dismissedReason}</p>
          ) : null}
          <div className="mt-2 flex flex-wrap gap-1">
            {MOVES.filter((move) => move.state !== issue.state).map((move) => (
              <button
                key={move.state}
                type="button"
                onClick={() => onMove(move.state)}
                className="h-11 rounded border border-border px-2 text-[11px] text-text-2 hover:border-text-3 hover:text-text md:h-7"
              >
                {move.label}
              </button>
            ))}
          </div>
          <label className="mt-2 flex items-center gap-2 text-[11px] text-text-3">
            assignee
            <input
              defaultValue={issue.assignee ?? ""}
              onBlur={(event) => onAssign(event.target.value)}
              className="h-11 flex-1 rounded border border-border bg-surface px-2 text-[12px] text-text md:h-7"
            />
          </label>
          <div className="mt-2">
            {(comments.data ?? []).map((one) => (
              <p key={one.id} className="mb-1 text-[11px] text-text-2">
                <span className="text-text-3">{one.author}:</span> {one.body}
                {one.knowledgeId ? (
                  <span className="ml-1 text-accent">· filed as knowledge</span>
                ) : null}
              </p>
            ))}
            <CommentBox onSubmit={onComment} label="Comment" />
          </div>
        </div>
      )}
    </article>
  );
}
