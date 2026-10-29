import type { CaseSlot, ImpactFlow, InventoryItem, TestPlanDetail } from "../api/types";

const flow = (o: Partial<ImpactFlow>): ImpactFlow => ({
  id: "f1", name: "Admin sets a task to repeat weekly", entry_points: ["sop-web:route:/tasks/create-task"], changed_functions: [],
  inventory_ids: ["i1", "i2"], modules: [], master_flow_id: null, risk: 9, risk_reasons: ["removed behaviour", "crosses repos"],
  est_claude_calls: 4, crosses_repos: true, has_job: false, ...o,
});
const inv = (id: string, kind: string, summary: string): InventoryItem => ({ id, kind, change: "added", repo: "sop-backend", file: "r.ts", lines: [1, 1], function: "AddTask", summary, outcomes: [] });
const slot = (id: string, flow_id: string, priority: CaseSlot["priority"], o: Partial<CaseSlot> = {}): CaseSlot => ({
  id, flow_id, scenario_type: "branches_edges", inventory_ids: [], outcome: "", priority, rule: "branch", status: "planned", case_id: null, fail_reason: null, ...o,
});

export const DETAIL: TestPlanDetail = {
  summary: null,
  change_set: {
    id: "cs1", kind: "pr", project: "sop", title: "Tasks: weekly repeat with end date", base_pack: "production", prs: {}, scenario_text: null, answer_id: null,
    base: { "sop-web": { branch: "master", commit: "a1b2c3d4e5f6a7" }, "sop-backend": { branch: "develop", commit: "0d4c8e21b7a9aa" } },
    head: { "sop-web": { branch: "feature/x", commit: "7c9e0f12ab34cc" }, "sop-backend": { branch: "feature/x", commit: "55d0e1aa9f02dd" } },
  },
  impact: {
    change_set_id: "cs1", head_commits: {}, graph_delta: { functions_changed: 14, repos_changed: ["sop-web", "sop-backend"] },
    inventory: [inv("i1", "permission", "Permission check `isAdmin`"), inv("i2", "validation", "Validation `end > start`"), inv("i3", "branch", "Branch `repeat`")],
    flows: [flow({}), flow({ id: "f2", name: "Worker queues the next repeat", inventory_ids: ["i3"], risk: 4, risk_reasons: ["background job"], master_flow_id: "m1", est_claude_calls: 2 })],
    needs_full_reanalysis: true, full_reanalysis_reason: "New top-level folder sop-backend:recurrence isn't part of any module.",
  },
  slots: [
    ...Array.from({ length: 4 }, (_, i) => slot(`a${i}`, "f1", "P0")),
    ...Array.from({ length: 3 }, (_, i) => slot(`b${i}`, "f1", "P1")),
    ...Array.from({ length: 2 }, (_, i) => slot(`c${i}`, "f2", "P0")),
    slot("d0", "f2", "P2"), slot("w0", "f1", "P0", { status: "written", case_id: "TC-001" }),
  ],
  scenarios: [], cases: [], extras: [],
  coverage: { items: {}, by_priority: {}, by_status: {} },
};
