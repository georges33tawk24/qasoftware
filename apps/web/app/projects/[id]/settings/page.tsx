"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { use, useState } from "react";

import { api, type ExportResult, type Project } from "@/lib/api";

const CONTROL =
  "h-11 rounded border border-border bg-raised px-2 text-[12px] text-text md:h-8";
const BUTTON =
  "h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8";

/**
 * Delivery — SPEC §14 and §15. Where issues go, when runs happen, and who hears about it.
 *
 * Deliberately plain: four short forms. A credential is never typed here, only the
 * *name* of the environment variable holding it, which is the rule the engine enforces
 * anyway and is worth making visible.
 */
export default function SettingsPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const client = useQueryClient();
  const invalidate = (key: string) => () =>
    client.invalidateQueries({ queryKey: [key, id] });

  const project = useQuery({ queryKey: ["project", id], queryFn: () => api.project(id) });
  const kinds = useQuery({ queryKey: ["exporters"], queryFn: api.exporters });
  const exports = useQuery({ queryKey: ["exports", id], queryFn: () => api.exports(id) });
  const schedules = useQuery({ queryKey: ["schedules", id], queryFn: () => api.schedules(id) });
  const channels = useQuery({ queryKey: ["channels", id], queryFn: () => api.channels(id) });
  const recordings = useQuery({ queryKey: ["recordings", id], queryFn: () => api.recordings(id) });

  const [pushed, setPushed] = useState<ExportResult[] | null>(null);

  const config = project.data?.config ?? {};
  const masks = Array.isArray(config.maskSelectors) ? (config.maskSelectors as string[]) : [];
  const save = (body: Partial<Project>) =>
    api.updateProject(id, {
      name: project.data!.name,
      target: project.data!.target,
      authorisedBy: project.data!.authorisedBy,
      figmaFileKey: project.data!.figmaFileKey,
      figmaTokenRef: project.data!.figmaTokenRef,
      config: project.data!.config,
      ...body,
    } as never);

  const design = useMutation({ mutationFn: save, onSuccess: invalidate("project") });
  const find = useMutation({ mutationFn: () => api.findVolatile(id) });
  const mask = useMutation({
    mutationFn: (selectors: string[]) => save({ config: { ...config, maskSelectors: selectors } }),
    onSuccess: invalidate("project"),
  });

  const addExport = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.addExport(id, body),
    onSuccess: invalidate("exports"),
  });
  const removeExport = useMutation({
    mutationFn: api.deleteExport,
    onSuccess: invalidate("exports"),
  });
  const push = useMutation({ mutationFn: api.pushExport, onSuccess: setPushed });

  const addSchedule = useMutation({
    mutationFn: (body: { expression: string; timezone: string }) => api.addSchedule(id, body),
    onSuccess: invalidate("schedules"),
  });
  const removeSchedule = useMutation({
    mutationFn: api.deleteSchedule,
    onSuccess: invalidate("schedules"),
  });

  const addChannel = useMutation({
    mutationFn: (body: Record<string, unknown>) => api.addChannel(id, body),
    onSuccess: invalidate("channels"),
  });
  const removeChannel = useMutation({
    mutationFn: api.deleteChannel,
    onSuccess: invalidate("channels"),
  });

  const addRecording = useMutation({
    mutationFn: (body: { name: string; script: string }) => api.addRecording(id, body),
    onSuccess: invalidate("recordings"),
  });
  const removeRecording = useMutation({
    mutationFn: api.deleteRecording,
    onSuccess: invalidate("recordings"),
  });

  return (
    <div className="mx-auto max-w-3xl px-6 pb-16">
      <header className="flex flex-wrap items-baseline gap-x-4 gap-y-1 border-b border-border py-4">
        <h1 className="text-[15px] font-semibold">{project.data?.name ?? "Settings"}</h1>
        <Link href={`/projects/${id}`} className="text-[12px] text-text-2 hover:text-text">
          ← issues
        </Link>
        <Link
          href={`/projects/${id}/board`}
          className="text-[12px] text-text-2 hover:text-text"
        >
          board
        </Link>
      </header>

      <Section
        title="Where issues go"
        note="A token is never stored here — name the environment variable that holds it."
      >
        <form
          className="grid gap-2 sm:grid-cols-[8rem_1fr_1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            addExport.mutate({
              kind: String(form.get("kind")),
              name: String(form.get("targetName") ?? ""),
              config: {
                baseUrl: String(form.get("baseUrl") ?? ""),
                project: String(form.get("project") ?? ""),
                tokenEnv: String(form.get("tokenEnv") ?? ""),
                user: String(form.get("user") ?? ""),
              },
            });
            event.currentTarget.reset();
          }}
        >
          <select name="kind" aria-label="Tracker" className={CONTROL} defaultValue="jira">
            {(kinds.data?.kinds ?? ["jira"]).map((kind) => (
              <option key={kind} value={kind}>
                {kind}
              </option>
            ))}
          </select>
          <input name="baseUrl" placeholder="https://acme.atlassian.net" className={CONTROL} />
          <input name="project" placeholder="project key or repo" className={CONTROL} />
          <button type="submit" className={BUTTON}>
            Add
          </button>
          <input name="tokenEnv" placeholder="JIRA_TOKEN" className={CONTROL} />
          <input name="user" placeholder="account email" className={CONTROL} />
          <input name="targetName" placeholder="a name for this target" className={CONTROL} />
        </form>

        <ul className="mt-3 flex flex-col gap-2">
          {(exports.data ?? []).map((target) => (
            <li key={target.id} className="flex flex-wrap items-baseline gap-2 text-[12px]">
              <span className="font-mono text-text">{target.kind}</span>
              <span className="text-text-2">{target.name || String(target.config.project ?? "")}</span>
              <span className="font-mono text-[11px] text-text-3">
                {target.lastExportedAt
                  ? `last sent ${new Date(target.lastExportedAt).toLocaleString()}`
                  : "never sent"}
              </span>
              <button
                type="button"
                className={`${BUTTON} ml-auto`}
                disabled={push.isPending}
                onClick={() => push.mutate(target.id)}
              >
                Export now
              </button>
              <button type="button" className={BUTTON} onClick={() => removeExport.mutate(target.id)}>
                Remove
              </button>
            </li>
          ))}
        </ul>
        {pushed && (
          <p className="mt-2 text-[12px] text-text-2">
            {pushed.filter((r) => r.action === "created").length} created ·{" "}
            {pushed.filter((r) => r.action === "updated").length} updated ·{" "}
            {pushed.filter((r) => r.action === "failed").length} failed
          </p>
        )}
      </Section>

      <Section title="When runs happen" note="A crontab expression in the client's own timezone.">
        <form
          className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            addSchedule.mutate({
              expression: String(form.get("expression")),
              timezone: String(form.get("timezone") || "UTC"),
            });
            event.currentTarget.reset();
          }}
        >
          <input name="expression" placeholder="0 2 * * *" className={CONTROL} required />
          <input name="timezone" placeholder="Europe/London" className={CONTROL} />
          <button type="submit" className={BUTTON}>
            Add
          </button>
        </form>
        {addSchedule.isError && (
          <p className="mt-2 text-[12px] text-blocker">{String(addSchedule.error)}</p>
        )}
        <ul className="mt-3 flex flex-col gap-1 text-[12px]">
          {(schedules.data ?? []).map((schedule) => (
            <li key={schedule.id} className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-text">{schedule.expression}</span>
              <span className="text-text-2">{schedule.timezone}</span>
              <span className="font-mono text-[11px] text-text-3">
                {schedule.nextFireAt
                  ? `next ${new Date(schedule.nextFireAt).toLocaleString()}`
                  : "not scheduled"}
              </span>
              <button
                type="button"
                className={`${BUTTON} ml-auto`}
                onClick={() => removeSchedule.mutate(schedule.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Who hears about it"
        note="Only when something new or regressed appears. A quiet run sends nothing."
      >
        <form
          className="grid gap-2 sm:grid-cols-[8rem_1fr_8rem_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const severity = String(form.get("minSeverity") ?? "");
            addChannel.mutate({
              kind: String(form.get("kind")),
              config: { url: String(form.get("url") ?? "") },
              minSeverity: severity || null,
            });
            event.currentTarget.reset();
          }}
        >
          <select name="kind" aria-label="Channel" className={CONTROL} defaultValue="slack">
            <option value="slack">slack</option>
            <option value="webhook">webhook</option>
            <option value="email">email</option>
          </select>
          <input name="url" placeholder="https://hooks.slack.com/…" className={CONTROL} />
          <select name="minSeverity" aria-label="At least" className={CONTROL} defaultValue="">
            <option value="">anything new</option>
            <option value="blocker">blocker</option>
            <option value="critical">critical</option>
            <option value="major">major</option>
          </select>
          <button type="submit" className={BUTTON}>
            Add
          </button>
        </form>
        <ul className="mt-3 flex flex-col gap-1 text-[12px]">
          {(channels.data ?? []).map((channel) => (
            <li key={channel.id} className="flex flex-wrap items-baseline gap-2">
              <span className="font-mono text-text">{channel.kind}</span>
              <span className="text-text-2">{channel.minSeverity ?? "anything new"}</span>
              <span className="font-mono text-[11px] text-text-3">
                {channel.lastSentAt
                  ? `last sent ${new Date(channel.lastSentAt).toLocaleString()}`
                  : "nothing sent yet"}
              </span>
              <button
                type="button"
                className={`${BUTTON} ml-auto`}
                onClick={() => removeChannel.mutate(channel.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Design source"
        note="Figma is optional. The token is named, not stored — put the value in the environment or the keychain and reference it here."
      >
        <form
          className="grid gap-2 sm:grid-cols-[1fr_1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            if (!project.data) return;
            design.mutate({
              figmaFileKey: String(form.get("figmaFileKey") ?? "") || null,
              figmaTokenRef: String(form.get("figmaTokenRef") ?? "") || null,
            });
          }}
        >
          <input
            name="figmaFileKey"
            placeholder="file key from the Figma URL"
            defaultValue={project.data?.figmaFileKey ?? ""}
            className={CONTROL}
          />
          <input
            name="figmaTokenRef"
            placeholder="env:ACME_FIGMA_TOKEN"
            defaultValue={project.data?.figmaTokenRef ?? ""}
            className={CONTROL}
          />
          <button type="submit" className={BUTTON}>
            Save
          </button>
        </form>
        {project.data?.credentials?.figma ? (
          <p
            className={`mt-2 text-[12px] ${
              project.data.credentials.figma === "ok" ? "text-text-2" : "text-blocker"
            }`}
          >
            {project.data.credentials.figma === "ok"
              ? "The token reference resolves on the worker."
              : project.data.credentials.figma}
          </p>
        ) : null}
      </Section>

      <Section
        title="What not to compare"
        note="Timestamps, carousels, randomised content, A/B variants. Masked in screenshots and excluded from the visual comparison, so they stop being a finding every run."
      >
        <form
          className="grid gap-2 sm:grid-cols-[1fr_auto]"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            const selector = String(form.get("selector") ?? "").trim();
            if (!selector || !project.data) return;
            const config = project.data.config ?? {};
            const current = Array.isArray(config.maskSelectors)
              ? (config.maskSelectors as string[])
              : [];
            mask.mutate([...new Set([...current, selector])]);
            event.currentTarget.reset();
          }}
        >
          <input name="selector" placeholder=".carousel, #last-updated" className={CONTROL} />
          <button type="submit" className={BUTTON}>
            Add
          </button>
        </form>
        <button
          type="button"
          disabled={find.isPending}
          onClick={() => find.mutate()}
          className={`${BUTTON} mt-2`}
        >
          {find.isPending ? "Loading the page twice…" : "Find them for me"}
        </button>
        {find.data ? (
          <div className="mt-2 text-[12px]">
            {find.data.candidates.length === 0 ? (
              <p className="text-text-2">
                Nothing moved between two loads of {find.data.url}.
              </p>
            ) : (
              <>
                <p className="text-text-2">
                  These changed between two identical loads. Nothing is masked until you
                  say so.
                </p>
                <ul className="mt-1 flex flex-col gap-1">
                  {find.data.candidates.map((candidate) => (
                    <li key={candidate.selector} className="flex flex-wrap items-baseline gap-2">
                      <span className="font-mono text-text">{candidate.selector}</span>
                      <span className="text-text-3">
                        {candidate.kind} · {candidate.detail}
                      </span>
                      <button
                        type="button"
                        className={`${BUTTON} ml-auto`}
                        onClick={() =>
                          mask.mutate([...new Set([...masks, candidate.selector])])
                        }
                      >
                        Mask it
                      </button>
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        ) : null}

        <ul className="mt-3 flex flex-col gap-1 text-[12px]">
          {masks.map((selector) => (
            <li key={selector} className="flex items-baseline gap-2">
              <span className="font-mono text-text">{selector}</span>
              <button
                type="button"
                className={`${BUTTON} ml-auto`}
                onClick={() => mask.mutate(masks.filter((one) => one !== selector))}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </Section>

      <Section
        title="Recorded journeys"
        note="Run `playwright codegen <url>` and paste the script. It runs on every future run."
      >
        <form
          className="grid gap-2"
          onSubmit={(event) => {
            event.preventDefault();
            const form = new FormData(event.currentTarget);
            addRecording.mutate({
              name: String(form.get("name")),
              script: String(form.get("script")),
            });
            event.currentTarget.reset();
          }}
        >
          <input name="name" placeholder="Sign in and reach the cart" className={CONTROL} required />
          <textarea
            name="script"
            rows={4}
            placeholder="await page.goto(…)"
            aria-label="Recorded script"
            className="w-full rounded border border-border bg-raised px-2 py-2 font-mono text-[12px] text-text"
          />
          <button type="submit" className={`${BUTTON} justify-self-start`}>
            Save journey
          </button>
        </form>
        <ul className="mt-3 flex flex-col gap-1 text-[12px]">
          {(recordings.data ?? []).map((recording) => (
            <li key={recording.id} className="flex flex-wrap items-baseline gap-2">
              <span className="text-text">{recording.name}</span>
              <span className="font-mono text-[11px] text-text-3">
                {recording.steps.length} steps · {recording.persona}
              </span>
              <button
                type="button"
                className={`${BUTTON} ml-auto`}
                onClick={() => removeRecording.mutate(recording.id)}
              >
                Remove
              </button>
            </li>
          ))}
        </ul>
      </Section>
    </div>
  );
}

function Section({
  title,
  note,
  children,
}: {
  title: string;
  note: string;
  children: React.ReactNode;
}) {
  return (
    <section className="mt-8" aria-label={title}>
      <h2 className="text-[11px] uppercase tracking-wider text-text-3">{title}</h2>
      <p className="mt-1 text-[12px] text-text-2">{note}</p>
      <div className="mt-3">{children}</div>
    </section>
  );
}
