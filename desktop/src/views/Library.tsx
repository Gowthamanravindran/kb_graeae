import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { BranchRow, BranchesResponse } from "../api/types";
import { useJob } from "../api/useJob";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Checkbox } from "../components/ui/checkbox";
import { Dialog, DialogContent, DialogTitle } from "../components/ui/dialog";
import { Input } from "../components/ui/input";
import { AddProjectDialog } from "../components/AddProjectDialog";
import { LoginDialog } from "../components/LoginDialog";
import { hostOf } from "../lib";
import { useApp } from "../state";

const BADGE: Record<BranchRow["state"], { text: string; tone: "ok" | "warn" | "priv" | "accent" }> = {
  synced: { text: "Synced", tone: "ok" },
  "not-pulled": { text: "Not pulled", tone: "warn" },
  "local-only": { text: "Private · local only", tone: "priv" },
  "update-available": { text: "Update available", tone: "accent" },
  "in-progress": { text: "In progress", tone: "accent" },
};

function ago(iso: string | null): string {
  if (!iso) return "";
  const d = (Date.now() - new Date(iso).getTime()) / 86400000;
  if (d < 1) return "today";
  if (d < 2) return "yesterday";
  if (d < 14) return `${Math.floor(d)} days ago`;
  return `${Math.floor(d / 7)} weeks ago`;
}

function Progress({ jobId, onDone }: { jobId: string; onDone(): void }) {
  const { api } = useApp();
  const { job, done, failed } = useJob(api, jobId);
  useEffect(() => {
    if (done || failed) onDone();
  }, [done, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  if (failed) return <p className="text-xs text-warn">{job?.error}</p>;
  return (
    <div>
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((job?.progress ?? 0) * 100)} className="h-2 max-w-[260px] overflow-hidden rounded bg-priv-soft">
        <div className="h-2 bg-accent" style={{ width: `${Math.round((job?.progress ?? 0.05) * 100)}%` }} />
      </div>
      <div className="mt-1.5 text-xs text-muted">{job?.step || "Starting…"}</div>
    </div>
  );
}

function AnalyseDialog({ open, onOpenChange, onStart, repos }: {
  open: boolean; onOpenChange(o: boolean): void; repos: { name: string; default_branch?: string | null }[];
  onStart(label: string, resume: boolean, branches?: Record<string, string>): void;
}) {
  const [branch, setBranch] = useState("");
  const [resume, setResume] = useState(false);
  const [perRepo, setPerRepo] = useState(false);
  const [label, setLabel] = useState("");
  const [map, setMap] = useState<Record<string, string>>({});
  useEffect(() => {
    if (open) setMap(Object.fromEntries(repos.map((r) => [r.name, r.default_branch ?? ""])));
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps
  const ready = perRepo ? !!label.trim() && repos.every((r) => (map[r.name] ?? "").trim()) : !!branch.trim();
  const submit = () => {
    if (perRepo) onStart(label.trim(), resume, Object.fromEntries(repos.map((r) => [r.name, map[r.name].trim()])));
    else onStart(branch.trim(), resume);
    onOpenChange(false);
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Analyse a branch</DialogTitle>
        <p className="mb-4 text-sm text-muted">Builds the code map and names the modules. Larger codebases take up to an hour; you can keep using the app.</p>
        <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); if (ready) submit(); }}>
          <div className="flex items-center gap-2">
            <Checkbox id="perrepo" checked={perRepo} onCheckedChange={(v) => setPerRepo(v === true)} />
            <label htmlFor="perrepo" className="text-sm">Different branch per repo</label>
          </div>
          {perRepo ? (
            <>
              {repos.map((r) => (
                <div key={r.name} className="flex flex-col gap-1">
                  <label className="text-sm font-semibold" htmlFor={`br-${r.name}`}>{r.name} branch</label>
                  <Input id={`br-${r.name}`} value={map[r.name] ?? ""} onChange={(e) => setMap((m) => ({ ...m, [r.name]: e.target.value }))} placeholder="master" />
                </div>
              ))}
              <label className="text-sm font-semibold" htmlFor="label">Name for this analysis</label>
              <Input id="label" value={label} onChange={(e) => setLabel(e.target.value)} placeholder="production" />
            </>
          ) : (
            <>
              <label className="text-sm font-semibold" htmlFor="branch">Branch</label>
              <Input id="branch" value={branch} onChange={(e) => setBranch(e.target.value)} placeholder="main" />
            </>
          )}
          <div className="flex items-center gap-2">
            <Checkbox id="resume" checked={resume} onCheckedChange={(v) => setResume(v === true)} />
            <label htmlFor="resume" className="text-sm">Resume where it stopped</label>
          </div>
          <div className="flex justify-end gap-2">
            <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={!ready}>Start analysis</Button>
          </div>
        </form>
      </DialogContent>
    </Dialog>
  );
}

