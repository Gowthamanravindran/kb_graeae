// Mirrors kb serve payloads (src/kb/serve).
export interface Status { version: string; claude: { available: boolean; reason: string | null }; langfuse: { configured: boolean; host?: string | null } }
export interface ProjectRow { name: string; config_path: string; registry: string | null; repos?: { name: string; role: string }[]; linkers?: string[]; error?: string }
export interface RegistryStatus { url: string | null; logged_in: boolean; email: string | null; reachable: boolean; sso: boolean }
export interface LocalBranch { version: number; status: string; commits: Record<string, string>; author: string | null; created_at: string | null; modules: number; qa_total: number; qa_published: number; synced: boolean; stats: Record<string, number> }
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
export interface Job<T = unknown> { id: string; kind: string; key: string | null; state: "queued" | "running" | "done" | "failed"; step: string; progress: number; result: T | null; error: string | null; created_at: string }
