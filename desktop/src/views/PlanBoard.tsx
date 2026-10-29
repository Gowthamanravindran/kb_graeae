import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { CaseSlot, PlanCase, Priority, ScenarioType, TestPlanDetail } from "../api/types";
import { useJob } from "../api/useJob";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { PRIORITIES } from "../testplan";
import { useApp } from "../state";
import { ClaudeBanner, GeneratePanel } from "./Impact";

const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));
const TYPES: [ScenarioType, string][] = [["happy_path", "Happy path"], ["branches_edges", "Branches and edges"],
  ["errors_failures", "Errors and failures"], ["regression", "Regression"], ["cross_side", "Cross-side effects"]];
const PRIO_COLOR: Record<Priority, string> = { P0: "text-warn", P1: "text-accent", P2: "text-ink", P3: "text-muted" };

function Prio({ p }: { p: Priority }) {
  return <span className={`rounded border border-current px-1.5 font-mono text-[11px] ${PRIO_COLOR[p]}`}>{p}</span>;
}

function JobStrip({ jobId, onDone }: { jobId: string; onDone(error: string | null): void }) {
  const { api } = useApp();
  const { job, done, failed } = useJob(api, jobId);
  useEffect(() => { if (done || failed) onDone(failed ? job?.error ?? "failed" : null); }, [done, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card className="flex flex-col gap-1.5 p-4" aria-busy="true">
      <div role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round((job?.progress ?? 0) * 100)} className="h-2 overflow-hidden rounded bg-priv-soft">
        <div className="h-2 bg-accent" style={{ width: `${Math.max(5, Math.round((job?.progress ?? 0) * 100))}%` }} />
      </div>
      <div className="text-sm text-accent">{job?.step || "Starting…"}</div>
    </Card>
  );
}

function CaseRow({ c }: { c: PlanCase }) {
  return (
    <details className="border-t border-line first:border-0">
      <summary className="grid cursor-pointer grid-cols-[64px_36px_minmax(0,1fr)_auto] items-center gap-3 px-5 py-2.5">
        <span className="font-mono text-xs">{c.id}</span><Prio p={c.priority} /><span className="min-w-0">{c.title}</span>
        {c.stale ? <Badge tone="warn">Stale: code changed</Badge> : <Badge tone="ok">Written · {c.author}</Badge>}
      </summary>
      <div className="grid gap-4 px-5 pb-4 pt-1 text-sm sm:grid-cols-2 lg:grid-cols-4">
        {([["Preconditions", c.preconditions, false], ["Steps", c.steps, true], ["Test data", c.test_data, false], ["Expected result", c.expected, false]] as const).map(([h, items, ordered]) => (
          <div key={h}>
            <h4 className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-muted">{h}</h4>
            {items.length === 0 ? <p className="text-muted">—</p> : ordered
              ? <ol className="list-decimal pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ol>
              : <ul className="list-disc pl-5">{items.map((x, i) => <li key={i}>{x}</li>)}</ul>}
          </div>
        ))}
      </div>
    </details>
  );
}

