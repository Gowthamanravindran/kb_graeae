import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { RegistryStatus } from "../api/types";
import { useJob } from "../api/useJob";
import { useApp } from "../state";
import { Button } from "./ui/button";
import { Dialog, DialogContent, DialogTitle } from "./ui/dialog";
import { Input } from "./ui/input";

export function LoginDialog({ open, onOpenChange }: { open: boolean; onOpenChange(open: boolean): void }) {
  const { api, current, refresh } = useApp();
  const [reg, setReg] = useState<RegistryStatus | null>(null);
  const [mode, setMode] = useState<"sso" | "token">("sso");
  const [token, setToken] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const { job, done, failed } = useJob<{ email: string }>(api, jobId);
  useEffect(() => {
    if (open && current?.name) void api.registry(current.name).then((r) => { setReg(r); setMode(r.sso ? "sso" : "token"); }).catch(() => setMode("token"));
  }, [api, open, current?.name]);
  useEffect(() => {
    if (done) { void refresh(); onOpenChange(false); }
    if (failed) setError(job?.error ?? "sign-in failed");
  }, [done, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  const url = reg?.url ?? current?.registry ?? "";
  const link = job?.step.match(/https?:\/\/\S+/)?.[0];
  const signInToken = async () => {
    setError(null);
    try {
      await api.login(url, token);
      await refresh();
      onOpenChange(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.detail : String(e));
    }
  };
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogTitle>Sign in to the registry</DialogTitle>
        <p className="mb-4 font-mono text-xs text-muted">{url || "No registry set in this project's kb.yaml"}</p>
        {error && <p role="alert" className="mb-4 rounded-lg bg-warn-soft p-3 text-sm text-warn">{error}</p>}
        {mode === "sso" ? (
          <div className="flex flex-col gap-3">
            {job?.step && <p className="rounded-lg bg-bg p-3 text-sm">{job.step}</p>}
            {link && <Button variant="secondary" onClick={() => void window.kb?.openExternal(link)}>Open sign-in page</Button>}
            {!jobId && <Button onClick={() => void api.loginSso(url).then((r) => setJobId(r.job_id)).catch((e) => setError(e instanceof ApiError ? e.detail : String(e)))}>Sign in with single sign-on</Button>}
            <Button variant="ghost" onClick={() => setMode("token")}>Use an API token instead</Button>
          </div>
        ) : (
          <form className="flex flex-col gap-3" onSubmit={(e) => { e.preventDefault(); void signInToken(); }}>
            <label className="text-sm font-semibold" htmlFor="kb-token">API token from your registry admin</label>
            <Input id="kb-token" type="password" value={token} onChange={(e) => setToken(e.target.value)} placeholder="kbr_…" />
            <div className="flex justify-end gap-2">
              {reg?.sso && <Button variant="ghost" onClick={() => setMode("sso")}>Use single sign-on</Button>}
              <Button type="submit" disabled={!token || !url}>Sign in</Button>
            </div>
          </form>
        )}
      </DialogContent>
    </Dialog>
  );
}
