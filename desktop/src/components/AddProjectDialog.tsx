import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { InspectedRepo, Inspection, LinkerSpec } from "../api/types";
import { useJob } from "../api/useJob";
import { useApp } from "../state";
import { Button } from "./ui/button";
import { Checkbox } from "./ui/checkbox";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";
import { Spinner } from "./ui/spinner";

const msg = (e: unknown) => (e instanceof ApiError ? e.detail : e instanceof Error ? e.message : String(e));
const select = "h-9 w-full rounded-lg border border-line bg-surface px-2 text-sm";

/** A detected link between repos, held by repo position so renaming a repo keeps it pointing right.
 * Each side can name several repos: every app that calls GraphQL against every service that serves it. */
interface Link { type: string; from: number[]; to: number[]; in: number[]; options: Record<string, unknown>; on: boolean }

const many = (v?: string | string[]) => (v === undefined ? [] : Array.isArray(v) ? v : [v]);

function toLinks(specs: LinkerSpec[], repos: InspectedRepo[]): Link[] {
  const idx = (v?: string | string[]) => many(v).map((n) => repos.findIndex((r) => r.name === n)).filter((i) => i >= 0);
  return specs.map((s) => ({ type: s.type, from: idx(s.from), to: idx(s.to), in: idx(s.in), options: s.options ?? {}, on: true }));
}

function describe(l: Link, names: (i: number[]) => string[]) {
  const list = (i: number[]) => names(i).join(", ");
  if (l.type === "graphql") return `GraphQL: ${list(l.from)} call${names(l.from).length === 1 ? "s" : ""} ${list(l.to)}`;
  if (l.type === "react-router") return `React Router screens in ${list(l.in)}`;
  if (l.type === "bull-queue") return `Bull queues in ${list(l.in)}`;
  if (l.type === "sidekiq") return `Sidekiq / ActiveJob jobs ${names(l.in).length > 1 ? "across" : "in"} ${list(l.in)}`;
  if (l.type === "rest") return `REST: ${list(l.from)} call${names(l.from).length === 1 ? "s" : ""} ${list(l.to)}`;
  if (l.type === "kafka") return `Kafka topics ${names(l.in).length > 1 ? "across" : "in"} ${list(l.in)}`;
  if (l.type === "sqs") return `SQS queues ${names(l.in).length > 1 ? "across" : "in"} ${list(l.in)}`;
  if (l.type === "rabbitmq") return `RabbitMQ queues ${names(l.in).length > 1 ? "across" : "in"} ${list(l.in)}`;
  return `${l.type} in ${list([...l.in, ...l.from])}`;
}

/** A link still makes sense once removed repos are taken out of it. */
function live(l: Link, kept: (i: number[]) => number[]) {
  return l.type === "graphql" || l.type === "rest" ? kept(l.from).length > 0 && kept(l.to).length > 0 : kept([...l.in, ...l.from]).length > 0;
}

