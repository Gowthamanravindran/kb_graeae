import { useEffect, useMemo, useState } from "react";
import { ApiError } from "../api/client";
import type { Priority, TestPlanDetail } from "../api/types";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { PRIORITIES, estimateCalls, selectSlots } from "../testplan";
import { useApp } from "../state";
import { describe } from "./TestPlans";

const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));
const PRIO_COLOR: Record<Priority, string> = { P0: "text-warn", P1: "text-accent", P2: "text-ink", P3: "text-muted" };

export function ClaudeBanner() {
  const { status } = useApp();
  if (status?.claude.available !== false) return null;
  return (
    <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">
      Claude Code isn't available on this computer ({status.claude.reason}). You can still read plans; to generate cases, install Claude Code and run <code className="font-mono">claude</code> once to sign in.
    </div>
  );
}

export function GeneratePanel({ detail, flowIds, onStart, label = "Generate" }: {
  detail: TestPlanDetail; flowIds: Set<string>; label?: string;
  onStart(counts: Record<Priority, number>, cap: number | null): Promise<void>;
}) {
  const { status } = useApp();
  const flows = detail.impact?.flows ?? [];
  const planned = detail.slots.filter((s) => s.status === "planned" && flowIds.has(s.flow_id));
  const plannedBy = Object.fromEntries(PRIORITIES.map((p) => [p, planned.filter((s) => s.priority === p).length])) as Record<Priority, number>;
  const [want, setWant] = useState<Partial<Record<Priority, number>>>({});
  const [cap, setCap] = useState("");
  const counts = Object.fromEntries(PRIORITIES.map((p) => [p, Math.min(want[p] ?? (p === "P0" ? plannedBy.P0 : 0), plannedBy[p])])) as Record<Priority, number>;
  const capN = cap.trim() ? Math.max(0, parseInt(cap, 10) || 0) : null;
  const chosen = selectSlots(planned, flows, counts, capN);
  const haveScen = new Set(detail.scenarios.map((s) => `${s.flow_id}|${s.type}`));
  const calls = estimateCalls(chosen, flows, new Set(), haveScen, detail.change_set.kind === "scenario" ? "scenario" : "pr");
  const [busy, setBusy] = useState(false);
  return (
    <aside aria-label="What to generate now" className="w-full shrink-0 lg:w-[320px]">
      <Card className="flex flex-col gap-3 p-5 lg:sticky lg:top-4">
        <h2 className="font-semibold">What to generate now</h2>
        <p className="text-xs text-muted">Slots are filled by priority, then by flow risk. The rest stay planned for later.</p>
        <div className="grid grid-cols-[64px_1fr_76px] items-center gap-x-2 gap-y-2 text-sm">
          <span className="text-xs uppercase tracking-wide text-muted">Priority</span><span className="text-xs uppercase tracking-wide text-muted">Planned</span><span className="text-right text-xs uppercase tracking-wide text-muted">Now</span>
          {PRIORITIES.map((p) => (
            <div key={p} className="contents">
              <span className={`font-mono text-xs font-semibold ${PRIO_COLOR[p]}`}>{p}</span>
              <span className="tabular-nums text-muted">{plannedBy[p]} planned</span>
              <Input type="number" min={0} max={plannedBy[p]} aria-label={`${p} cases to generate now`} className="text-right tabular-nums"
                value={String(counts[p])} onChange={(e) => setWant((w) => ({ ...w, [p]: Math.max(0, parseInt(e.target.value || "0", 10) || 0) }))} />
            </div>
          ))}
        </div>
        <label className="flex items-center justify-between gap-2 text-sm font-semibold">Max per flow
          <Input type="number" min={0} placeholder="No limit" className="w-24 text-right" value={cap} onChange={(e) => setCap(e.target.value)} />
        </label>
        <div className="border-t border-line pt-3 text-sm">
          <div>{`Will generate ${chosen.length} of ${planned.length}`}</div>
          <div className="text-xs text-muted">{chosen.length ? `about ${calls} Claude calls` : "Nothing selected"}</div>
        </div>
        <Button disabled={!chosen.length || busy || status?.claude.available === false}
          onClick={async () => { setBusy(true); try { await onStart(counts, capN); } finally { setBusy(false); } }}>{label}</Button>
      </Card>
    </aside>
  );
}

