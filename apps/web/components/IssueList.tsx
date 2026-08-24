"use client";

import { forwardRef } from "react";

import { SEVERITY_CLASS, SEVERITIES, type Issue, type Severity } from "@/lib/api";

/** The centre list — SPEC §16. 32px rows, tabular numerals, hairline borders. */
export const IssueList = forwardRef<
  HTMLElement,
  {
    issues: Issue[];
    selected: string | null;
    onSelect: (issue: Issue) => void;
    filters: { severity: Set<Severity>; category: string; query: string };
    onFilters: (next: { severity: Set<Severity>; category: string; query: string }) => void;
    categories: string[];
    counts: Record<string, number>;
    /** A phone has room for one pane, so the detail replaces the list rather than
     * splitting the height with it. */
    steppedAside: boolean;
  }
>(function IssueList(
  { issues, selected, onSelect, filters, onFilters, categories, counts, steppedAside },
  ref,
) {
  return (
    <main
      ref={ref}
      className={`flex min-h-0 min-w-0 flex-1 flex-col border-r border-border ${
        steppedAside ? "max-lg:hidden" : ""
      }`}
    >
      <h1 className="sr-only">Issues</h1>
      <div className="flex flex-wrap items-center gap-1 border-b border-border px-3 py-2">
        {SEVERITIES.map((severity) => {
          const on = filters.severity.has(severity);
          return (
            <button
              key={severity}
              type="button"
              aria-pressed={on}
              onClick={() => {
                const next = new Set(filters.severity);
                if (on) next.delete(severity);
                else next.add(severity);
                onFilters({ ...filters, severity: next });
              }}
              className={`h-11 rounded border px-2 md:h-8 text-[12px] transition-colors duration-150 ${
                on ? "border-text-3 text-text" : "border-border text-text-3"
              }`}
            >
              {severity} <span className="font-mono">{counts[severity] ?? 0}</span>
            </button>
          );
        })}

        <select
          aria-label="Category"
          value={filters.category}
          onChange={(event) => onFilters({ ...filters, category: event.target.value })}
          className="h-11 rounded border border-border bg-raised px-2 md:h-8 text-[12px] text-text"
        >
          <option value="">all categories</option>
          {categories.map((category) => (
            <option key={category} value={category}>
              {category}
            </option>
          ))}
        </select>

        <input
          type="search"
          aria-label="Filter issues"
          placeholder="filter…"
          value={filters.query}
          onChange={(event) => onFilters({ ...filters, query: event.target.value })}
          className="h-11 min-w-32 flex-1 rounded border border-border bg-raised px-2 md:h-8 text-[12px] text-text"
        />
        <span className="ml-auto font-mono text-[11px] text-text-3">{issues.length}</span>
      </div>

      <ul className="min-h-0 flex-1 overflow-y-auto" role="listbox" aria-label="Issues">
        {issues.map((issue) => (
          // The li is presentational so the option is the listbox's direct child in the
          // accessibility tree; a plain li between the two breaks the role contract.
          <li key={issue.id} role="presentation">
            <button
              type="button"
              role="option"
              aria-selected={issue.id === selected}
              onClick={() => onSelect(issue)}
              data-issue={issue.id}
              className={`flex h-11 w-full items-center gap-3 border-l-2 px-3 text-left hover:bg-raised md:h-8 ${
                issue.id === selected ? "border-l-accent bg-raised" : "border-l-transparent"
              }`}
            >
              <span
                className={`w-14 shrink-0 font-mono text-[10px] uppercase ${SEVERITY_CLASS[issue.severity]}`}
              >
                {issue.severity}
              </span>
              <span className="truncate">{issue.title}</span>
              {issue.flaky ? (
                <span
                  className="shrink-0 font-mono text-[10px] text-text-3"
                  title="Comes and goes across runs"
                >
                  flaky
                </span>
              ) : null}
              <span className="ml-auto shrink-0 font-mono text-[11px] text-text-3">
                {issue.instanceCount}
              </span>
            </button>
          </li>
        ))}
        {issues.length === 0 && (
          <li role="presentation" className="px-3 py-4 text-[12px] text-text-3">
            Nothing matches these filters.
          </li>
        )}
      </ul>
    </main>
  );
});
