import { useEffect, useMemo, useState } from "react";
import { createClient } from "./api/client";
import type { ProjectRow } from "./api/types";
import { EngineError } from "./components/EngineError";

export default function App() {
  const api = useMemo(() => createClient({ baseUrl: window.kb?.baseUrl ?? "", token: window.kb?.token ?? "" }), []);
  const [engine, setEngine] = useState<{ ok: boolean; message?: string; log?: string } | null>(null);
  const [projects, setProjects] = useState<ProjectRow[]>([]);
  useEffect(() => {
    if (location.hash === "#engine-error" && window.kb) void window.kb.engineStatus().then(setEngine);
    else {
      setEngine({ ok: true });
      void api.projects().then(setProjects).catch(() => setProjects([]));
    }
  }, [api]);
  if (engine && !engine.ok)
    return <EngineError message={engine.message ?? "unknown error"} log={engine.log ?? ""} onRetry={() => void window.kb?.retryEngine()} onOpenLogs={() => void window.kb?.openLogs()} />;
  return (
    <main className="p-10">
      <h1 className="text-2xl font-bold">Library</h1>
      <ul>{projects.map((p) => <li key={p.name}>{p.name}</li>)}</ul>
    </main>
  );
}
