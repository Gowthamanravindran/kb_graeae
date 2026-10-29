import type { NewProjectBody, SettingKey, SettingsView, PlanSummary, TestPlanDetail, Priority, AskPayload, BranchesResponse, Job, MapView, MemoryView, ProjectRow, RegistryStatus, SavedQA, Status } from "./types";

export class ApiError extends Error {
  constructor(public status: number, public detail: string) {
    super(detail);
  }
}

const seg = (s: string) => s.split("/").map(encodeURIComponent).join("/");
type JobRef = { job_id: string };

export function createClient({ baseUrl, token }: { baseUrl: string; token: string }) {
  async function call<T>(method: string, path: string, body?: unknown): Promise<T> {
    const res = await fetch(`${baseUrl}${path}`, {
      method,
      headers: { "X-KB-Token": token, ...(body !== undefined ? { "Content-Type": "application/json" } : {}) },
      body: body !== undefined ? JSON.stringify(body) : undefined,
    });
    const text = await res.text();
    const data = text ? JSON.parse(text) : null;
    if (!res.ok) throw new ApiError(res.status, (data && typeof data.detail === "string" ? data.detail : res.statusText) || "error");
    return data as T;
  }
  const tp = (p: string, id: string) => `/api/projects/${encodeURIComponent(p)}/testplans/${encodeURIComponent(id)}`;
  const b = (p: string, br: string) => `/api/projects/${encodeURIComponent(p)}/branches/${seg(br)}`;
  return {
    status: (fresh = false) => call<Status>("GET", fresh ? "/api/status?fresh=1" : "/api/status"),
    projects: () => call<ProjectRow[]>("GET", "/api/projects"),
    addProject: (configPath: string) => call<ProjectRow>("POST", "/api/projects", { config_path: configPath }),
    inspectProject: (sources: string[]) => call<JobRef>("POST", "/api/projects/inspect", { sources }),
    createProject: (body: NewProjectBody) => call<ProjectRow>("POST", "/api/projects/create", body),
    registry: (p: string) => call<RegistryStatus>("GET", `/api/projects/${encodeURIComponent(p)}/registry`),
    login: (registry: string, token: string) => call<{ email: string }>("POST", "/api/login", { registry, token }),
    logout: (registry: string) => call<{ forgotten: boolean }>("POST", "/api/logout", { registry }),
    settings: () => call<SettingsView>("GET", "/api/settings"),
    prInfo: (url: string) => call<{ title: string; base_ref: string; head_ref: string }>("GET", `/api/github/pr?url=${encodeURIComponent(url)}`),
    testplans: (p: string, kind?: "pr" | "scenario") =>
      call<PlanSummary[]>("GET", `/api/projects/${encodeURIComponent(p)}/testplans${kind ? `?kind=${kind}` : ""}`),
    newPrPlan: (p: string, body: { base: Record<string, string>; head: Record<string, string | null>; pr_links: Record<string, string> }) =>
      call<JobRef>("POST", `/api/projects/${encodeURIComponent(p)}/testplans/pr`, body),
    newScenarioPlan: (p: string, body: { label: string; text?: string; answer_id?: string }) =>
      call<JobRef>("POST", `/api/projects/${encodeURIComponent(p)}/testplans/scenario`, body),
    testplan: (p: string, id: string) => call<TestPlanDetail>("GET", `${tp(p, id)}`),
    generate: (p: string, id: string, counts: Partial<Record<Priority, number>>, perFlowCap: number | null = null, flowIds?: string[]) =>
      call<JobRef>("POST", `${tp(p, id)}/generate`, flowIds ? { counts, per_flow_cap: perFlowCap, flow_ids: flowIds } : { counts, per_flow_cap: perFlowCap }),
    acceptExtra: (p: string, id: string, caseId: string) => call<{ ok: boolean }>("POST", `${tp(p, id)}/extras/${encodeURIComponent(caseId)}/accept`),
    dismissExtra: (p: string, id: string, caseId: string) => call<{ ok: boolean }>("POST", `${tp(p, id)}/extras/${encodeURIComponent(caseId)}/dismiss`),
    retrySlot: (p: string, id: string, slotId: string) => call<JobRef>("POST", `${tp(p, id)}/slots/${encodeURIComponent(slotId)}/retry`),
    exportCsv: async (p: string, id: string, filters: { priority?: string[]; flow?: string[]; status?: string[] } = {}) => {
      const q = new URLSearchParams();
      for (const [k, vs] of Object.entries(filters)) for (const v of vs ?? []) q.append(k, v);
      const res = await fetch(`${baseUrl}${tp(p, id)}/export.csv${q.toString() ? `?${q}` : ""}`, { headers: { "X-KB-Token": token } });
      if (!res.ok) throw new ApiError(res.status, await res.text());
      return new Uint8Array(await res.arrayBuffer());
    },
    saveSettings: (values: Partial<Record<SettingKey, string | null>>) => call<SettingsView>("PUT", "/api/settings", { values }),
    loginSso: (registry: string) => call<JobRef>("POST", "/api/login/sso", { registry }),
    branches: (p: string) => call<BranchesResponse>("GET", `/api/projects/${encodeURIComponent(p)}/branches`),
    analyze: (p: string, br: string, resume = false, branches?: Record<string, string>) =>
      call<JobRef>("POST", `${b(p, br)}/analyze`, branches ? { resume, branches } : { resume }),
    ask: (p: string, br: string, o: { question: string; fromQa?: string | null; noReuse?: boolean }) =>
      call<JobRef>("POST", `${b(p, br)}/ask`, { question: o.question, from_qa: o.fromQa ?? null, no_reuse: o.noReuse ?? false }),
    push: (p: string, br: string) => call<JobRef>("POST", `${b(p, br)}/push`),
    pull: (p: string, br: string, force = false) => call<JobRef>("POST", `${b(p, br)}/pull`, { force }),
    map: (p: string, br: string) => call<MapView>("GET", `${b(p, br)}/map`),
    memory: (p: string, br: string) => call<MemoryView>("GET", `${b(p, br)}/memory`),
    qa: (p: string, br: string, id: string) => call<SavedQA>("GET", `${b(p, br)}/qa/${encodeURIComponent(id)}`),
    publish: (p: string, br: string, ids: string[], notes = false) =>
      call<{ added?: string[]; existing?: string[]; queued?: number }>("POST", `${b(p, br)}/publish`, { qa_ids: ids, notes }),
    sync: (p: string, br: string) => call<{ sent: number; left: number; warnings: string[] }>("POST", `${b(p, br)}/sync`),
    retrace: (p: string, br: string, id: string) => call<JobRef>("POST", `${b(p, br)}/qa/${encodeURIComponent(id)}/retrace`),
    runSuggestion: (p: string, br: string, id: string) => call<JobRef>("POST", `${b(p, br)}/suggestions/${encodeURIComponent(id)}/run`),
    feedback: (traceId: string, value: 0 | 1) => call<{ recorded: boolean }>("POST", "/api/feedback", { trace_id: traceId, value }),
    citation: (p: string, br: string, repo: string, file: string, line: number) =>
      call<{ path: string; line: number }>("GET", `${b(p, br)}/citation?${new URLSearchParams({ repo, file, line: String(line) })}`),
    job: <T = unknown>(id: string) => call<Job<T>>("GET", `/api/jobs/${encodeURIComponent(id)}`),
    jobs: (o: { kind?: string; active?: boolean } = {}) =>
      call<Job[]>("GET", `/api/jobs?${new URLSearchParams({ ...(o.kind ? { kind: o.kind } : {}), active: o.active ? "1" : "0" })}`),
  };
}

export type Api = ReturnType<typeof createClient>;
export type { AskPayload };
