"use client";

import { useEffect, useMemo, useState } from "react";

export interface Command {
  id: string;
  label: string;
  hint?: string;
  run: () => void;
}

/** ⌘K — SPEC §16. Keyboard-first, so everything reachable by mouse is reachable here. */
export function Palette({
  open,
  onClose,
  commands,
}: {
  open: boolean;
  onClose: () => void;
  commands: Command[];
}) {
  const [query, setQuery] = useState("");
  const [cursor, setCursor] = useState(0);

  const matches = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return commands.slice(0, 12);
    return commands
      .filter((command) => `${command.label} ${command.hint ?? ""}`.toLowerCase().includes(needle))
      .slice(0, 12);
  }, [commands, query]);

  useEffect(() => {
    if (open) {
      setQuery("");
      setCursor(0);
    }
  }, [open]);

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      className="fixed inset-0 z-50 flex items-start justify-center bg-black/50 pt-[12vh]"
      onClick={onClose}
    >
      <div
        className="w-[520px] max-w-[92vw] overflow-hidden rounded border border-border bg-raised"
        onClick={(event) => event.stopPropagation()}
      >
        <input
          autoFocus
          value={query}
          placeholder="Type a command…"
          aria-label="Command"
          onChange={(event) => {
            setQuery(event.target.value);
            setCursor(0);
          }}
          onKeyDown={(event) => {
            if (event.key === "Escape") onClose();
            if (event.key === "ArrowDown") {
              event.preventDefault();
              setCursor((value) => Math.min(value + 1, matches.length - 1));
            }
            if (event.key === "ArrowUp") {
              event.preventDefault();
              setCursor((value) => Math.max(value - 1, 0));
            }
            if (event.key === "Enter" && matches[cursor]) {
              matches[cursor].run();
              onClose();
            }
          }}
          className="h-11 w-full border-b border-border bg-transparent px-3 text-[13px] text-text outline-none"
        />
        <ul role="listbox" aria-label="Commands" className="max-h-80 overflow-y-auto py-1">
          {matches.map((command, index) => (
            <li key={command.id}>
              <button
                type="button"
                role="option"
                aria-selected={index === cursor}
                onMouseEnter={() => setCursor(index)}
                onClick={() => {
                  command.run();
                  onClose();
                }}
                className={`flex h-11 w-full items-center justify-between px-3 text-left md:h-8 ${
                  index === cursor ? "bg-surface text-text" : "text-text-2"
                }`}
              >
                <span>{command.label}</span>
                {command.hint && (
                  <span className="font-mono text-[11px] text-text-3">{command.hint}</span>
                )}
              </button>
            </li>
          ))}
          {matches.length === 0 && (
            <li className="px-3 py-2 text-[12px] text-text-3">No command matches.</li>
          )}
        </ul>
      </div>
    </div>
  );
}
