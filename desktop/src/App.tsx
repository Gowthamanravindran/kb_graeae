import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import { EngineError } from "./components/EngineError";
import { ErrorBoundary } from "./components/ErrorBoundary";
import { Sidebar } from "./components/Sidebar";
import { AppProvider, useApp } from "./state";
import { Library } from "./views/Library";
import { MapView } from "./views/MapView";
import { Ask } from "./views/Ask";
import { Memory } from "./views/Memory";
import { Settings } from "./views/Settings";
import { Impact } from "./views/Impact";
import { TestPlans } from "./views/TestPlans";

function Main() {
  const { view, project, branch, go, planScreen, planId } = useApp();
  return (
    <div className="flex h-full">
      <ErrorBoundary key={`side-${project}`}><Sidebar /></ErrorBoundary>
      <main className="min-w-0 flex-1 overflow-y-auto px-10 py-8">
        <ErrorBoundary key={`${view}-${project}@${branch}`} onReset={() => go("library")}>
        {view === "library" && <Library />}
        {view === "map" && <MapView />}
        {view === "ask" && <Ask key={`${project}@${branch}`} />}
        {view === "memory" && <Memory key={`${project}@${branch}`} />}
        {view === "testplans" && (planScreen === "impact" && planId ? <Impact key={planId} /> : <TestPlans key={project ?? ""} />)}
        {view === "settings" && <Settings />}
        </ErrorBoundary>
      </main>
    </div>
  );
}

export default function App() {
  const api = useMemo(() => createClient({ baseUrl: window.kb?.baseUrl ?? "", token: window.kb?.token ?? "" }), []);
  const [engine, setEngine] = useState<{ ok: boolean; message?: string; log?: string; stopped?: boolean } | null>(null);
  useEffect(() => {
    if (location.hash === "#engine-error" && window.kb) void window.kb.engineStatus().then(setEngine);
    else setEngine({ ok: true });
    return window.kb?.onEngineStopped?.(() => void window.kb!.engineStatus().then(setEngine));
  }, []);
  if (!engine) return null;
  if (!engine.ok)
    return <EngineError message={engine.message ?? "unknown error"} log={engine.log ?? ""} stopped={engine.stopped} onRetry={() => void window.kb?.retryEngine()} onOpenLogs={() => void window.kb?.openLogs()} />;
  return <AppProvider api={api}><Main /></AppProvider>;
}
