import { useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ApiError } from "../api/client";
import type { AskPayload, Citation } from "../api/types";
import { useJob } from "../api/useJob";
import { FlowSteps } from "../components/FlowSteps";
import { Mermaid } from "../components/Mermaid";
import { Badge } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { Card } from "../components/ui/card";
import { Input } from "../components/ui/input";
import { useApp } from "../state";

interface Turn { key: string; question: string; jobId?: string; payload?: AskPayload; shared?: boolean; error?: string }

let seq = 0;
const nextKey = () => `t${++seq}`;
const msg = (e: unknown) => (e instanceof ApiError ? e.detail : String(e));

function Running({ jobId, onDone, onFail }: { jobId: string; onDone(p: AskPayload): void; onFail(e: string): void }) {
  const { api } = useApp();
  const { job, done, failed } = useJob<AskPayload>(api, jobId);
  useEffect(() => {
    if (done && job?.result) onDone(job.result);
    if (failed) onFail(job?.error ?? "The question failed");
  }, [done, failed]); // eslint-disable-line react-hooks/exhaustive-deps
  return (
    <Card className="flex flex-col gap-3 p-5" aria-busy="true">
      <div className="text-sm font-semibold text-accent">{job?.step || "Starting…"}</div>
      <div className="h-3 w-3/4 animate-pulse rounded bg-bg" />
      <div className="h-3 w-2/3 animate-pulse rounded bg-bg" />
      <div className="h-24 animate-pulse rounded bg-bg" />
    </Card>
  );
}

function Answer({ turn, onShare, onRetrace, onAsk, onOpen, onFeedback }: {
  turn: Turn & { payload: AskPayload }; onShare(): void; onRetrace(): void; onAsk(q: string): void; onOpen(c: Citation): void; onFeedback(v: 0 | 1): void;
}) {
  const { project, branch } = useApp();
  const p = turn.payload;
  const verified = p.steps.filter((s) => s.verified).length;
  const [voted, setVoted] = useState<0 | 1 | null>(null);
  return (
    <Card className="flex flex-col gap-5 p-6">
      <div className="flex flex-wrap items-center gap-2">
        {p.steps.length > 0 && <Badge tone={verified === p.steps.length ? "ok" : "warn"}>{verified} of {p.steps.length} steps verified</Badge>}
        {p.reused && <Badge tone="accent">Reused a saved answer — no Claude call</Badge>}
        {p.not_found && <Badge tone="warn">Not found in this code</Badge>}
      </div>
      {p.warnings.map((w) => <p key={w} className="text-sm text-warn">{w}</p>)}
      <div className="flex flex-col gap-3 text-[15px] leading-relaxed"><ReactMarkdown>{p.answer}</ReactMarkdown></div>
      {p.mermaid && <Mermaid code={p.mermaid} />}
      {p.steps.length > 0 && (
        <div className="flex flex-col gap-5 md:flex-row">
          <FlowSteps title="UI flow" steps={p.steps.filter((s) => s.side === "ui")} onOpen={onOpen} />
          <FlowSteps title="Backend flow" steps={p.steps.filter((s) => s.side === "backend")} onOpen={onOpen} />
        </div>
      )}
      {p.saved_qa_id && (
        <div className="flex flex-wrap items-center gap-2 border-t border-line pt-4">
          <span className="mr-auto text-sm text-muted">{turn.shared ? "Shared" : "Saved privately to your memory"}</span>
          {p.how?.trace_id && (
            <>
              <Button variant="ghost" size="sm" aria-label="Helpful" aria-pressed={voted === 1} onClick={() => { setVoted(1); onFeedback(1); }}>👍</Button>
              <Button variant="ghost" size="sm" aria-label="Not helpful" aria-pressed={voted === 0} onClick={() => { setVoted(0); onFeedback(0); }}>👎</Button>
            </>
          )}
          <Button variant="secondary" size="sm" onClick={onRetrace}>Re-trace</Button>
          {!turn.shared && <Button size="sm" onClick={onShare}>Share to {project}@{branch}</Button>}
        </div>
      )}
      {p.suggestions.length > 0 && (
        <div className="flex flex-col gap-2">
          <div className="text-xs font-semibold uppercase tracking-wide text-muted">Related scenarios · queued, not run</div>
          <div className="flex flex-wrap gap-2">
            {p.suggestions.map((s) => (
              <button key={s} type="button" onClick={() => onAsk(s)} className="rounded-full border border-line bg-bg px-3 py-1.5 text-sm hover:border-accent">{s}</button>
            ))}
          </div>
        </div>
      )}
    </Card>
  );
}

function How({ how }: { how: AskPayload["how"] }) {
  const { status } = useApp();
  const host = status?.langfuse?.host;
  const rows: [string, string][] = [
    ["Matched modules", how.modules.join(", ") || "—"],
    ["Shortlist", `${how.shortlist_size} code pieces`],
    ["Files read", `${how.files_read} files · ${how.tool_steps} tool steps`],
    ["Claude calls", String(how.claude_calls)],
    ["Time", `${how.seconds} s`],
  ];
  return (
    <aside aria-label="How this answer was made" className="w-full shrink-0 lg:w-[280px]">
      <Card className="flex flex-col gap-3 p-5">
        <h2 className="font-semibold">How this answer was made</h2>
        <dl className="flex flex-col gap-2.5 text-sm">
          {rows.map(([k, v]) => (
            <div key={k}><dt className="text-xs text-muted">{k}</dt><dd>{v}</dd></div>
          ))}
        </dl>
        {how.trace_id && host && (
          <Button variant="secondary" size="sm" onClick={() => void window.kb?.openExternal(`${host}/trace/${encodeURIComponent(how.trace_id!)}`)}>Open trace in Langfuse</Button>
        )}
      </Card>
    </aside>
  );
}

