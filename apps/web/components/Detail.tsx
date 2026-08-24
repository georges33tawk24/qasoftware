"use client";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { CommentBox } from "./CommentBox";
import { EvidenceViewer } from "./EvidenceViewer";
import { api, SEVERITY_CLASS, type Comment, type Issue } from "@/lib/api";

/** The right detail pane — SPEC §16. Screenshots are the hero; this gets out of the way. */
export function Detail({
  issue,
  onBack,
  onDismiss,
  onComment,
}: {
  issue: Issue | null;
  onBack: () => void;
  onDismiss: (issue: Issue) => void;
  onComment: (issue: Issue, body: string, intoKnowledge: boolean) => void;
}) {
  const evidence = useQuery({
    queryKey: ["evidence", issue?.id],
    queryFn: () => api.evidence(issue!.id),
    enabled: Boolean(issue),
    retry: false,
  });
  const comments = useQuery({
    queryKey: ["comments", issue?.id],
    queryFn: () => api.comments(issue!.id),
    enabled: Boolean(issue),
  });

  if (!issue) {
    return (
      <aside className="w-[46%] shrink-0 p-4 text-[12px] text-text-3 max-lg:hidden">
        Select an issue.
      </aside>
    );
  }

  const payload = issue.payload ?? {};
  return (
    <aside
      aria-label="Issue detail"
      className="min-h-0 w-[46%] shrink-0 overflow-y-auto p-4 max-lg:w-full max-lg:flex-1"
    >
      <button
        type="button"
        onClick={onBack}
        className="mb-3 h-11 text-[12px] text-text-2 hover:text-text lg:hidden"
      >
        ← All issues
      </button>
      <div className="flex items-baseline gap-2">
        <span className={`font-mono text-[10px] uppercase ${SEVERITY_CLASS[issue.severity]}`}>
          {issue.severity}
        </span>
        <span className="rounded border border-border px-1 font-mono text-[11px] text-text-3">
          {issue.checkerId}
        </span>
        <span className="rounded border border-border px-1 font-mono text-[11px] text-text-3">
          {payload.source ?? "measured"}
        </span>
        {payload.confidence != null && (
          <span className="rounded border border-border px-1 font-mono text-[11px] text-text-3">
            {Math.round(payload.confidence * 100)}%
          </span>
        )}
      </div>

      <h1 className="mt-2 text-[15px] font-semibold leading-6">{issue.title}</h1>
      {payload.description && (
        <p className="mt-2 max-w-[70ch] whitespace-pre-line text-text-2">{payload.description}</p>
      )}

      <dl className="mt-3 grid grid-cols-[92px_1fr] gap-x-3 gap-y-1 font-mono text-[12px]">
        {payload.expected && (
          <>
            <dt className="font-sans text-text-3">Expected</dt>
            <dd className="break-words">{payload.expected}</dd>
          </>
        )}
        {payload.actual && (
          <>
            <dt className="font-sans text-text-3">Actual</dt>
            <dd className="break-words text-blocker">{payload.actual}</dd>
          </>
        )}
        <dt className="font-sans text-text-3">Pages</dt>
        <dd className="break-words">{(payload.pagePaths ?? []).join(", ")}</dd>
      </dl>

      {evidence.data ? (
        <EvidenceViewer evidence={evidence.data} />
      ) : (
        evidence.isError && (
          <p className="mt-3 text-[12px] text-text-3">No screenshot was captured for this issue.</p>
        )
      )}

      {payload.steps && payload.steps.length > 0 && (
        <details className="mt-4">
          <summary className="cursor-pointer text-[12px] text-text-2">
            Reproduction steps ({payload.steps.length})
          </summary>
          <ol className="mt-2 list-decimal pl-5 text-[12px] text-text-2">
            {payload.steps.map((step) => (
              <li key={step.n} className={step.status === "failed" ? "text-blocker" : undefined}>
                {step.text} <span className="font-mono text-[11px] text-text-3">{step.url}</span>
              </li>
            ))}
          </ol>
        </details>
      )}

      {(payload.instances ?? []).length > 0 && (
        <details className="mt-3">
          <summary className="cursor-pointer text-[12px] text-text-2">
            Instances ({issue.instanceCount})
          </summary>
          <table className="mt-2 w-full text-left font-mono text-[11px] text-text-2">
            <thead className="text-text-3">
              <tr>
                <th className="py-1 pr-2 font-normal">Page</th>
                <th className="py-1 pr-2 font-normal">Viewport</th>
                <th className="py-1 pr-2 font-normal">Selector</th>
                <th className="py-1 font-normal">Measured</th>
              </tr>
            </thead>
            <tbody>
              {payload.instances.slice(0, 30).map((instance, index) => (
                <tr key={index} className="border-t border-border">
                  <td className="py-1 pr-2">{instance.pagePath}</td>
                  <td className="py-1 pr-2">{instance.viewport}</td>
                  <td className="break-all py-1 pr-2">{instance.selector ?? "—"}</td>
                  <td className="py-1">{instance.actual ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </details>
      )}

      <Comments issue={issue} comments={comments.data ?? []} onComment={onComment} />

      <div className="mt-4 flex gap-2 border-t border-border pt-3">
        <button
          type="button"
          onClick={() => onDismiss(issue)}
          className="h-11 rounded border border-border px-3 text-[12px] text-text-2 transition-colors duration-150 hover:border-text-3 hover:text-text md:h-8"
        >
          Dismiss <kbd className="ml-1 font-mono text-[10px] text-text-3">x</kbd>
        </button>
        <button
          type="button"
          onClick={() => document.querySelector<HTMLTextAreaElement>(
            "aside[aria-label='Issue detail'] textarea",
          )?.focus()}
          className="h-11 rounded border border-border px-3 text-[12px] text-text-2 transition-colors duration-150 hover:border-text-3 hover:text-text md:h-8"
        >
          Comment <kbd className="ml-1 font-mono text-[10px] text-text-3">c</kbd>
        </button>
      </div>
    </aside>
  );
}

function Comments({
  issue,
  comments,
  onComment,
}: {
  issue: Issue;
  comments: Comment[];
  onComment: (issue: Issue, body: string, intoKnowledge: boolean) => void;
}) {
  return (
    <section className="mt-4 border-t border-border pt-3" aria-label="Comments">
      {comments.length === 0 ? (
        <p className="text-[12px] text-text-3">No comments yet</p>
      ) : (
        comments.map((comment) => (
          <article key={comment.id} className="mb-2">
            <header className="text-[11px] text-text-3">
              {comment.author} · {new Date(comment.createdAt).toLocaleString()}
              {comment.knowledgeId ? (
                <span className="ml-2 text-accent">filed as project knowledge</span>
              ) : null}
            </header>
            <p className="text-text-2">{comment.body}</p>
          </article>
        ))
      )}
      <CommentBox onSubmit={(body, into) => onComment(issue, body, into)} />
    </section>
  );
}