export function Impact() {
  const { api, project, planId, go } = useApp();
  const [detail, setDetail] = useState<TestPlanDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [ticked, setTicked] = useState<Set<string> | null>(null);
  useEffect(() => {
    if (!project || !planId) return;
    api.testplan(project, planId).then((d) => { setDetail(d); setTicked(new Set((d.impact?.flows ?? []).map((f) => f.id))); },
      (e) => setError(msg(e)));
  }, [api, project, planId]);
  const inv = useMemo(() => new Map((detail?.impact?.inventory ?? []).map((i) => [i.id, i])), [detail]);
  if (error) return <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>;
  if (!detail || !ticked) return <p className="text-muted">Loading the impact…</p>;
  const cs = detail.change_set;
  const imp = detail.impact;
  const flows = imp?.flows ?? [];
  const planned = (fid: string, p: Priority) => detail.slots.filter((s) => s.flow_id === fid && s.status === "planned" && s.priority === p).length;
  const headBranches = Object.fromEntries(Object.entries(cs.head).map(([r, h]) => [r, h.branch]));
  const start = async (counts: Record<Priority, number>, cap: number | null) => {
    try {
      const all = flows.every((f) => ticked.has(f.id));
      const { job_id } = all ? await api.generate(project!, planId!, counts, cap) : await api.generate(project!, planId!, counts, cap, [...ticked]);
      go("testplans", { planScreen: "board", planJobId: job_id });
    } catch (e) {
      setError(msg(e));
    }
  };
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div className="min-w-0">
          <h1 className="text-[26px] font-bold tracking-tight">{cs.title}</h1>
          <div className="mt-1.5 flex flex-col font-mono text-xs text-muted">
            {Object.keys(cs.base).map((r) => (
              <span key={r}>{r}: {cs.base[r].branch} {cs.base[r].commit.slice(0, 12)}{cs.head[r].commit !== cs.base[r].commit ? ` → ${cs.head[r].branch} ${cs.head[r].commit.slice(0, 12)}` : " (no changes)"}{cs.prs[r] ? ` (${cs.prs[r].url.split("/").slice(-2).join(" #").replace("pull #", "#")})` : ""}</span>
            ))}
            <span>Base analysis: {cs.base_pack}</span>
          </div>
        </div>
        <Button variant="secondary" onClick={() => go("testplans", { planScreen: "list" })}>Back to test plans</Button>
      </div>
      <ClaudeBanner />
      <Card className="flex flex-wrap gap-x-8 gap-y-3 px-5 py-4">
        {[[String(imp?.graph_delta.functions_changed ?? 0), "functions changed"], [String((imp?.graph_delta.repos_changed as string[] | undefined)?.length ?? 0), "repos"],
          [String(imp?.inventory.length ?? 0), "decision points"], [String(flows.length), "flows affected"],
          [String(detail.slots.filter((s) => s.status !== "obsolete").length), "cases planned"]].map(([n, l]) => (
          <div key={l} className="flex flex-col"><b className="text-lg tabular-nums">{n}</b><span className="text-xs text-muted">{l}</span></div>
        ))}
      </Card>
      {imp?.needs_full_reanalysis && (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">
          <span>{imp.full_reanalysis_reason} Flow names there may be rough.</span>
          <Button size="sm" variant="secondary" onClick={() => void api.analyze(project!, `${planId}-head`, false, headBranches).then(() => go("library"))}>
            Run full analysis of the PR branch
          </Button>
        </div>
      )}
      <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
        <Card className="min-w-0 flex-1">
          <div className="flex items-center justify-between border-b border-line px-5 py-3">
            <h2 className="font-semibold">Affected flows, highest risk first</h2>
            <span className="flex gap-3 font-mono text-xs">{PRIORITIES.map((p) => <span key={p} className={`w-6 text-right ${PRIO_COLOR[p]}`}>{p}</span>)}</span>
          </div>
          <ul>
            {flows.map((f) => (
              <li key={f.id} className="grid grid-cols-[20px_minmax(0,1fr)_auto] gap-3 border-b border-line px-5 py-4 last:border-0">
                <input type="checkbox" className="mt-1 h-4 w-4 accent-[var(--kb-accent,#2F5BD3)]" aria-label={`Include ${f.name}`} checked={ticked.has(f.id)}
                  onChange={(e) => setTicked((t) => { const n = new Set(t); if (e.target.checked) n.add(f.id); else n.delete(f.id); return n; })} />
                <div className="min-w-0">
                  <div className="font-semibold">{f.name}</div>
                  <div className="break-all font-mono text-xs text-muted">{f.entry_points[0]}</div>
                  <div className="mt-1.5 flex flex-wrap gap-1.5">
                    {f.risk_reasons.map((r) => <Badge key={r} tone="warn">{r}</Badge>)}
                    {f.master_flow_id ? <Badge tone="ok">Reuses master flow</Badge> : <Badge tone="priv">Needs master trace</Badge>}
                  </div>
                  <details className="mt-1.5">
                    <summary className="cursor-pointer text-sm text-accent">{f.inventory_ids.length} changed decision points</summary>
                    <ul className="mt-2 flex flex-col gap-1">
                      {f.inventory_ids.map((id) => inv.get(id)).filter(Boolean).map((i) => (
                        <li key={i!.id} className="grid grid-cols-[86px_minmax(0,1fr)] gap-2 text-sm">
                          <span className="pt-0.5 text-[10.5px] uppercase tracking-wide text-muted">{i!.kind}</span><span>{i!.summary}</span>
                        </li>
                      ))}
                    </ul>
                  </details>
                </div>
                <div className="flex gap-3 font-mono text-xs tabular-nums">
                  {PRIORITIES.map((p) => <span key={p} className={`w-6 text-right ${PRIO_COLOR[p]}`}>{planned(f.id, p)}</span>)}
                </div>
              </li>
            ))}
            {flows.length === 0 && <li className="px-5 py-8 text-center text-muted">No flows reach the changed code. {describe(headBranches)} may only change tests or configuration.</li>}
          </ul>
        </Card>
        <GeneratePanel detail={detail} flowIds={ticked} onStart={start} />
      </div>
    </div>
  );
}
