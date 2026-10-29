import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import type { Api } from "./api/client";
import { AppProvider, type UiState } from "./state";

/** Fake API: every method resolves to `undefined` unless overridden. */
export function fakeApi(over: Partial<Record<keyof Api, unknown>> = {}): Api {
  const defaults: Partial<Record<keyof Api, unknown>> = {
    status: vi.fn(async () => ({ version: "0.1.0", claude: { available: true, reason: null }, langfuse: { configured: false } })),
    projects: vi.fn(async () => [{ name: "sop", config_path: "/x/sop.kb.yaml", registry: "http://reg", repos: [{ name: "sop-web", role: "frontend" }, { name: "sop-backend", role: "backend" }], linkers: ["graphql"] }]),
    registry: vi.fn(async () => ({ url: "http://reg", logged_in: true, email: "me@corp", reachable: true, sso: false })),
    job: vi.fn(async (id: string) => ({ id, kind: "x", key: null, state: "running", step: "", progress: 0, result: null, error: null, created_at: "" })),
    jobs: vi.fn(async () => []),
  };
  return new Proxy({ ...defaults, ...over } as Record<string, unknown>, {
    get: (t, k: string) => (k in t ? t[k] : vi.fn(async () => undefined)),
  }) as unknown as Api;
}

export function renderApp(ui: ReactElement, api: Api, initial: Partial<UiState> = { project: "sop", branch: "main", view: "library" }) {
  return render(<AppProvider api={api} initial={initial}>{ui}</AppProvider>);
}
