import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import { EngineError } from "./components/EngineError";
import { Sidebar } from "./components/Sidebar";
import { AppProvider, useApp } from "./state";
import { Library } from "./views/Library";
import { MapView } from "./views/MapView";
import { Ask } from "./views/Ask";

function Placeholder({ title }: { title: string }) {
  return <h1 className="text-[26px] font-bold">{title}</h1>;
}

function Main() {
  const { view, project, branch } = useApp();
  return (
    <div className="flex h-full">
      <Sidebar />
      <main className="min-w-0 flex-1 overflow-y-auto px-10 py-8">
        {view === "library" && <Library />}
        {view === "map" && <MapView />}
        {view === "ask" && <Ask key={`${project}@${branch}`} />}
        {view === "memory" && <Placeholder title="Memory" />}
      </main>
    </div>
  );
}

export default function App() {
  const api = useMemo(() => createClient({ baseUrl: window.kb?.baseUrl ?? "", token: window.kb?.token ?? "" }), []);
  const [engine, setEngine] = useState<{ ok: boolean; message?: string; log?: string } | null>(null);
  useEffect(() => {
    if (location.hash === "#engine-error" && window.kb) void window.kb.engineStatus().then(setEngine);
    else setEngine({ ok: true });
  }, []);
  if (!engine) return null;
  if (!engine.ok)
    return <EngineError message={engine.message ?? "unknown error"} log={engine.log ?? ""} onRetry={() => void window.kb?.retryEngine()} onOpenLogs={() => void window.kb?.openLogs()} />;
  return <AppProvider api={api}><Main /></AppProvider>;
}
