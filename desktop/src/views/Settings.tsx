import { useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { RegistryStatus, SettingKey, SettingsView } from "../api/types";
import { LoginDialog } from "../components/LoginDialog";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { useApp } from "../state";

const FIELDS: { key: SettingKey; label: string; secret: boolean; help?: string }[] = [
  { key: "LANGFUSE_PUBLIC_KEY", label: "Public key", secret: true },
  { key: "LANGFUSE_SECRET_KEY", label: "Secret key", secret: true },
  { key: "LANGFUSE_HOST", label: "Host", secret: false, help: "For example http://localhost:3000" },
  { key: "GITHUB_TOKEN", label: "GitHub token", secret: true, help: "Used to read pull request titles and descriptions." },
];
const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

function Field({ f, view, value, onChange }: { f: (typeof FIELDS)[number]; view: SettingsView; value: string | undefined; onChange(v: string): void }) {
  const cur = view.values[f.key];
  const fromEnv = cur.source === "environment";
  const placeholder = f.secret ? (cur.set ? `••••${cur.last4}` : "Not set") : "Not set";
  const id = `set-${f.key}`;
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={id} className="text-sm font-semibold">{f.label}</label>
      <Input id={id} type={f.secret ? "password" : "text"} autoComplete="off" disabled={fromEnv} placeholder={placeholder}
        value={value ?? (f.secret ? "" : cur.value ?? "")} onChange={(e) => onChange(e.target.value)} />
      {fromEnv ? <p className="text-xs text-warn">Set in your environment; that value wins over this file.</p>
        : f.help ? <p className="text-xs text-muted">{f.help}</p> : null}
    </div>
  );
}

function Registries() {
  const { api, projects, set } = useApp();
  const [rows, setRows] = useState<Record<string, RegistryStatus | null>>({});
  const [login, setLogin] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const load = () => {
    for (const p of projects) {
      if (!p.registry) continue;
      api.registry(p.name).then((r) => setRows((x) => ({ ...x, [p.name]: r })), () => setRows((x) => ({ ...x, [p.name]: null })));
    }
  };
  useEffect(load, [api, projects]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <section aria-label="Registries">
      <Card className="flex flex-col gap-4 p-6">
        <div>
          <h2 className="text-lg font-semibold">Registries</h2>
          <p className="mt-1 text-sm text-muted">Each project names its registry in its kb.yaml.</p>
        </div>
        {error && <p role="alert" className="text-sm text-warn">{error}</p>}
        <ul className="flex flex-col divide-y divide-line">
          {projects.map((p) => {
            const r = rows[p.name];
            return (
              <li key={p.name} className="flex flex-wrap items-center justify-between gap-3 py-3">
                <div className="min-w-0">
                  <div className="font-semibold">{p.name}</div>
                  <div className="break-all font-mono text-xs text-muted">{p.registry ?? "No registry in kb.yaml"}</div>
                  {r?.logged_in && <div className="text-xs text-ok">Signed in as {r.email}</div>}
                </div>
                <div className="flex gap-2">
                  {p.registry && r && !r.logged_in && (
                    <Button size="sm" onClick={() => { set({ project: p.name }); setLogin(true); }}>Sign in</Button>
                  )}
                  {p.registry && r?.logged_in && (
                    <Button size="sm" variant="secondary" onClick={async () => {
                      try { await api.logout(p.registry!); load(); } catch (e) { setError(msg(e)); }
                    }}>Sign out</Button>
                  )}
                  <Button size="sm" variant="ghost" onClick={async () => {
                    const res = await window.kb?.revealConfig(p.name);
                    if (res && !res.ok) setError(res.error ?? "Could not show kb.yaml.");
                  }}>Show kb.yaml</Button>
                </div>
              </li>
            );
          })}
          {projects.length === 0 && <li className="py-3 text-sm text-muted">No projects yet.</li>}
        </ul>
      </Card>
      {login && <LoginDialog open={login} onOpenChange={(o) => { setLogin(o); if (!o) load(); }} />}
    </section>
  );
}

export function Settings() {
  const { api, refresh, status } = useApp();
  const [view, setView] = useState<SettingsView | null>(null);
  const [edits, setEdits] = useState<Partial<Record<SettingKey, string>>>({});
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api.settings().then(setView, (e) => setError(msg(e))); }, [api]);
  const save = async () => {
    setError(null);
    setNote(null);
    try {
      const changed = Object.fromEntries(Object.entries(edits).filter(([, v]) => v !== undefined));
      setView(await api.saveSettings(changed));
      setEdits({});
      setNote("Saved. New questions use these settings.");
      void refresh(true);
    } catch (e) {
      setError(msg(e));
    }
  };
  return (
    <div className="flex max-w-3xl flex-col gap-5">
      <div>
        <h1 className="text-[26px] font-bold tracking-tight">Settings</h1>
        <p className="mt-1.5 text-muted">Stored on this computer in <span className="font-mono text-sm">{view?.env_path ?? "~/.kb/kb.env"}</span>.</p>
      </div>
      {error && <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>}
      {note && <div role="status" className="rounded-lg bg-ok-soft px-4 py-3 text-sm text-ok">{note}</div>}
      {view && (
        <form className="flex flex-col gap-5" onSubmit={(e) => { e.preventDefault(); void save(); }}>
          <Card className="flex flex-col gap-4 p-6">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <h2 className="text-lg font-semibold">Langfuse</h2>
              <span className="text-xs text-muted">{status?.langfuse.configured ? `Configured · ${status.langfuse.host ?? ""}` : "Not configured"}</span>
            </div>
            {FIELDS.slice(0, 3).map((f) => (
              <Field key={f.key} f={f} view={view} value={edits[f.key]} onChange={(v) => setEdits((x) => ({ ...x, [f.key]: v }))} />
            ))}
            <div><Button type="button" variant="secondary" size="sm" onClick={() => void refresh(true)}>Test connection</Button></div>
          </Card>
          <Card className="flex flex-col gap-4 p-6">
            <h2 className="text-lg font-semibold">GitHub</h2>
            <Field f={FIELDS[3]} view={view} value={edits.GITHUB_TOKEN} onChange={(v) => setEdits((x) => ({ ...x, GITHUB_TOKEN: v }))} />
          </Card>
          <div><Button type="submit" disabled={Object.keys(edits).length === 0}>Save</Button></div>
        </form>
      )}
      <Registries />
    </div>
  );
}
