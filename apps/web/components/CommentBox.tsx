"use client";

import { useState } from "react";

/**
 * SPEC §13's key feature. A developer opens the board, says "the client changed this,
 * ignore it", and that becomes project knowledge — as a draft, because §10 wants a human
 * to confirm before a run acts on it. The checkbox is what closes the loop; without it
 * the comment is just a comment.
 */
export function CommentBox({
  onSubmit,
  busy,
  label = "Comment",
}: {
  onSubmit: (body: string, intoKnowledge: boolean) => void;
  busy?: boolean;
  label?: string;
}) {
  const [body, setBody] = useState("");
  const [intoKnowledge, setIntoKnowledge] = useState(false);

  return (
    <form
      className="mt-3 flex flex-col gap-2"
      onSubmit={(event) => {
        event.preventDefault();
        if (!body.trim()) return;
        onSubmit(body.trim(), intoKnowledge);
        setBody("");
        setIntoKnowledge(false);
      }}
    >
      <textarea
        value={body}
        rows={2}
        onChange={(event) => setBody(event.target.value)}
        placeholder="the client changed this, ignore it"
        aria-label={label}
        className="w-full rounded border border-border bg-raised px-2 py-1.5 text-[12px] text-text"
      />
      <div className="flex items-center gap-3">
        <label className="flex items-center gap-2 text-[11px] text-text-2">
          <input
            type="checkbox"
            checked={intoKnowledge}
            onChange={(event) => setIntoKnowledge(event.target.checked)}
          />
          File as project knowledge
        </label>
        <button
          type="submit"
          disabled={busy || !body.trim()}
          className="ml-auto h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8"
        >
          {label}
        </button>
      </div>
    </form>
  );
}