export function PlanBoard() {
  const { api, project, planId, planJobId, set, go } = useApp();
  const [d, setD] = useState<TestPlanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<"all" | Priority>("all");
  const [more, setMore] = useState(false);
  const [note, setNote] = useState<string | null>(null);
  const load = useCallback(async () => {
    if (!project || !planId) return;
    try { setD(await api.testplan(project, planId)); } catch (e) { setError(msg(e)); }
  }, [api, project, planId]);
  useEffect(() => { void load(); }, [load]);
  if (error && !d) return <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>;
  if (!d) return <p className="text-muted">Loading the plan…</p>;
  const act = async (fn: () => Promise<unknown>) => { setError(null); try { await fn(); } catch (e) { setError(msg(e)); } };
  const job = (fn: () => Promise<{ job_id: string }>) => act(async () => { const { job_id } = await fn(); set({ planJobId: job_id }); });
  const flows = d.impact?.flows ?? [];
  const live = d.slots.filter((s) => s.status !== "obsolete");
  const scen = new Map(d.scenarios.map((s) => [s.id, s]));
  const caseById = new Map(d.cases.map((c) => [c.id, c]));
  const shown = (p: Priority) => filter === "all" || filter === p;
  const covCount = (k: string) => Object.values(d.coverage.items).filter((v) => v === k).length;
  const exportCsv = () => act(async () => {
    const bytes = await api.exportCsv(project!, planId!, filter === "all" ? {} : { priority: [filter] });
    const r = await window.kb?.saveFile(`${project}-${planId}-test-cases.csv`, bytes);
    if (r?.saved) setNote(`Saved ${r.path ?? "the CSV"}.`);
  });
  const typeOf = (s: CaseSlot, c?: PlanCase): ScenarioType => (c?.scenario_id && scen.get(c.scenario_id)?.type) || s.scenario_type;
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[26px] font-bold tracking-tight">{d.change_set.title}</h1>
          <p className="mt-1.5 text-muted">{d.change_set.kind === "pr" ? "Test plan for a pull request" : "Test scenarios"} on <span className="font-mono text-sm">{project}@{d.change_set.base_pack}</span></p>
        </div>
        <div className="flex flex-wrap gap-2">
          <select aria-label="Filter by priority" className="rounded-lg border border-line bg-surface px-3 py-2 text-sm" value={filter}
            onChange={(e) => setFilter(e.target.value as "all" | Priority)}>
            <option value="all">All priorities</option>{PRIORITIES.map((p) => <option key={p} value={p}>{p}</option>)}
          </select>
          <Button variant="secondary" onClick={() => void exportCsv()}>Export CSV</Button>
          <Button variant="secondary" onClick={() => setMore((m) => !m)}>Generate more</Button>
          <Button variant="ghost" onClick={() => go("testplans", { planScreen: "list" })}>All plans</Button>
        </div>
      </div>
      <ClaudeBanner />
      {error && <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>}
      {note && <div role="status" className="rounded-lg bg-ok-soft px-4 py-3 text-sm text-ok">{note}</div>}
      {planJobId && <JobStrip jobId={planJobId} onDone={(err) => { set({ planJobId: null }); if (err) setError(err); void load(); }} />}
      {more && !planJobId && (
        <GeneratePanel detail={d} flowIds={new Set(flows.map((f) => f.id))} label="Generate" onStart={async (counts, cap) => {
          await job(() => api.generate(project!, planId!, counts, cap));
          setMore(false);
        }} />
      )}
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        {PRIORITIES.map((p) => {
          const all = live.filter((s) => s.priority === p);
          const w = all.filter((s) => s.status === "written" || s.status === "stale").length;
          return (
            <Card key={p} className="flex flex-col gap-2 px-4 py-3">
              <div className="flex items-center justify-between"><Prio p={p} /><b className="tabular-nums">{w} / {all.length}</b></div>
              <div className="h-1.5 overflow-hidden rounded-full bg-priv-soft"><div className="h-1.5 bg-ok" style={{ width: `${all.length ? (100 * w) / all.length : 0}%` }} /></div>
              <span className="text-xs text-muted">{all.filter((s) => s.status === "planned").length} planned</span>
            </Card>
          );
        })}
      </div>
      <Card className="flex flex-wrap items-center gap-3 px-4 py-3 text-sm">
        <span><b>{Object.keys(d.coverage.items).length}</b> decision points</span>
        <Badge tone="ok">{covCount("covered")} covered</Badge>
        <Badge tone="priv">{covCount("skipped")} not generated yet</Badge>
        {covCount("failed") > 0 && <Badge tone="warn">{covCount("failed")} failed</Badge>}
        {covCount("uncovered") > 0 && <Badge tone="warn">{covCount("uncovered")} without a slot</Badge>}
      </Card>
      {flows.map((f) => {
        const mine = live.filter((s) => s.flow_id === f.id && shown(s.priority));
        if (!mine.length) return null;
        return (
          <section key={f.id} aria-label={f.name}>
            <Card className="overflow-hidden">
              <div className="flex flex-wrap items-center justify-between gap-2 border-b border-line px-5 py-3">
                <h2 className="font-semibold">{f.name}</h2>
                <span className="text-xs text-muted">{mine.filter((s) => s.status === "written" || s.status === "stale").length} of {mine.length} written</span>
              </div>
              {TYPES.map(([t, title]) => {
                const rows = mine.filter((s) => typeOf(s, caseById.get(s.case_id ?? "")) === t);
                if (!rows.length) return null;
                const planned = rows.filter((s) => s.status === "planned");
                return (
                  <div key={t}>
                    <div className="px-5 pb-1 pt-3 text-[11px] font-semibold uppercase tracking-wide text-muted">{title}</div>
                    {rows.filter((s) => s.status !== "planned").map((s) => {
                      const c = caseById.get(s.case_id ?? "");
                      if (c && (s.status === "written" || s.status === "stale")) return (
                        <div key={s.id}>
                          <CaseRow c={c} />
                          {s.status === "stale" && <div className="px-5 pb-2 text-right"><Button size="sm" variant="secondary" onClick={() => void job(() => api.retrySlot(project!, planId!, s.id))}>Regenerate</Button></div>}
                        </div>
                      );
                      return (
                        <div key={s.id} className="grid grid-cols-[64px_36px_minmax(0,1fr)_auto] items-center gap-3 border-t border-line px-5 py-2.5 text-muted">
                          <span className="font-mono text-xs">—</span><Prio p={s.priority} />
                          <span className="min-w-0">{s.status === "failed" ? <>Couldn't write this case. <span className="text-warn">{s.fail_reason}</span></> : `Generating (${s.rule})…`}</span>
                          {s.status === "failed" && <Button size="sm" variant="secondary" onClick={() => void job(() => api.retrySlot(project!, planId!, s.id))}>Retry</Button>}
                        </div>
                      );
                    })}
                    {planned.length > 0 && (
                      <div className="border-t border-line px-5 py-2 text-xs text-muted">
                        {planned.length} more planned ({PRIORITIES.map((p) => [p, planned.filter((s) => s.priority === p).length] as const).filter(([, n]) => n).map(([p, n]) => `${p} ${n}`).join(", ")})
                      </div>
                    )}
                  </div>
                );
              })}
            </Card>
          </section>
        );
      })}
      {d.extras.length > 0 && (
        <section aria-label="Suggested extras">
          <Card className="flex flex-col gap-2 p-5">
            <div className="flex items-center justify-between"><h2 className="font-semibold">Suggested extras</h2><span className="text-xs text-muted">Not counted against any limit</span></div>
            {d.extras.map((c) => (
              <div key={c.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-line px-3 py-2">
                <span>{c.title}</span>
                <span className="flex gap-2">
                  <Button size="sm" variant="secondary" onClick={() => void act(async () => { await api.acceptExtra(project!, planId!, c.id); await load(); })}>Accept</Button>
                  <Button size="sm" variant="ghost" onClick={() => void act(async () => { await api.dismissExtra(project!, planId!, c.id); await load(); })}>Dismiss</Button>
                </span>
              </div>
            ))}
          </Card>
        </section>
      )}
    </div>
  );
}
