import { useCallback, useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import type { BranchRow, PlanSummary, SharedPlanRow } from "../api/types";
import { useJob } from "../api/useJob";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { Spinner } from "../components/ui/spinner";
import { cn } from "../lib";
import { useApp } from "../state";

type Tab = "pr" | "scenario";
const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

export function describe(branches: Record<string, string>) {
  const vals = new Set(Object.values(branches));
  return vals.size === 1 ? [...vals][0] : Object.entries(branches).map(([k, v]) => `${k} ${v}`).join(" · ");
}

const same = (a: Record<string, string> | undefined, b: Record<string, string>) =>
  !!a && Object.keys(b).length === Object.keys(a).length && Object.entries(b).every(([k, v]) => a[k] === v);

function Progress({ jobId, onDone }: { jobId: string; onDone(result: Record<string, unknown> | null, error: string | null): void }) {
  const { api } = useApp();
  const { job, done, failed } = useJob<Record<string, unknown>>(api, jobId);
  useEffect(() => {
    if (done) onDone(job?.result ?? null, null);
    if (failed) onDone(null, job?.error ?? "failed");
  }, [done, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <div className="flex flex-col gap-1.5" aria-busy="true">
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((job?.progress ?? 0) * 100)} className="h-2 overflow-hidden rounded bg-priv-soft">
        <div className="h-2 bg-accent" style={{ width: `${Math.max(5, Math.round((job?.progress ?? 0) * 100))}%` }} />
      </div>
      <div className="flex items-center gap-2 text-xs text-muted"><Spinner />{job?.step || "Starting…"}</div>
    </div>
  );
}

function PrForm({ rows, onStarted }: { rows: BranchRow[]; onStarted(jobId: string): void }) {
  const { api, project, current, set } = useApp();
  const repos = current?.repos ?? [];
  const [base, setBase] = useState<Record<string, string>>({});
  const [head, setHead] = useState<Record<string, string>>({});
  const [links, setLinks] = useState<Record<string, string>>({});
  const [linkNote, setLinkNote] = useState<Record<string, { title?: string; error?: string }>>({});
  const [lookedUp, setLookedUp] = useState<Record<string, string>>({});
  const lookUp = async (repo: string) => {
    const url = (links[repo] ?? "").trim();
    if (url === (lookedUp[repo] ?? "")) return; // unchanged link: keep any edits made since
    setLookedUp((l) => ({ ...l, [repo]: url }));
    if (!url) return setLinkNote((n) => ({ ...n, [repo]: {} }));
    try {
      // the PR's own base and branch (a fork's branch is pull/<n>/head) replace what was filled in
      const pr = await api.prInfo(url);
      if (pr.base_ref) setBase((b) => ({ ...b, [repo]: pr.base_ref }));
      if (pr.head_ref) setHead((h) => ({ ...h, [repo]: pr.head_ref }));
      setLinkNote((n) => ({ ...n, [repo]: { title: pr.title } }));
    } catch (e) {
      setLinkNote((n) => ({ ...n, [repo]: { error: msg(e) } }));
    }
  };
  const [error, setError] = useState<string | null>(null);
  const [analysing, setAnalysing] = useState(false);
  useEffect(() => {
    setBase(Object.fromEntries(repos.map((r) => [r.name, r.default_branch ?? ""])));
  }, [current?.name]); // eslint-disable-line react-hooks/exhaustive-deps
  const baseSet = Object.fromEntries(repos.map((r) => [r.name, (base[r.name] ?? "").trim()]));
  const complete = repos.length > 0 && Object.values(baseSet).every(Boolean);
  const found = complete ? rows.find((r) => r.local && same(r.local.branches ?? undefined, baseSet)) : undefined;
  const anyHead = repos.some((r) => (head[r.name] ?? "").trim() || (links[r.name] ?? "").trim());
  const submit = async () => {
    setError(null);
    try {
      const body = {
        base: baseSet,
        head: Object.fromEntries(repos.map((r) => [r.name, (head[r.name] ?? "").trim() || null])),
        pr_links: Object.fromEntries(repos.filter((r) => (links[r.name] ?? "").trim()).map((r) => [r.name, links[r.name].trim()])),
      };
      const { job_id } = await api.newPrPlan(project!, body);
      onStarted(job_id);
    } catch (e) {
      setError(msg(e));
    }
  };
  const analyse = async () => {
    const vals = new Set(Object.values(baseSet));
    try {
      if (vals.size === 1) await api.analyze(project!, [...vals][0], false);
      else await api.analyze(project!, "production", false, baseSet);
      setAnalysing(true);
      set({ view: "library" });
    } catch (e) {
      setError(msg(e));
    }
  };
  return (
    <Card className="flex flex-col gap-3 p-5">
      <h2 className="text-lg font-semibold">New PR test plan</h2>
      <p className="text-sm text-muted">Each repo has its own base. Leave the PR branch empty for a repo the PR doesn't change.</p>
      <form aria-label="New PR test plan" className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void submit(); }}>
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] border-collapse text-sm">
            <thead>
              <tr className="text-left text-xs uppercase tracking-wide text-muted">
                <th className="py-1 pr-2 font-semibold">Repo</th><th className="px-2 py-1 font-semibold">Base (production)</th><th className="px-2 py-1 font-semibold">PR branch</th>
              </tr>
            </thead>
            <tbody>
              {repos.map((r) => (
                <tr key={r.name} className="align-top">
                  <td className="py-1.5 pr-2"><div className="font-semibold">{r.name}</div><div className="text-xs text-muted">{r.role}</div></td>
                  <td className="px-2 py-1.5">
                    <Input aria-label={`${r.name} base branch`} value={base[r.name] ?? ""} placeholder="master"
                      onChange={(e) => setBase((b) => ({ ...b, [r.name]: e.target.value }))} />
                  </td>
                  <td className="px-2 py-1.5">
                    <div className="flex flex-col gap-1.5">
                      <Input aria-label={`${r.name} PR branch`} value={head[r.name] ?? ""} placeholder="No changes"
                        onChange={(e) => setHead((h) => ({ ...h, [r.name]: e.target.value }))} />
                      <Input aria-label={`${r.name} PR link`} value={links[r.name] ?? ""} placeholder="GitHub PR link (optional)" className="text-xs"
                        onChange={(e) => setLinks((l) => ({ ...l, [r.name]: e.target.value }))} onBlur={() => void lookUp(r.name)} />
                      {linkNote[r.name]?.title && <div className="text-xs text-muted">PR: {linkNote[r.name].title}</div>}
                      {linkNote[r.name]?.error && <div className="text-xs text-warn">{linkNote[r.name].error}</div>}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        {complete && (found ? (
          <p className="text-sm text-muted">{`Base analysis found: ${found.branch} (${describe(baseSet)}).`}</p>
        ) : (
          <div className="flex flex-wrap items-center gap-2 rounded-lg bg-warn-soft px-3 py-2 text-sm text-warn">
            <span>Analyse {describe(baseSet)} first. Test plans compare the PR with an analysis of its base.</span>
            <Button type="button" size="sm" variant="secondary" onClick={() => void analyse()} disabled={analysing}>Analyse</Button>
          </div>
        ))}
        {error && <p role="alert" className="text-sm text-warn">{error}</p>}
        <div className="flex flex-wrap items-center gap-3">
          <Button type="submit" disabled={!found || !anyHead}>Find what changed</Button>
          <span className="text-xs text-muted">Reads the diff and the code graph. No Claude calls except naming the affected flows.</span>
        </div>
      </form>
    </Card>
  );
}

function ScenarioForm({ rows, onStarted }: { rows: BranchRow[]; onStarted(jobId: string): void }) {
  const { api, project } = useApp();
  const analysed = rows.filter((r) => r.local);
  const [text, setText] = useState("");
  const [label, setLabel] = useState("");
  const [error, setError] = useState<string | null>(null);
  const chosen = label || analysed[0]?.branch || "";
  return (
    <Card className="flex flex-col gap-3 p-5">
      <h2 className="text-lg font-semibold">New scenario test plan</h2>
      <form aria-label="New scenario test plan" className="flex flex-col gap-3" onSubmit={async (e) => {
        e.preventDefault();
        setError(null);
        try {
          const { job_id } = await api.newScenarioPlan(project!, { label: chosen, text: text.trim() });
          onStarted(job_id);
        } catch (err) {
          setError(msg(err));
        }
      }}>
        <label className="flex flex-col gap-1 text-sm font-semibold">Scenario
          <Input value={text} onChange={(e) => setText(e.target.value)} placeholder="Admin schedules a task that repeats every week" />
        </label>
        <label className="flex flex-col gap-1 text-sm font-semibold">Analysed branch
          <select className="rounded-lg border border-line bg-bg px-3 py-2 text-sm font-normal" value={chosen} onChange={(e) => setLabel(e.target.value)}>
            {analysed.map((r) => (
              <option key={r.branch} value={r.branch}>{r.branch}{r.local?.branches && new Set(Object.values(r.local.branches)).size > 1 ? ` · ${describe(r.local.branches)}` : ""}</option>
            ))}
          </select>
        </label>
        {error && <p role="alert" className="text-sm text-warn">{error}</p>}
        <div><Button type="submit" disabled={!text.trim() || !chosen}>Plan test cases</Button></div>
        <p className="text-xs text-muted">You'll see how many cases are planned per priority before anything is generated. You can also start from <b>Make test plan</b> on any saved answer in Ask or Memory.</p>
      </form>
    </Card>
  );
}

function PlanTable({ plans, kind, onOpen }: { plans: PlanSummary[]; kind: Tab; onOpen(p: PlanSummary): void }) {
  if (!plans.length) return <p className="text-sm text-muted">No {kind === "pr" ? "PR test plans" : "scenario test plans"} yet.</p>;
  return (
    <Card className="overflow-x-auto">
      <table className="w-full min-w-[720px] border-collapse">
        <thead>
          <tr className="text-left text-xs uppercase tracking-wide text-muted">
            {[kind === "pr" ? "Plan" : "Scenario", kind === "pr" ? "Change" : "Branch", "Progress", ""].map((h, i) => (
              <th key={i} className="border-b border-line px-4 py-3 font-semibold">{h}</th>
            ))}
          </tr>
        </thead>
        <tbody>
          {plans.map((p) => {
            const total = Object.values(p.counts_by_status).reduce((a, b) => a + b, 0) - (p.counts_by_status.obsolete ?? 0);
            const pct = total ? Math.round((100 * p.cases_written) / total) : 0;
            const toImpact = p.kind === "pr" && p.cases_written === 0;
            return (
              <tr key={p.id} className="border-b border-line last:border-0 align-top">
                <td className="px-4 py-3"><div className="font-semibold">{p.title}</div><div className="font-mono text-xs text-muted">{p.id}</div></td>
                <td className="px-4 py-3 font-mono text-xs">
                  {p.kind === "pr" ? Object.keys(p.base).map((r) => (
                    <div key={r}>{r}: {p.base[r]} {p.head[r] && p.head[r] !== p.base[r] ? `→ ${p.head[r]}` : "(no changes)"}</div>
                  )) : p.label}
                </td>
                <td className="min-w-[180px] px-4 py-3">
                  <div className="h-1.5 overflow-hidden rounded-full bg-priv-soft"><div className="h-1.5 bg-ok" style={{ width: `${pct}%` }} /></div>
                  <div className="mt-1 text-xs text-muted">{p.cases_written} of {total} written</div>
                </td>
                <td className="px-4 py-3 text-right">
                  <Button size="sm" variant="secondary" onClick={() => onOpen(p)}>{toImpact ? "Choose cases" : "Open board"}</Button>
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </Card>
  );
}

function SharedPlans({ rows, onOpen }: { rows: SharedPlanRow[]; onOpen(r: SharedPlanRow): void }) {
  const fresh = rows.filter((r) => !r.local);
  if (!fresh.length) return null;
  return (
    <section aria-label="Shared with the team">
      <Card className="flex flex-col gap-2 p-5">
        <h2 className="font-semibold">Shared with the team</h2>
        <p className="text-xs text-muted">Plans teammates shared. Opening one downloads it; cases you generate are reserved so nobody writes them twice.</p>
        {fresh.map((r) => (
          <div key={`${r.label}/${r.plan_id}`} className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-2">
            <span><b>{r.title}</b> <span className="font-mono text-xs text-muted">{r.label} · {r.plan_id}</span></span>
            <Button size="sm" variant="secondary" onClick={() => onOpen(r)}>Open</Button>
          </div>
        ))}
      </Card>
    </section>
  );
}

export function TestPlans() {
  const { api, project, set, go, planJobId, current } = useApp();
  const [shared, setShared] = useState<SharedPlanRow[]>([]);
  const [tab, setTab] = useState<Tab>("pr");
  const [plans, setPlans] = useState<PlanSummary[]>([]);
  const [rows, setRows] = useState<BranchRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!project) return;
    try {
      const [ps, br] = await Promise.all([api.testplans(project), api.branches(project)]);
      setPlans(ps ?? []);
      setRows(br?.rows ?? []);
    } catch (e) {
      setError(msg(e));
    }
  }, [api, project]);
  useEffect(() => { void load(); }, [load]);
  useEffect(() => {
    if (!project || !current?.registry) return;
    api.sharedTestplans(project).then((r) => setShared(r ?? []), () => setShared([]));  // registry unreachable: nothing to list
  }, [api, project, current?.registry]);
  const byKind = useMemo(() => ({ pr: plans.filter((p) => p.kind === "pr"), scenario: plans.filter((p) => p.kind === "scenario") }), [plans]);
  if (!project) return <p className="text-muted">Add a project first.</p>;
  const started = (jobId: string) => set({ planJobId: jobId });
  return (
    <div className="flex flex-col gap-5">
      <div>
        <h1 className="text-[26px] font-bold tracking-tight">Test plans</h1>
        <p className="mt-1.5 text-muted">Code-backed test cases for your QA team.</p>
      </div>
      {error && <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>}
      <div role="tablist" aria-label="Kind of test plan" className="flex gap-1 border-b border-line">
        {([["pr", "Test Plan for PR"], ["scenario", "Test Scenarios in a Branch"]] as const).map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
            className={cn("-mb-px border-b-2 px-3 py-2.5 text-sm", tab === id ? "border-accent font-semibold text-ink" : "border-transparent text-muted")}>
            {label} <span className="ml-1 rounded-full bg-priv-soft px-1.5 text-xs text-muted">{byKind[id].length}</span>
          </button>
        ))}
      </div>
      <p className="text-sm text-muted">{tab === "pr" ? "What changed in a pull request, what it could break, and the cases to check it."
        : "Complete test cases for how a flow works today, for regression suites, release passes or features merged without QA."}</p>
      {planJobId ? (
        <Card className="p-5">
          <Progress jobId={planJobId} onDone={(result, err) => {
            set({ planJobId: null });
            if (err) { setError(err); return; }
            const id = String(result?.plan_id ?? "");
            void load();
            // a pulled team plan opens on its board; a new PR plan on its impact
            if (id) go("testplans", { planId: id, planScreen: id.startsWith("sc-") || result?.label ? "board" : "impact" });
          }} />
        </Card>
      ) : tab === "pr" ? <PrForm rows={rows} onStarted={started} /> : <ScenarioForm rows={rows} onStarted={started} />}
      <SharedPlans rows={shared} onOpen={(r) => void (async () => {
        try { set({ planJobId: (await api.pullTestplan(project, r.label, r.plan_id)).job_id }); } catch (e) { setError(msg(e)); }
      })()} />
      <PlanTable plans={byKind[tab]} kind={tab} onOpen={(p) => go("testplans", { planId: p.id, planScreen: p.kind === "pr" && p.cases_written === 0 ? "impact" : "board" })} />
    </div>
  );
}