export function AddProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const { api, refresh, set } = useApp();
  const [step, setStep] = useState<"sources" | "review">("sources");
  const [project, setProject] = useState("");
  const [sources, setSources] = useState<string[]>([""]);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job } = useJob<Inspection>(api, jobId, 500);
  const [repos, setRepos] = useState<InspectedRepo[]>([]);
  const [links, setLinks] = useState<Link[]>([]);
  const [removed, setRemoved] = useState<Set<number>>(new Set());
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!open) {
      setStep("sources"); setProject(""); setSources([""]); setJobId(null); setRepos([]); setLinks([]); setRemoved(new Set()); setError(null);
    }
  }, [open]);
  useEffect(() => {
    if (job?.state === "done" && job.result && step === "sources") {
      setRepos(job.result.repos.map((r) => ({ ...r })));
      setLinks(toLinks(job.result.linkers, job.result.repos));
      setRemoved(new Set());
      setStep("review");
      setJobId(null);
    } else if (job?.state === "failed") {
      setError(job.error ?? "Couldn't read the repos.");
      setJobId(null);
    }
  }, [job, step]);

  const filled = sources.map((s) => s.trim()).filter(Boolean);
  const reading = jobId !== null;
  const read = async () => {
    setError(null);
    try {
      setJobId((await api.inspectProject(filled)).job_id);
    } catch (e) {
      setError(msg(e));
    }
  };
  const pick = async (i: number) => {
    const p = await window.kb?.pickFolder?.();
    if (p) setSources((s) => s.map((x, j) => (j === i ? p : x)));
  };
  const edit = (i: number, patch: Partial<InspectedRepo>) => setRepos((rs) => rs.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  const names = repos.map((r) => r.name.trim());
  const kept = (i: number[]) => i.filter((x) => !removed.has(x));
  const nameList = (i: number[]) => kept(i).map((x) => names[x]);
  const one = (v: string[]) => (v.length === 1 ? v[0] : v);

  const create = async () => {
    setError(null);
    setBusy(true);
    try {
      const body = {
        project: project.trim(),
        repos: repos.filter((_, i) => !removed.has(i)).map((r) => ({ name: r.name.trim(), role: r.role, source: r.source, path: r.path || null, default_branch: r.default_branch?.trim() || null })),
        linkers: links.filter((l) => l.on && live(l, kept)).map((l) => {
          const out: LinkerSpec = { type: l.type };
          if (l.from.length) out.from = one(nameList(l.from));
          if (l.to.length) out.to = one(nameList(l.to));
          if (l.in.length) out.in = one(nameList(l.in));
          if (Object.keys(l.options).length) out.options = l.options;
          return out;
        }),
        context_docs: repos.filter((_, i) => !removed.has(i)).flatMap((r) => r.context_docs.map((d) => `${r.name.trim()}:${d}`)),
      };
      const p = await api.createProject(body);
      await refresh();
      set({ project: p.name, branch: null, view: "library" });
      onOpenChange(false);
    } catch (e) {
      setError(msg(e));
    } finally {
      setBusy(false);
    }
  };
  const useYaml = async () => {
    setError(null);
    const path = await window.kb?.pickFile([{ name: "kb.yaml", extensions: ["yaml", "yml"] }]);
    if (!path) return;
    try {
      const p = await api.addProject(path);
      await refresh();
      set({ project: p.name, branch: null });
      onOpenChange(false);
    } catch (e) {
      setError(msg(e));
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[calc(100vh-48px)] w-[min(680px,calc(100vw-32px))] overflow-y-auto">
        <DialogTitle>Add a project</DialogTitle>
        {step === "sources" ? (
          <form className="flex flex-col gap-4" onSubmit={(e) => { e.preventDefault(); if (project.trim() && filled.length) void read(); }}>
            <label className="flex flex-col gap-1.5 text-sm font-semibold">
              Project name
              <Input aria-label="Project name" value={project} placeholder="sop" onChange={(e) => setProject(e.target.value)} disabled={reading} />
            </label>
            <div className="flex flex-col gap-2">
              <div className="text-sm font-semibold">Repos</div>
              <p className="text-xs text-muted">One row per repo: paste its GitHub link (KB clones it with your git sign-in) or choose the folder of a clone you have.</p>
              {sources.map((s, i) => (
                <div key={i} className="flex gap-2">
                  <Input aria-label={`Repo ${i + 1}`} value={s} placeholder="GitHub link or folder path" disabled={reading}
                    onChange={(e) => setSources((xs) => xs.map((x, j) => (j === i ? e.target.value : x)))} />
                  <Button type="button" variant="secondary" aria-label={`Choose folder for repo ${i + 1}`} onClick={() => void pick(i)} disabled={reading}>Folder…</Button>
                  {sources.length > 1 && (
                    <Button type="button" variant="ghost" aria-label={`Remove repo ${i + 1}`} disabled={reading}
                      onClick={() => setSources((xs) => xs.filter((_, j) => j !== i))}>✕</Button>
                  )}
                </div>
              ))}
              <div>
                <Button type="button" variant="ghost" size="sm" onClick={() => setSources((xs) => [...xs, ""])} disabled={reading}>Add another repo</Button>
              </div>
            </div>
            {reading && <p className="flex items-center gap-2 text-sm text-muted" aria-live="polite"><Spinner />{job?.step || "Reading the repos…"}</p>}
            {error && <p role="alert" className="rounded-lg bg-warn-soft p-3 text-sm text-warn">{error}</p>}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => void useYaml()}>Use an existing kb.yaml…</Button>
              <div className="flex gap-2">
                <Button type="button" variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
                <Button type="submit" disabled={!project.trim() || !filled.length || reading}>Read repos</Button>
              </div>
            </div>
          </form>
        ) : (
          <div className="flex flex-col gap-4">
            <p className="text-sm text-muted">This is what KB found in {project.trim()}. Change anything that's off, then create the project.</p>
            {repos.map((r, i) => removed.has(i) ? null : (
              <div key={i} className="flex flex-col gap-2 rounded-lg border border-line p-3">
                <div className="flex items-center justify-between gap-2">
                  <div className="truncate text-xs text-muted" title={r.source}>{r.source}{r.path ? ` · ${r.path}` : ""}</div>
                  {repos.length - removed.size > 1 && (
                    <Button type="button" variant="ghost" size="sm" aria-label={`Remove ${r.name || `repo ${i + 1}`}`}
                      onClick={() => setRemoved((s) => new Set(s).add(i))}>Remove</Button>
                  )}
                </div>
                <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <label className="flex flex-col gap-1 text-xs font-semibold">Name
                    <Input aria-label={`Name of repo ${i + 1}`} value={r.name} onChange={(e) => edit(i, { name: e.target.value })} />
                  </label>
                  <label className="flex flex-col gap-1 text-xs font-semibold">Role
                    <select aria-label={`Role of repo ${i + 1}`} className={select} value={r.role} onChange={(e) => edit(i, { role: e.target.value })}>
                      <option value="frontend">Frontend</option><option value="backend">Backend</option><option value="other">Other</option>
                    </select>
                  </label>
                  <label className="flex flex-col gap-1 text-xs font-semibold">Production branch
                    <Input aria-label={`Default branch of repo ${i + 1}`} value={r.default_branch ?? ""} list={`branches-${i}`}
                      placeholder={r.is_git ? "master" : "—"} onChange={(e) => edit(i, { default_branch: e.target.value })} />
                    <datalist id={`branches-${i}`}>{r.branches.map((b) => <option key={b} value={b} />)}</datalist>
                  </label>
                  {r.path_options.length > 0 && (
                    <label className="flex flex-col gap-1 text-xs font-semibold">Package
                      <select aria-label={`Package in repo ${i + 1}`} className={select} value={r.path ?? ""} onChange={(e) => edit(i, { path: e.target.value || null })}>
                        <option value="">Whole repo</option>
                        {r.path_options.map((o) => <option key={o.path} value={o.path}>{o.path}</option>)}
                      </select>
                    </label>
                  )}
                </div>
                {!r.is_git && <p className="text-xs text-warn">This folder is not a git clone. Maps and questions work; PR test plans need a git clone.</p>}
                {r.context_docs.length > 0 && <p className="text-xs text-muted">Also reads: {r.context_docs.join(", ")}</p>}
              </div>
            ))}
            <fieldset aria-label="How the repos connect" className="flex flex-col gap-2">
              <legend className="mb-1 text-sm font-semibold">How the repos connect</legend>
              {links.length === 0 && <p className="text-xs text-muted">Nothing detected. Maps still work per repo; links between repos won't be drawn.</p>}
              {links.map((l, i) => {
                if (!live(l, kept)) return null;
                const label = describe(l, nameList);
                return (
                  <div key={i} className="flex flex-wrap items-center gap-2 text-sm">
                    <Checkbox aria-label={label} checked={l.on} onCheckedChange={(v) => setLinks((ls) => ls.map((x, j) => (j === i ? { ...x, on: v === true } : x)))} />
                    <span>{label}</span>
                    {l.type === "graphql" && typeof l.options.backend_style !== "object" && (
                      <select aria-label="GraphQL backend style" className="h-8 rounded-lg border border-line bg-surface px-2 text-xs" value={String(l.options.backend_style ?? "sdl")}
                        onChange={(e) => setLinks((ls) => ls.map((x, j) => (j === i ? { ...x, options: { ...x.options, backend_style: e.target.value } } : x)))}>
                        <option value="sdl">schema files (SDL)</option><option value="graphql-compose">graphql-compose</option>
                      </select>
                    )}
                  </div>
                );
              })}
            </fieldset>
            {error && <p role="alert" className="rounded-lg bg-warn-soft p-3 text-sm text-warn">{error}</p>}
            <div className="flex justify-end gap-2">
              <Button variant="secondary" onClick={() => { setStep("sources"); setError(null); }}>Back</Button>
              <Button onClick={() => void create()} disabled={busy || repos.some((r, i) => !removed.has(i) && !r.name.trim())}>Create project</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
