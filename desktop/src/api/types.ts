// Mirrors kb serve payloads (src/kb/serve).
export interface Status { version: string; claude: { available: boolean; reason: string | null }; langfuse: { configured: boolean; host?: string | null } }
export interface ProjectRow { name: string; config_path: string; registry: string | null; repos?: { name: string; role: string; default_branch?: string | null }[]; linkers?: string[]; error?: string }
export interface RegistryStatus { url: string | null; logged_in: boolean; email: string | null; reachable: boolean; sso: boolean }
export interface LocalBranch { version: number; status: string; commits: Record<string, string>; author: string | null; created_at: string | null; modules: number; qa_total: number; qa_published: number; synced: boolean; stats: Record<string, number>; branches?: Record<string, string>; testplans?: number }
export interface RemoteBranch { latest_version: number; overlay_count: number; created_by: string; commits: Record<string, string> }
export type BranchState = "synced" | "not-pulled" | "local-only" | "update-available" | "in-progress";
export interface BranchRow { branch: string; local: LocalBranch | null; remote: RemoteBranch | null; state: BranchState; job_id: string | null }
export interface BranchesResponse { rows: BranchRow[]; registry_error: string | null }
export interface EntryPoint { id: string; name: string; kind: string; file: string; line: number | null }
export interface MapModule { id: string; name: string; summary: string; layer: "product" | "behind"; size: number; top_paths: string[]; entry_points: EntryPoint[]; answered: { qa_id: string; question: string; author: string; visibility: string }[] }
export interface MapView { apps: { repo: string; role: string; label: string }[]; modules: MapModule[]; edges: { source: string; target: string; count: number }[]; system_md: string }
export interface Citation { repo: string; file: string; line_start: number; line_end: number; content_hash: string }
export interface Step { side: "ui" | "backend"; text: string; verified: boolean; citation: Citation | null }
export interface AskPayload {
  answer: string; reused: boolean; reused_flow_id: string | null; saved_qa_id: string | null; suggestions: string[]; warnings: string[];
  mermaid: string; not_found: boolean; steps: Step[];
  how: { modules: string[]; shortlist_size: number; claude_calls: number; files_read: number; tool_steps: number; seconds: number; trace_id: string | null };
}
export interface MemoryItem { qa_id: string; question: string; answer_preview: string; state: "private" | "published" | "queued"; stale: boolean; author: string; created_at: string; modules: string[]; citations: number; has_flow: boolean }
export interface MemoryView { items: MemoryItem[]; suggestions: { id: string; question: string; source_qa_id: string }[]; counts: { all: number; private: number; published: number; stale: number; suggested: number } }
export interface SavedQA { id: string; question: string; answer: string; visibility: string; stale: boolean; author: string; created_at: string; flow: { steps: Step[]; mermaid: string; module_ids: string[] } | null }
export interface Job<T = unknown> { id: string; kind: string; key: string | null; state: "queued" | "running" | "done" | "failed"; step: string; progress: number; result: T | null; error: string | null; created_at: string; queue_position?: number | null }
export interface SettingValue { set: boolean; last4: string | null; source: "file" | "environment" | null; value?: string | null }
export type SettingKey = "LANGFUSE_PUBLIC_KEY" | "LANGFUSE_SECRET_KEY" | "LANGFUSE_HOST" | "GITHUB_TOKEN";
export interface SettingsView { env_path: string; values: Record<SettingKey, SettingValue> }

