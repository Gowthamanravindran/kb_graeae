import { useState } from "react";
import { ApiError } from "../api/client";
import { useApp } from "../state";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";

export function AddProjectDialog({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const { api, refresh, set } = useApp();
  const [error, setError] = useState<string | null>(null);
  const choose = async () => {
    setError(null);
    const path = await window.kb?.pickFile([{ name: "kb.yaml", extensions: ["yaml", "yml"] }]);
    if (!path) return;
    try {
      const p = await api.addProject(path);
      await refresh();
      set({ project: p.name, branch: null });
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Add a project</DialogTitle>
        <p className="mb-4 text-sm text-muted">Choose the project's kb.yaml. It lists the repos that make up the product and how they connect.</p>
        {error && <p role="alert" className="mb-4 rounded-lg bg-warn-soft p-3 text-sm text-warn">{error}</p>}
        <div className="flex justify-end gap-2">
          <Button variant="secondary" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button onClick={() => void choose()}>Choose kb.yaml…</Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
