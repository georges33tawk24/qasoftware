"use client";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { api, type Knowledge, type KnowledgeEntry } from "@/lib/api";

const KIND_HELP: Record<string, string> = {
  override: "a property was changed on purpose",
  removal: "something in the design is gone on purpose",
  addition: "something not in the design is there on purpose",
  ignore: "silence this, with no claim about the site",
};

/**
 * The run form — SPEC §10.
 *
 * "Paste anything you were told about this project", then read back what that was
 * understood to mean and say yes. Nothing is applied until someone has: a parsed entry
 * that nobody confirmed is a guess, and a guess that silences findings is worse than no
 * knowledge at all.
 */
export function RunDialog({
  projectId,
  onStart,
  onClose,
  busy,
}: {
  projectId: string;
  onStart: () => void;
  onClose: () => void;
  busy: boolean;
}) {
  const client = useQueryClient();
  const [raw, setRaw] = useState("");
  const [draft, setDraft] = useState<Knowledge | null>(null);
  const [keep, setKeep] = useState<Set<number>>(new Set());

  const parse = useMutation({
    mutationFn: () => api.addKnowledge(projectId, { raw: raw.trim(), createdBy: "you" }),
    onSuccess: (created) => {
      setDraft(created);
      setKeep(new Set(created.entries.map((_, index) => index)));
    },
  });

  const confirm = useMutation({
    mutationFn: (entries: KnowledgeEntry[]) =>
      api.updateKnowledge(draft!.id, { confirm: true, entries }),
    onSuccess: () => {
      client.invalidateQueries({ queryKey: ["knowledge", projectId] });
      onStart();
    },
  });

  const discard = useMutation({
    mutationFn: () => api.deleteKnowledge(draft!.id),
    onSuccess: () => {
      setDraft(null);
      onStart();
    },
  });

  return (
    <div
      role="dialog"
      aria-label="Start a run"
      className="fixed inset-0 z-20 flex items-start justify-center bg-black/50 p-6 pt-[10vh]"
      onClick={(event) => event.target === event.currentTarget && onClose()}
    >
      <div className="w-full max-w-xl rounded border border-border bg-surface p-4 shadow-2xl">
        {draft === null ? (
          <>
            <h2 className="text-[13px] font-semibold">Start a run</h2>
            <label htmlFor="knowledge" className="mt-3 block text-[12px] text-text-2">
              Paste anything you were told about this project.
            </label>
            <textarea
              id="knowledge"
              value={raw}
              autoFocus
              rows={5}
              onChange={(event) => setRaw(event.target.value)}
              placeholder="the client asked for the CTA to be green, and testimonials are deferred"
              className="mt-2 w-full rounded border border-border bg-raised px-2 py-2 text-[13px] text-text"
            />
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                disabled={!raw.trim() || parse.isPending}
                onClick={() => parse.mutate()}
                className="h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8"
              >
                {parse.isPending ? "Reading…" : "Read it back"}
              </button>
              <button
                type="button"
                disabled={busy}
                onClick={onStart}
                className="h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8"
              >
                Run without notes
              </button>
              <button
                type="button"
                onClick={onClose}
                className="ml-auto h-11 px-2 text-[12px] text-text-3 hover:text-text md:h-8"
              >
                Cancel
              </button>
            </div>
          </>
        ) : (
          <>
            <h2 className="text-[13px] font-semibold">Is this what you meant?</h2>
            <p className="mt-1 text-[12px] text-text-2">
              Each of these both silences the findings it explains and gets checked in its
              own right, so the report can say whether it was actually done.
            </p>
            {draft.entries.length === 0 ? (
              <p className="mt-3 text-[12px] text-text-3">
                Nothing checkable came out of that. It is kept with the project either way.
              </p>
            ) : (
              <ul className="mt-3 flex flex-col gap-2">
                {draft.entries.map((entry, index) => (
                  <li key={index} className="rounded border border-border bg-raised p-2">
                    <label className="flex items-start gap-2">
                      <input
                        type="checkbox"
                        checked={keep.has(index)}
                        onChange={() =>
                          setKeep((current) => {
                            const next = new Set(current);
                            if (!next.delete(index)) next.add(index);
                            return next;
                          })
                        }
                        className="mt-1"
                      />
                      <span>
                        <span className="font-mono text-[12px] text-text">{entry.scope}</span>
                        {entry.property ? (
                          <span className="font-mono text-[12px] text-text-2">
                            {" "}
                            {entry.property} → {entry.expected}
                          </span>
                        ) : null}
                        <span className="block text-[11px] text-text-3">
                          {KIND_HELP[entry.kind] ?? entry.kind}
                          {entry.note ? ` · ${entry.note}` : ""}
                        </span>
                      </span>
                    </label>
                  </li>
                ))}
              </ul>
            )}
            <div className="mt-3 flex items-center gap-2">
              <button
                type="button"
                disabled={confirm.isPending || busy}
                onClick={() =>
                  confirm.mutate(draft.entries.filter((_, index) => keep.has(index)))
                }
                className="h-11 rounded border border-border px-3 text-[12px] text-text hover:border-text-3 disabled:opacity-40 md:h-8"
              >
                Confirm and run
              </button>
              <button
                type="button"
                disabled={discard.isPending || busy}
                onClick={() => discard.mutate()}
                className="h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8"
              >
                Discard and run anyway
              </button>
              <button
                type="button"
                onClick={onClose}
                className="ml-auto h-11 px-2 text-[12px] text-text-3 hover:text-text md:h-8"
              >
                Cancel
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
