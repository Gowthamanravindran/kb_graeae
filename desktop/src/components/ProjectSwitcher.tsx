import { useState } from "react";
import { useApp } from "../state";
import { AddProjectDialog } from "./AddProjectDialog";

export function ProjectSwitcher() {
  const { projects, project, branch, set } = useApp();
  const [open, setOpen] = useState(false);
  const [adding, setAdding] = useState(false);
  return (
    <div className="relative">
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open}
        className="flex w-full flex-col items-start gap-0.5 rounded-lg border border-line bg-bg px-3 py-2.5 text-left">
        <span className="text-[11px] uppercase tracking-wider text-muted">Project</span>
        <span className="font-semibold">{project ?? "No project yet"}</span>
        {branch && <span className="font-mono text-xs text-muted">{branch}</span>}
      </button>
      {open && (
        <div role="menu" className="absolute left-0 right-0 z-20 mt-1 rounded-lg border border-line bg-surface p-1 shadow-lg">
          {projects.map((p) => (
            <button key={p.name} role="menuitem" type="button" onClick={() => { set({ project: p.name, branch: null }); setOpen(false); }}
              className="block w-full rounded-md px-3 py-2 text-left hover:bg-bg">{p.name}{p.error ? " (config error)" : ""}</button>
          ))}
          <button role="menuitem" type="button" onClick={() => { setAdding(true); setOpen(false); }} className="block w-full rounded-md px-3 py-2 text-left text-accent-ink hover:bg-bg">
            Add a project…
          </button>
        </div>
      )}
      <AddProjectDialog open={adding} onOpenChange={setAdding} />
    </div>
  );
}
