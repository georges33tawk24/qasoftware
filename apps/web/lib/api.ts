/**
 * Typed access to the control plane.
 *
 * The browser calls the API directly rather than through a same-origin proxy: a rewrite
 * proxy buffers `text/event-stream`, and live run progress is the one thing this UI must
 * not buffer. One origin for fetches, the event stream and evidence images, so there is
 * only ever one URL to get wrong.
 *
 * Inlined by `next build`, so it must be set for the build, not just at runtime — which
 * is what `docker-compose.yml` does. Empty means same-origin, which is right for a
 * deployment that puts both behind one reverse proxy.
 */
export const API_ORIGIN = process.env.NEXT_PUBLIC_API_ORIGIN ?? "";

/** Absolute URL for a media path the API handed us (`evidence.live.src`, a report). */
export const mediaUrl = (path: string) =>
  /^https?:/.test(path) ? path : `${API_ORIGIN}${path}`;

export type Severity = "blocker" | "critical" | "major" | "minor" | "trivial";
export const SEVERITIES: Severity[] = ["blocker", "critical", "major", "minor", "trivial"];

export type IssueState =
  | "new"
  | "confirmed"
  | "fixed"
  | "regressed"
  | "dismissed"
  | "wont_fix";

export interface Project {
  id: string;
  name: string;
  target: string;
  createdAt: string;
  authorisedBy: string | null;
  figmaFileKey: string | null;
  /** A reference — `env:NAME` or `keychain:service/account` — never the token itself. */
  figmaTokenRef: string | null;
  modelTokenRef: string | null;
  provider: string | null;
  config: Record<string, unknown>;
  runs: number;
  openIssues: number;
  /** Whether each reference resolves right now: `ok`, or why not. Never a value. */
  credentials: Record<string, string>;
}

export interface Run {
  id: string;
  projectId: string;
  state: "queued" | "running" | "complete" | "failed" | "aborted";
  queuedAt: string;
  startedAt: string | null;
  finishedAt: string | null;
  pages: number;
  issues: number;
  counts: Record<string, number>;
  error: string | null;
  artifactRunId: string | null;
  baseRunId: string | null;
  reportUrl: string | null;
  /** SPEC §11's New / Still open / Fixed / Regressed. Empty on a first run. */
  diff: Record<string, number>;
}

export type EntryKind = "override" | "removal" | "addition" | "ignore";

export interface KnowledgeEntry {
  kind: EntryKind;
  scope: string;
  property: string | null;
  expected: string | null;
  note: string;
  assertPresence: boolean;
}

export interface Knowledge {
  id: string;
  raw: string;
  entries: KnowledgeEntry[];
  confirmed: boolean;
  source: "run-form" | "comment" | "dismissal";
  archived: boolean;
  createdBy: string | null;
  createdAt: string;
}

export interface ExportTarget {
  id: string;
  projectId: string;
  kind: string;
  name: string;
  config: Record<string, unknown>;
  enabled: boolean;
  lastExportedAt: string | null;
}

export interface ExportResult {
  fingerprint: string;
  remoteKey: string;
  url: string;
  action: string;
  error: string;
  attachments: number;
}

export interface Schedule {
  id: string;
  projectId: string;
  expression: string;
  timezone: string;
  enabled: boolean;
  lastFiredAt: string | null;
  nextFireAt: string | null;
}

export interface NotifyChannel {
  id: string;
  projectId: string;
  kind: string;
  config: Record<string, unknown>;
  minSeverity: string | null;
  enabled: boolean;
  lastSentAt: string | null;
}

export interface Recording {
  id: string;
  projectId: string;
  name: string;
  persona: string;
  steps: { action: string; selector: string; value: string; description: string }[];
  script: string;
  enabled: boolean;
  createdAt: string;
  createdBy: string | null;
}

export interface BoardColumn {
  state: IssueState;
  title: string;
  issues: Issue[];
}

export interface Board {
  projectId: string;
  columns: BoardColumn[];
  assignees: string[];
  labels: string[];
}

export interface Instance {
  pagePath: string;
  viewport: string;
  selector: string | null;
  actual: string | null;
  fingerprint: string;
  box?: Box | null;
}

export interface IssuePayload {
  title: string;
  description: string;
  expected: string | null;
  actual: string | null;
  instances: Instance[];
  pagePaths: string[];
  data: Record<string, unknown>;
  source: string;
  confidence: number | null;
  steps?: { n: number; text: string; url: string; status: string }[];
}

export interface Issue {
  id: string;
  fingerprint: string;
  checkerId: string;
  issueKind: string;
  category: string;
  severity: Severity;
  state: IssueState;
  title: string;
  instanceCount: number;
  firstSeenRunId: string | null;
  lastSeenRunId: string | null;
  assignee: string | null;
  dueDate: string | null;
  labels: string[];
  dismissedReason: string | null;
  /** Seen, then not seen, then seen again. Grouped apart and never called a regression. */
  flaky: boolean;
  payload: IssuePayload;
}

export interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

export interface EvidencePane {
  src: string;
  scale: number;
  page?: string;
  viewport?: string;
  frame?: string;
  box?: Box;
}

export interface Evidence {
  issueId: string;
  title: string;
  severity: Severity;
  live: EvidencePane;
  design: EvidencePane | null;
  deltas: {
    label: string;
    expected: string | null;
    actual: string | null;
    selector: string | null;
    box: Box;
  }[];
}

