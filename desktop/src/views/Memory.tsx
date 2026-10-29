import { useCallback, useEffect, useState } from "react";
import { ApiError } from "../api/client";
import type { MemoryItem, MemoryView } from "../api/types";
import { useJob } from "../api/useJob";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Checkbox } from "../components/ui/checkbox";
import { LoginDialog } from "../components/LoginDialog";
import { cn } from "../lib";
import { useApp } from "../state";

type Tab = "all" | "private" | "published" | "stale" | "suggested";
const TABS: [Tab, string][] = [["all", "All"], ["private", "Mine, private"], ["published", "Published"], ["stale", "Stale"], ["suggested", "Suggested"]];
const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

function ago(iso: string) {
  const m = (Date.now() - new Date(iso).getTime()) / 60000;
  if (!isFinite(m) || m < 1) return "just now";
  if (m < 60) return `${Math.floor(m)} min ago`;
  if (m < 1440) return `${Math.floor(m / 60)} h ago`;
  return `${Math.floor(m / 1440)} days ago`;
}

function badge(i: MemoryItem) {
  if (i.stale) return <Badge tone="warn">Stale</Badge>;
  if (i.state === "published") return <Badge tone="ok">Published</Badge>;
  if (i.state === "queued") return <Badge tone="accent">Queued</Badge>;
  return <Badge tone="priv">Private</Badge>;
}

function visible(items: MemoryItem[], tab: Tab) {
  if (tab === "private") return items.filter((i) => i.state === "private" || i.state === "queued");
  if (tab === "published") return items.filter((i) => i.state === "published");
  if (tab === "stale") return items.filter((i) => i.stale);
  return items;
}

/** Runs suggestions one after another in the background (each waits for the previous job). */
function RunAll({ ids, onFinished }: { ids: string[]; onFinished(): void }) {
  const { api, project, branch } = useApp();
  const [index, setIndex] = useState(0);
  const [jobId, setJobId] = useState<string | null>(null);
  const { done, failed } = useJob(api, jobId);
  useEffect(() => {
    if (index >= ids.length) { onFinished(); return; }
    api.runSuggestion(project!, branch!, ids[index]).then((r) => setJobId(r.job_id), () => setIndex((i) => i + 1));
  }, [index]); // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (done || failed) { setJobId(null); setIndex((i) => i + 1); }
  }, [done, failed]);
  return <p className="text-sm text-accent">Running {Math.min(index + 1, ids.length)} of {ids.length}…</p>;
}

