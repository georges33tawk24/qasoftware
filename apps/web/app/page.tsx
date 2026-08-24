"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";

import { branding } from "@/config/branding";
import { api } from "@/lib/api";

export default function Home() {
  const router = useRouter();
  const client = useQueryClient();
  const projects = useQuery({ queryKey: ["projects"], queryFn: api.projects });
  const [form, setForm] = useState({ name: "", target: "", authorisedBy: "" });

  const create = useMutation({
    mutationFn: () => api.createProject(form),
    onSuccess: (project) => {
      client.invalidateQueries({ queryKey: ["projects"] });
      router.push(`/projects/${project.id}`);
    },
  });

  return (
    <main className="mx-auto max-w-3xl px-6 py-10">
      <h1 className="text-[15px] font-semibold">{branding.productName}</h1>
      <p className="mt-1 text-text-2">{branding.description}</p>

      <section className="mt-8">
        <h2 className="border-b border-border pb-2 text-[11px] uppercase tracking-wider text-text-3">
          Projects
        </h2>
        <ul>
          {(projects.data ?? []).map((project) => (
            <li key={project.id} className="border-b border-border">
              <Link
                href={`/projects/${project.id}`}
                className="flex h-11 items-center justify-between hover:bg-raised"
              >
                <span className="px-2">
                  {project.name}
                  <span className="ml-3 font-mono text-[11px] text-text-3">{project.target}</span>
                </span>
                <span className="px-2 font-mono text-[12px] text-text-2">
                  {project.openIssues} open · {project.runs} runs
                </span>
              </Link>
            </li>
          ))}
        </ul>
        {projects.data?.length === 0 && (
          <p className="py-3 text-[12px] text-text-3">Nothing here yet.</p>
        )}
      </section>

      <section className="mt-8">
        <h2 className="border-b border-border pb-2 text-[11px] uppercase tracking-wider text-text-3">
          New project
        </h2>
        <form
          className="mt-3 grid gap-3 sm:grid-cols-3"
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          {(
            [
              ["name", "Name", "Client site"],
              ["target", "Target URL", "https://example.com/"],
              ["authorisedBy", "Authorised by", "Who signed this off"],
            ] as const
          ).map(([key, label, placeholder]) => (
            <label key={key} className="text-[12px] text-text-3">
              {label}
              <input
                required={key !== "authorisedBy"}
                value={form[key]}
                placeholder={placeholder}
                onChange={(event) => setForm({ ...form, [key]: event.target.value })}
                className="mt-1 h-11 w-full rounded border border-border bg-raised px-2 text-[13px] text-text md:h-8"
              />
            </label>
          ))}
          <div className="sm:col-span-3">
            <button
              type="submit"
              disabled={create.isPending}
              className="h-11 rounded border border-border px-3 text-[12px] text-text-2 hover:border-text-3 hover:text-text disabled:opacity-40 md:h-8"
            >
              Create project
            </button>
            <span className="ml-3 text-[11px] text-text-3">
              Probing an API needs an authoriser; crawling and checking do not.
            </span>
          </div>
        </form>
        {create.isError && (
          <p className="mt-2 text-[12px] text-blocker">{String(create.error)}</p>
        )}
      </section>
    </main>
  );
}
