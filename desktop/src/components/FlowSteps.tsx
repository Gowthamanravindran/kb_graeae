import type { Citation, Step } from "../api/types";
import { cn } from "../lib";

export function citeLabel(c: Citation) {
  return `${c.repo}:${c.file}:${c.line_start}`;
}

/** Numbered flow steps for one side, each with a clickable source citation. */
export function FlowSteps({ title, steps, onOpen }: { title: string; steps: Step[]; onOpen(c: Citation): void }) {
  return (
    <section aria-label={title} className="flex min-w-0 flex-1 flex-col gap-3">
      <h3 className="text-xs font-semibold uppercase tracking-wide text-muted">{title}</h3>
      {steps.length === 0 ? (
        <p className="text-sm text-muted">No steps on this side.</p>
      ) : (
        <ol className="flex flex-col gap-2.5">
          {steps.map((s, i) => (
            <li key={i} className={cn("flex gap-3 rounded-lg border p-3", s.verified ? "border-line bg-surface" : "border-warn/40 bg-warn-soft")}>
              <span className={cn("flex h-6 w-6 shrink-0 items-center justify-center rounded-full text-xs font-semibold", s.verified ? "bg-accent-soft text-accent-ink" : "bg-warn text-white")}>{i + 1}</span>
              <div className="min-w-0">
                <div className="text-sm">{s.text}</div>
                {s.citation ? (
                  <button type="button" onClick={() => onOpen(s.citation!)} className="mt-1 break-all text-left font-mono text-xs text-accent hover:underline">
                    {citeLabel(s.citation)}
                  </button>
                ) : null}
                {!s.verified && <div className="mt-1 text-xs font-semibold text-warn">Unverified · no matching code found</div>}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}
