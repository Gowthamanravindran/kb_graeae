import { Button } from "./ui/button";

export function EngineError({ message, log, stopped, onRetry, onOpenLogs }: { message: string; log: string; stopped?: boolean; onRetry(): void; onOpenLogs(): void }) {
  return (
    <main className="mx-auto flex max-w-3xl flex-col gap-4 p-10">
      <h1 className="text-2xl font-bold">{stopped ? "KB engine stopped" : "KB engine didn't start"}</h1>
      <p className="text-muted">{message}. The app needs its engine to read your analysed branches. Try again, or open the log to see what went wrong.</p>
      {log && <pre className="max-h-80 overflow-auto rounded-lg border border-line bg-surface p-4 font-mono text-xs">{log}</pre>}
      <div className="flex gap-2">
        <Button onClick={onRetry}>Retry</Button>
        <Button variant="secondary" onClick={onOpenLogs}>Open log folder</Button>
      </div>
    </main>
  );
}
