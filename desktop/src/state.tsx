import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import type { Api } from "./api/client";
import type { ProjectRow, Status } from "./api/types";

export type View = "library" | "map" | "ask" | "memory";
export interface AskSeed { qaId?: string; question?: string; jobId?: string }
export interface UiState { project: string | null; branch: string | null; view: View; askSeed: AskSeed | null }

interface Ctx extends UiState {
  api: Api;
  status: Status | null;
  projects: ProjectRow[];
  current: ProjectRow | null;
  set(patch: Partial<UiState>): void;
  go(view: View, patch?: Partial<UiState>): void;
  refresh(fresh?: boolean): Promise<void>;
}

const AppCtx = createContext<Ctx | null>(null);

/** Apply a patch; an Ask seed belongs to one project@branch, so switching either drops it. */
function applyPatch(u: UiState, patch: Partial<UiState>): UiState {
  const moved = ("project" in patch && patch.project !== u.project) || ("branch" in patch && patch.branch !== u.branch);
  return { ...u, ...(moved && !("askSeed" in patch) ? { askSeed: null } : {}), ...patch };
}
const KEY = "kb.desktop.ui";

function loadSaved(): Partial<UiState> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}");
  } catch {
    return {};
  }
}

export function AppProvider({ api, initial, children }: { api: Api; initial?: Partial<UiState>; children: ReactNode }) {
  const [ui, setUi] = useState<UiState>({ project: null, branch: null, view: "library", askSeed: null, ...loadSaved(), ...initial });
  const [status, setStatus] = useState<Status | null>(null);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  const refresh = useCallback(async (fresh = false) => {
    const [s, p] = await Promise.all([api.status(fresh).catch(() => null), api.projects().catch(() => [])]);
    setStatus(s);
    setProjects(p ?? []);
    setUi((u) => (u.project || !p?.length ? u : { ...u, project: p[0].name }));
  }, [api]);
  useEffect(() => {
    void refresh();
  }, [refresh]);
  useEffect(() => {
    // Installing or signing in to Claude Code happens outside the app: re-check when the user comes back.
    let last = 0;
    const onFocus = () => {
      if (Date.now() - last < 10_000) return;
      last = Date.now();
      void refresh(true);
    };
    window.addEventListener("focus", onFocus);
    return () => window.removeEventListener("focus", onFocus);
  }, [refresh]);
  useEffect(() => {
    try {
      localStorage.setItem(KEY, JSON.stringify({ project: ui.project, branch: ui.branch, view: ui.view }));
    } catch {
      /* storage unavailable: fine */
    }
  }, [ui.project, ui.branch, ui.view]);
  const value = useMemo<Ctx>(() => ({
    ...ui, api, status, projects,
    current: projects.find((p) => p.name === ui.project) ?? null,
    set: (patch) => setUi((u) => applyPatch(u, patch)),
    go: (view, patch = {}) => setUi((u) => ({ ...applyPatch(u, patch), view })),
    refresh,
  }), [ui, api, status, projects, refresh]);
  return <AppCtx.Provider value={value}>{children}</AppCtx.Provider>;
}

export function useApp(): Ctx {
  const c = useContext(AppCtx);
  if (!c) throw new Error("useApp outside AppProvider");
  return c;
}