// ---- test plans (stage 5a)
export type Priority = "P0" | "P1" | "P2" | "P3";
export type ScenarioType = "happy_path" | "branches_edges" | "errors_failures" | "regression" | "cross_side";
export interface PlanSummary {
  id: string; kind: "pr" | "scenario"; title: string; label: string; base: Record<string, string>; head: Record<string, string>;
  counts_by_status: Record<string, number>; counts_by_priority: Record<string, number>; cases_written: number;
  updated_at: string | null; has_impact: boolean;
}
export interface InventoryItem { id: string; kind: string; change: "added" | "changed" | "removed"; repo: string; file: string; lines: [number, number]; function: string; summary: string; outcomes: string[] }
export interface ImpactFlow {
  id: string; name: string; entry_points: string[]; changed_functions: string[]; inventory_ids: string[]; modules: string[];
  master_flow_id: string | null; risk: number; risk_reasons: string[]; est_claude_calls: number; crosses_repos: boolean; has_job: boolean;
}
export interface ImpactReport {
  change_set_id: string; head_commits: Record<string, string>; graph_delta: Record<string, unknown>;
  inventory: InventoryItem[]; flows: ImpactFlow[]; needs_full_reanalysis: boolean; full_reanalysis_reason: string;
}
export interface CaseSlot {
  id: string; flow_id: string; scenario_type: ScenarioType; inventory_ids: string[]; outcome: string; priority: Priority; rule: string;
  status: "planned" | "reserved" | "written" | "stale" | "failed" | "obsolete"; case_id: string | null; fail_reason: string | null;
  reserved_by?: string | null; reserved_until?: string | null; assigned_to?: string | null;
}
export interface PlanScenario { id: string; flow_id: string; type: ScenarioType; title: string; description: string }
export interface PlanCase {
  id: string; slot_id: string | null; suggested: boolean; status: string; scenario_id: string | null; flow_id: string; title: string;
  priority: Priority; preconditions: string[]; steps: string[]; test_data: string[]; expected: string[]; author: string; created_at: string; stale: boolean;
}
export interface BranchRefOut { branch: string; commit: string }
export interface ChangeSetOut {
  id: string; kind: "pr" | "scenario"; project: string; title: string; base: Record<string, BranchRefOut>; head: Record<string, BranchRefOut>;
  base_pack: string; prs: Record<string, { url: string; title: string }>; scenario_text: string | null; answer_id: string | null;
}
export interface Coverage { items: Record<string, "covered" | "skipped" | "failed" | "uncovered">; by_priority: Record<string, number>; by_status: Record<string, number> }
export interface TestPlanDetail {
  summary: PlanSummary | null; change_set: ChangeSetOut; impact: ImpactReport | null; slots: CaseSlot[];
  scenarios: PlanScenario[]; cases: PlanCase[]; extras: PlanCase[]; coverage: Coverage;
  shared?: boolean; pending?: number;
}
export interface TeamPeople {
  me: string | null; role: "viewer" | "contributor" | "maintainer" | null;
  members: { email: string; name: string; role: string }[];
  progress: { email: string; written: number; reserved: number }[];
  trace_locks: { flow_id: string; side: string; holder: string; until: string }[];
}
export interface SharedPlanRow { plan_id: string; label: string; title: string; kind: string; counts_by_status: Record<string, number>; updated_at: string; local: boolean }
export interface DesignResult { written: number; failed: number; extras: number }

export interface InspectedRepo {
  source: string; kind: "url" | "local"; name: string; role: string; is_git: boolean; default_branch: string | null;
  branches: string[]; path: string | null; path_options: { path: string; name: string; role: string }[]; context_docs: string[];
}
export interface LinkerSpec { type: string; from?: string | string[]; to?: string | string[]; in?: string | string[]; options?: Record<string, unknown> }
export interface Inspection { repos: InspectedRepo[]; linkers: LinkerSpec[] }
export interface NewProjectBody {
  project: string;
  repos: { name: string; role: string; source: string; path: string | null; default_branch: string | null }[];
  linkers: LinkerSpec[];
  context_docs: string[];
}

export interface Loc { repo: string; file: string; line: number }
export interface ConnNode { id: string; lane: string; kind: "module" | "jobs"; module_id: string | null; name: string; code_name: string; links: number; routes: { path: string; repo: string; file: string; line: number }[] }
export interface ConnEdge { id: string; source: string; target: string; kind: string; count: number; items: { label: string; from: Loc | null; to: Loc | null }[] }
export interface Connections { lanes: { id: string; label: string; role: string }[]; nodes: ConnNode[]; edges: ConnEdge[]; unresolved: Record<string, number> }