export function Library() {
  const { api, current, project, go, set } = useApp();
  const [data, setData] = useState<BranchesResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [jobs, setJobs] = useState<Record<string, string>>({});
  const [analysing, setAnalysing] = useState(false);
  const [login, setLogin] = useState(false);
  const [dismissed, setDismissed] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!project) return;
    try {
      setData(await api.branches(project));
      setError(null);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  }, [api, project]);
  useEffect(() => { void load(); }, [load]);
  const start = async (branch: string, fn: () => Promise<{ job_id: string }>) => {
    try {
      const { job_id } = await fn();
      setJobs((j) => ({ ...j, [branch]: job_id }));
    } catch (e) {
      if (e instanceof ApiError && e.status === 401) setLogin(true);
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  };
  const finished = (branch: string) => {
    setJobs((j) => { const n = { ...j }; delete n[branch]; return n; });
    void load();
  };
  if (!project) return <Empty />;
  const rows = (data?.rows ?? []).filter((r) => r.branch.toLowerCase().includes(query.toLowerCase()));
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight">Library</h1>
          <p className="mt-1.5 text-muted">Analysed branches you can open, ask questions of, or share.</p>
        </div>
        <div className="flex flex-wrap gap-2">
          <label className="sr-only" htmlFor="search">Search branches</label>
          <Input id="search" type="search" placeholder="Search branches" value={query} onChange={(e) => setQuery(e.target.value)} className="w-60" />
          <Button onClick={() => setAnalysing(true)}>Analyse a branch</Button>
        </div>
      </div>
      {(error ?? data?.registry_error) && dismissed !== (error ?? data?.registry_error) && (
        <div role="alert" className="flex items-start justify-between gap-4 rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">
          <span>{error ?? data?.registry_error}</span>
          <button type="button" aria-label="Dismiss" className="font-semibold" onClick={() => setDismissed(error ?? data?.registry_error ?? null)}>×</button>
        </div>
      )}
      {current && (
        <Card className="flex flex-wrap items-center gap-6 px-6 py-5">
          <div className="flex min-w-[260px] flex-1 flex-col gap-1.5">
            <div className="text-lg font-semibold">{current.name}</div>
            <div className="text-muted">{current.repos?.length ?? 0} repos · {current.registry ? `registry ${hostOf(current.registry)}` : "no registry"}</div>
          </div>
          <div className="flex flex-wrap gap-2">
            {current.repos?.map((r) => <span key={r.name} className="rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs"><b className="font-semibold">{r.name}</b> · {r.role}</span>)}
            {!!current.linkers?.length && <span className="rounded-md border border-line bg-bg px-2.5 py-1.5 text-xs">Linkers: {current.linkers.join(" · ")}</span>}
          </div>
        </Card>
      )}
      <Card className="overflow-x-auto">
        <table className="w-full min-w-[860px] border-collapse">
          <thead>
            <tr className="text-left text-xs uppercase tracking-wide text-muted">
              {["Branch", "Commits", "Coverage", "Knowledge", "Status", "Action"].map((h, i) => (
                <th key={h} className={`border-b border-line py-3.5 font-semibold ${i === 0 ? "px-6" : i === 5 ? "px-6 text-right" : "px-4"}`}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => {
              const lo = r.local;
              const commits = lo?.commits ?? r.remote?.commits ?? {};
              const jobId = jobs[r.branch] ?? (r.state === "in-progress" ? r.job_id : null);
              const badge = jobId ? BADGE["in-progress"] : BADGE[r.state];
              return (
                <tr key={r.branch} className="border-b border-[#EFEFEC] last:border-0">
                  <td className="px-6 py-4">
                    <div className="font-semibold">{r.branch}</div>
                    <div className="text-xs text-muted">{lo ? `Analysed ${ago(lo.created_at)}${lo.author ? ` by ${lo.author}` : ""}` : r.remote ? `Shared by ${r.remote.created_by}` : ""}</div>
                    {lo?.branches && new Set(Object.values(lo.branches)).size > 1 && (
                      <div className="font-mono text-xs text-muted">{Object.entries(lo.branches).map(([k, v]) => `${k} ${v}`).join(" · ")}</div>
                    )}
                  </td>
                  <td className="px-4 py-4 font-mono text-xs text-muted">{Object.entries(commits).map(([k, v]) => <div key={k}>{k} {v.slice(0, 12)}</div>)}</td>
                  {jobId ? (
                    <td className="px-4 py-4" colSpan={2}><Progress jobId={jobId} onDone={() => finished(r.branch)} /></td>
                  ) : (
                    <>
                      <td className="px-4 py-4 text-[13px]">
                        {lo ? <><div>{lo.modules} modules</div><div className="text-muted">{lo.stats.links_resolved ?? 0} links · {lo.stats.links_unresolved ?? 0} unresolved</div></> : <span className="text-muted">—</span>}
                      </td>
                      <td className="px-4 py-4 text-[13px]">
                        <div>{lo ? `${lo.qa_total} Q&As` : `${r.remote?.overlay_count ?? 0} shared`}</div>
                        {!!lo?.testplans && <div className="text-muted">{lo.testplans} test plan{lo.testplans === 1 ? "" : "s"}</div>}
                      </td>
                    </>
                  )}
                  <td className="px-4 py-4"><Badge tone={badge.tone}>{badge.text}</Badge></td>
                  <td className="px-6 py-4 text-right">
                    <div className="flex justify-end gap-2">
                      {jobId ? null : r.state === "synced" || r.state === "update-available" ? (
                        <>
                          {r.state === "update-available" && <Button variant="secondary" size="sm" onClick={() => void start(r.branch, () => api.pull(project, r.branch, false))}>Pull</Button>}
                          <Button variant="secondary" size="sm" onClick={() => go("map", { branch: r.branch })}>Open</Button>
                        </>
                      ) : r.state === "not-pulled" ? (
                        <Button variant="secondary" size="sm" onClick={() => void start(r.branch, () => api.pull(project, r.branch, false))}>Pull</Button>
                      ) : r.state === "local-only" ? (
                        <>
                          {current?.registry && <Button size="sm" onClick={() => void start(r.branch, () => api.push(project, r.branch))}>Push to registry</Button>}
                          <Button variant="secondary" size="sm" onClick={() => go("map", { branch: r.branch })}>Open</Button>
                        </>
                      ) : null}
                    </div>
                  </td>
                </tr>
              );
            })}
            {data && rows.length === 0 && (
              <tr><td colSpan={6} className="px-6 py-10 text-center text-muted">No analysed branches yet. Use “Analyse a branch” to build the first map.</td></tr>
            )}
          </tbody>
        </table>
      </Card>
      <AnalyseDialog open={analysing} onOpenChange={setAnalysing} repos={current?.repos ?? []}
        onStart={(b, resume, branches) => { set({ branch: b }); void start(b, () => (branches ? api.analyze(project, b, resume, branches) : api.analyze(project, b, resume))); }} />
      <LoginDialog open={login} onOpenChange={setLogin} />
    </div>
  );
}

function Empty() {
  const [adding, setAdding] = useState(false);
  return (
    <div className="flex flex-col items-start gap-3">
      <h1 className="text-[26px] font-bold">Library</h1>
      <p className="text-muted">Add a project to get started: give KB the GitHub links or folders of its repos.</p>
      <Button onClick={() => setAdding(true)}>Add a project</Button>
      <AddProjectDialog open={adding} onOpenChange={setAdding} />
    </div>
  );
}
