import type { CaseSlot, ImpactFlow, Priority } from "./api/types";

export const PRIORITIES: Priority[] = ["P0", "P1", "P2", "P3"];
const CASES_PER_CALL = 15;

/** Same order as the engine's `select`: priority, then flow risk, then slot id. */
export function selectSlots(slots: CaseSlot[], flows: ImpactFlow[], counts: Partial<Record<Priority, number>>, perFlowCap: number | null,
  statuses: string[] = ["planned"]): CaseSlot[] {
  const risk = new Map(flows.map((f) => [f.id, f.risk]));
  const order = slots.filter((s) => statuses.includes(s.status)).sort((a, b) =>
    PRIORITIES.indexOf(a.priority) - PRIORITIES.indexOf(b.priority) || (risk.get(b.flow_id) ?? 0) - (risk.get(a.flow_id) ?? 0)
    || (a.id < b.id ? -1 : a.id > b.id ? 1 : 0));
  const left: Record<string, number> = Object.fromEntries(PRIORITIES.map((p) => [p, Math.max(0, counts[p] ?? 0)]));
  const perFlow = new Map<string, number>();
  const out: CaseSlot[] = [];
  for (const s of order) {
    if (left[s.priority] <= 0) continue;
    if (perFlowCap !== null && (perFlow.get(s.flow_id) ?? 0) >= perFlowCap) continue;
    out.push(s);
    left[s.priority] -= 1;
    perFlow.set(s.flow_id, (perFlow.get(s.flow_id) ?? 0) + 1);
  }
  return out;
}

/** Mirrors the engine's estimate_calls (spec §8). haveScenarios holds "flow|type" keys. */
export function estimateCalls(selected: CaseSlot[], flows: ImpactFlow[], haveTrace: Set<string>, haveScenarios: Set<string>, mode: "pr" | "scenario"): number {
  const byFlow = new Map<string, CaseSlot[]>();
  for (const s of selected) byFlow.set(s.flow_id, [...(byFlow.get(s.flow_id) ?? []), s]);
  const fl = new Map(flows.map((f) => [f.id, f]));
  let total = 0;
  for (const [fid, ss] of byFlow) {
    const f = fl.get(fid);
    if (mode === "pr" && f && f.master_flow_id === null && !haveTrace.has(`${fid}:base`)) total += 1;
    if (!haveTrace.has(`${fid}:head`)) total += 1;
    if (ss.some((s) => !haveScenarios.has(`${fid}|${s.scenario_type}`))) total += 1;
    total += Math.ceil(ss.length / CASES_PER_CALL);
  }
  return total;
}