export function Ask() {
  const { api, project, branch, status, askSeed, set } = useApp();
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState("");
  const [error, setError] = useState<string | null>(null);
  const started = useRef(false);
  useEffect(() => {
    if (started.current || !project || !branch) return;
    started.current = true;
    const s = askSeed;
    if (s?.jobId) setTurns([{ key: nextKey(), question: s.question ?? "", jobId: s.jobId }]);
    else if (s?.qaId) {
      api.qa(project, branch, s.qaId).then(
        (qa) => setTurns([{
          key: nextKey(), question: qa.question, shared: qa.visibility === "published",
          payload: { answer: qa.answer, reused: false, reused_flow_id: null, saved_qa_id: qa.id, suggestions: [], warnings: qa.stale ? ["The code changed since this answer was traced. Re-trace to refresh it."] : [], mermaid: qa.flow?.mermaid ?? "", not_found: false, steps: qa.flow?.steps ?? [], how: null as unknown as AskPayload["how"] },
        }]),
        (e) => setError(msg(e)),
      );
    } else if (s?.question) setInput(s.question);
  }, [api, project, branch]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!project || !branch) return <p className="text-muted">Open a branch from the Library to ask about it.</p>;
  const claudeOff = status?.claude.available === false;
  const last = turns[turns.length - 1];
  const running = turns.some((t) => t.jobId && !t.payload && !t.error);
  const followUpOf = last?.payload?.saved_qa_id ?? null;

  const track = (question: string, jobId: string) => {
    setTurns((ts) => [...ts, { key: nextKey(), question, jobId }]);
    set({ askSeed: { question, jobId } });
  };
  const ask = async (question: string, fromQa: string | null) => {
    if (!question.trim()) return;
    setError(null);
    try {
      const { job_id } = await api.ask(project, branch, { question: question.trim(), fromQa });
      setInput("");
      track(question.trim(), job_id);
    } catch (e) {
      setError(msg(e));
    }
  };
  const update = (key: string, patch: Partial<Turn>) => setTurns((ts) => ts.map((t) => (t.key === key ? { ...t, ...patch } : t)));
  const open = async (c: Citation) => {
    const r = await window.kb?.openCitation({ project, branch, repo: c.repo, file: c.file, line: c.line_start });
    if (r && !r.ok) setError(r.error ?? "Could not open that file.");
  };
  const label = followUpOf ? "Ask a follow-up about this answer…" : "Question";

  return (
    <div className="flex flex-col gap-5 lg:flex-row">
      <div className="flex min-w-0 flex-1 flex-col gap-5">
        <div>
          <h1 className="text-[26px] font-bold tracking-tight">Ask</h1>
          <p className="mt-1.5 text-muted">{project}@{branch} · answers are traced through the code and saved to your memory.</p>
        </div>
        {claudeOff && (
          <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">
            Claude Code isn't available on this computer ({status?.claude.reason}). You can still read saved answers; to ask new questions, install Claude Code and run <code className="font-mono">claude</code> once to sign in.
          </div>
        )}
        {error && <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{error}</div>}
        {turns.map((t) => (
          <div key={t.key} className="flex flex-col gap-3">
            <div className="self-end rounded-2xl bg-ink px-4 py-2.5 text-sm text-white">{t.question}</div>
            {t.error ? (
              <div role="alert" className="rounded-lg bg-warn-soft px-4 py-3 text-sm text-warn">{t.error}</div>
            ) : t.payload ? (
              <Answer
                turn={t as Turn & { payload: AskPayload }}
                onOpen={(c) => void open(c)}
                onAsk={(q) => void ask(q, null)}
                onFeedback={(v) => t.payload?.how?.trace_id && void api.feedback(t.payload.how.trace_id, v).catch(() => {})}
                onShare={async () => {
                  try {
                    await api.publish(project, branch, [t.payload!.saved_qa_id!], false);
                    update(t.key, { shared: true });
                  } catch (e) {
                    setError(msg(e));
                  }
                }}
                onRetrace={async () => {
                  try {
                    const { job_id } = await api.retrace(project, branch, t.payload!.saved_qa_id!);
                    track(t.question, job_id);
                  } catch (e) {
                    setError(msg(e));
                  }
                }}
              />
            ) : t.jobId ? (
              <Running
                jobId={t.jobId}
                onDone={(p) => { update(t.key, { payload: p }); set({ askSeed: p.saved_qa_id ? { qaId: p.saved_qa_id } : null }); }}
                onFail={(e) => { update(t.key, { error: e }); set({ askSeed: null }); }}
              />
            ) : null}
          </div>
        ))}
        <form className="flex gap-2" onSubmit={(e) => { e.preventDefault(); void ask(input, followUpOf); }}>
          <Input aria-label={label} placeholder={followUpOf ? label : "How does scheduling work in Tasks?"} value={input}
            onChange={(e) => setInput(e.target.value)} disabled={claudeOff || running} className="flex-1" />
          <Button type="submit" disabled={claudeOff || running || !input.trim()}>Ask</Button>
        </form>
      </div>
      {last?.payload?.how && <How how={last.payload.how} />}
    </div>
  );
}