export function Memory() {
  const { api, project, branch, go } = useApp();
  const [view, setView] = useState<MemoryView | null>(null);
  const [tab, setTab] = useState<Tab>("all");
  const [picked, setPicked] = useState<Set<string>>(new Set());
  const [note, setNote] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [runAll, setRunAll] = useState(false);
  const [login, setLogin] = useState(false);
  const load = useCallback(async () => {
    if (!project || !branch) return;
    try { setView(await api.memory(project, branch)); } catch (e) { setError(msg(e)); }
  }, [api, project, branch]);
  useEffect(() => { void load(); }, [load]);
  if (!project || !branch) return <p className="text-muted">Open a branch from the Library to see its memory.</p>;

  const act = async (fn: () => Promise<void>) => {
    setError(null);
    try { await fn(); } catch (e) {
      if (e instanceof ApiError && e.status === 401) setLogin(true);
      setError(msg(e));
    }
  };
  const toAsk = (question: string, jobId: string) => go("ask", { askSeed: { question, jobId } });
  const share = () => act(async () => {
    await api.publish(project, branch, [...picked], false);
    setPicked(new Set());
    setNote(`Shared ${picked.size} to ${project}@${branch}.`);
    await load();
  });
  const sync = () => act(async () => {
    const r = await api.sync(project, branch);
    setNote(r.left ? `Sent ${r.sent}; ${r.left} still waiting for the registry.` : `Sent ${r.sent} item${r.sent === 1 ? "" : "s"} to the registry.`);
    await load();
  });
  const items = view ? visible(view.items, tab) : [];
  const sugg = view?.suggestions ?? [];

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-wrap items-end justify-between gap-4">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight">Memory · {project}@{branch}</h1>
          <p className="mt-1.5 text-muted">Everything asked on this branch. Private answers stay on this computer until you share them.</p>
        </div>
        <Button disabled={picked.size === 0} onClick={() => void share()}>Share {picked.size} selected</Button>
      </div>
      {error && <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>}
      {login && <LoginDialog open={login} onOpenChange={setLogin} />}
      {note && <div role="status" className="rounded-lg bg-ok-soft px-4 py-3 text-sm text-ok">{note}</div>}
      {view && (
        <div className="flex flex-col gap-5 lg:flex-row">
          <div className="flex min-w-0 flex-1 flex-col gap-4">
            <div role="tablist" aria-label="Filter" className="flex flex-wrap gap-1 border-b border-line">
              {TABS.map(([id, label]) => (
                <button key={id} type="button" role="tab" aria-selected={tab === id} onClick={() => setTab(id)}
                  className={cn("-mb-px border-b-2 px-3 py-2 text-sm", tab === id ? "border-accent font-semibold text-ink" : "border-transparent text-muted")}>
                  {label} · {view.counts[id]}
                </button>
              ))}
            </div>
            {tab === "suggested" ? (
              <p className="text-sm text-muted">Suggested questions are listed on the right.</p>
            ) : (
              <Card>
                <ul role="tabpanel">
                  {items.map((i) => (
                    <li key={i.qa_id} className="flex items-start gap-3 border-b border-[#EFEFEC] px-5 py-4 last:border-0">
                      <div className="w-5 pt-0.5">
                        {i.state === "private" && (
                          <Checkbox aria-label={`Select ${i.question}`} checked={picked.has(i.qa_id)}
                            onCheckedChange={(v) => setPicked((s) => { const n = new Set(s); if (v === true) n.add(i.qa_id); else n.delete(i.qa_id); return n; })} />
                        )}
                      </div>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{i.question}</span>{badge(i)}</div>
                        <div className="mt-1 text-xs text-muted">
                          {[i.modules.join(", ") || "—", i.has_flow ? "flow with diagram" : "answer", `${i.citations} citations`, `${i.author}, ${ago(i.created_at)}`].join(" · ")}
                        </div>
                      </div>
                      <div className="flex shrink-0 gap-2">
                        {i.state === "queued" && <Button variant="secondary" size="sm" onClick={() => void sync()}>Sync now</Button>}
                        {i.stale && (
                          <Button variant="secondary" size="sm" onClick={() => void act(async () => toAsk(i.question, (await api.retrace(project, branch, i.qa_id)).job_id))}>Re-trace</Button>
                        )}
                        <Button variant="ghost" size="sm" onClick={() => go("ask", { askSeed: { qaId: i.qa_id } })}>Ask this</Button>
                        {i.has_flow && (
                          <Button variant="ghost" size="sm" onClick={() => void act(async () => {
                            const { job_id } = await api.newScenarioPlan(project, { label: branch, answer_id: i.qa_id });
                            go("testplans", { planScreen: "list", planJobId: job_id });
                          })}>Make test plan</Button>
                        )}
                      </div>
                    </li>
                  ))}
                  {items.length === 0 && <li className="px-5 py-10 text-center text-muted">Nothing here yet.</li>}
                </ul>
              </Card>
            )}
          </div>
          <aside aria-label="Suggested next" className="w-full shrink-0 lg:w-[320px]">
            <Card className="flex flex-col gap-3 p-5">
              <h2 className="font-semibold">Suggested next</h2>
              <p className="text-sm text-muted">Queued from earlier answers. Nothing runs until you choose, so your Claude usage stays under your control.</p>
              <ul className="flex flex-col gap-2">
                {sugg.map((s) => (
                  <li key={s.id} className="flex items-start justify-between gap-2 rounded-lg border border-line p-3">
                    <span className="text-sm">{s.question}</span>
                    <Button variant="secondary" size="sm" disabled={runAll}
                      onClick={() => void act(async () => toAsk(s.question, (await api.runSuggestion(project, branch, s.id)).job_id))}>Run</Button>
                  </li>
                ))}
              </ul>
              {sugg.length > 1 && (runAll
                ? <RunAll ids={sugg.map((s) => s.id)} onFinished={() => { setRunAll(false); setNote("All suggestions answered."); void load(); }} />
                : <Button variant="secondary" onClick={() => setRunAll(true)}>Run all {sugg.length}</Button>)}
              {sugg.length === 0 && <p className="text-sm text-muted">No suggestions yet. They appear after you ask questions.</p>}
            </Card>
          </aside>
        </div>
      )}
    </div>
  );
}