export interface Comment {
  id: string;
  issueId: string;
  author: string;
  body: string;
  createdAt: string;
  /** Set when the comment was also filed as project knowledge — SPEC §13's loop. */
  knowledgeId: string | null;
}

export interface RunEvent {
  kind: "stage" | "page" | "issue" | "flow" | "note" | "error";
  stage: string;
  at?: string;
  [key: string]: unknown;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_ORIGIN}${path}`, {
    ...init,
    headers: { "content-type": "application/json", ...(init?.headers ?? {}) },
  });
  if (!response.ok) {
    throw new Error(`${response.status} ${await response.text()}`);
  }
  return response.status === 204 ? (undefined as T) : ((await response.json()) as T);
}

export const api = {
  projects: () => request<Project[]>("/api/projects"),
  project: (id: string) => request<Project>(`/api/projects/${id}`),
  createProject: (body: Partial<Project>) =>
    request<Project>("/api/projects", { method: "POST", body: JSON.stringify(body) }),
  updateProject: (id: string, body: Partial<Project>) =>
    request<Project>(`/api/projects/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  runs: (projectId: string) => request<Run[]>(`/api/projects/${projectId}/runs`),
  run: (id: string) => request<Run>(`/api/runs/${id}`),
  startRun: (projectId: string, body: { knowledge?: string; triggeredBy?: string }) =>
    request<Run>(`/api/projects/${projectId}/runs`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  issues: (projectId: string, params: Record<string, string> = {}) => {
    const query = new URLSearchParams(params).toString();
    return request<Issue[]>(`/api/projects/${projectId}/issues${query ? `?${query}` : ""}`);
  },
  updateIssue: (id: string, body: Record<string, unknown>) =>
    request<Issue>(`/api/issues/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  evidence: (id: string) => request<Evidence>(`/api/issues/${id}/evidence`),
  comments: (id: string) => request<Comment[]>(`/api/issues/${id}/comments`),
  addComment: (id: string, body: { author: string; body: string; intoKnowledge?: boolean }) =>
    request<Comment>(`/api/issues/${id}/comments`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  board: (projectId: string) => request<Board>(`/api/projects/${projectId}/board`),
  knowledge: (projectId: string) => request<Knowledge[]>(`/api/projects/${projectId}/knowledge`),
  addKnowledge: (projectId: string, body: { raw: string; createdBy?: string }) =>
    request<Knowledge>(`/api/projects/${projectId}/knowledge`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  updateKnowledge: (
    id: string,
    body: { confirm?: boolean; archived?: boolean; entries?: KnowledgeEntry[] },
  ) => request<Knowledge>(`/api/knowledge/${id}`, { method: "PATCH", body: JSON.stringify(body) }),
  deleteKnowledge: (id: string) => request<void>(`/api/knowledge/${id}`, { method: "DELETE" }),

  findVolatile: (projectId: string, body: { viewport?: string; loads?: number } = {}) =>
    request<{
      url: string;
      viewport: string;
      compared: number;
      candidates: { selector: string; kind: string; detail: string }[];
      selectors: string[];
    }>(`/api/projects/${projectId}/volatile`, { method: "POST", body: JSON.stringify(body) }),
  exporters: () => request<{ kinds: string[] }>("/api/exporters"),
  exports: (projectId: string) => request<ExportTarget[]>(`/api/projects/${projectId}/exports`),
  addExport: (projectId: string, body: Partial<ExportTarget>) =>
    request<ExportTarget>(`/api/projects/${projectId}/exports`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteExport: (id: string) => request<void>(`/api/exports/${id}`, { method: "DELETE" }),
  pushExport: (id: string) =>
    request<ExportResult[]>(`/api/exports/${id}/run`, { method: "POST", body: "{}" }),

  schedules: (projectId: string) => request<Schedule[]>(`/api/projects/${projectId}/schedules`),
  addSchedule: (projectId: string, body: { expression: string; timezone: string }) =>
    request<Schedule>(`/api/projects/${projectId}/schedules`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteSchedule: (id: string) => request<void>(`/api/schedules/${id}`, { method: "DELETE" }),

  channels: (projectId: string) => request<NotifyChannel[]>(`/api/projects/${projectId}/channels`),
  addChannel: (projectId: string, body: Partial<NotifyChannel>) =>
    request<NotifyChannel>(`/api/projects/${projectId}/channels`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteChannel: (id: string) => request<void>(`/api/channels/${id}`, { method: "DELETE" }),

  recordings: (projectId: string) => request<Recording[]>(`/api/projects/${projectId}/recordings`),
  addRecording: (projectId: string, body: { name: string; script: string; persona?: string }) =>
    request<Recording>(`/api/projects/${projectId}/recordings`, {
      method: "POST",
      body: JSON.stringify(body),
    }),
  deleteRecording: (id: string) => request<void>(`/api/recordings/${id}`, { method: "DELETE" }),
};

export const DIFF_LABELS: Record<string, string> = {
  regressed: "regressed",
  new: "new",
  "still-open": "still open",
  fixed: "fixed",
};

export const SEVERITY_CLASS: Record<Severity, string> = {
  blocker: "text-blocker",
  critical: "text-critical",
  major: "text-major",
  minor: "text-minor",
  trivial: "text-trivial",
};
