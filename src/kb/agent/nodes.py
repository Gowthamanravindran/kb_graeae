"""Agent nodes. Each takes (state, deps) and returns a partial state update.

understand: classify, find modules, reuse a fresh saved flow if it answers the same question
collect:    bounded graph walk from module entry points along linker + code edges
trace:      Claude Code reads the shortlisted code (read-only tools) and returns cited steps
verify:     every citation checked against the checkout (no LLM)
render:     plain-English answer + Mermaid with sentence labels
learn:      save Q&A + flow (private), queue suggested scenarios, propose module-note updates
"""

from __future__ import annotations

import re
import uuid
from collections import Counter, deque
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field

from kb.agent.state import AgentState
from kb.errors import KbError
from kb.llm.base import LLMRequest
from kb.llm.json_call import ask_json
from kb.mapper.cluster import LINKER_KINDS
from kb.packs.index import STOP, build_index, search, words
from kb.packs.model import Citation, Flow, FlowStep, QAItem, Suggestion
from kb.packs.staleness import hash_span, is_stale

MAX_DEPTH = 4
MAX_NODES = 40
REUSE_OVERLAP = 0.6
KIND_ORDER = ["route", "component", "graphql_op", "resolver", "http_route", "queue_topic", "message_topic"]

SYSTEM = (
    "You explain how a software feature works, for people who may not read code. "
    "Trace the real code: open the files you are given, follow calls, and only describe what you read. "
    "Use the product's glossary terms. Plain English, one action per step. "
    "Do not quote source code: describe what the code does in words (names of screens, actions and data are fine)."
)
_FENCE = re.compile(r"```.*?(```|$)", re.S)
_INLINE = re.compile(r"`([^`]*)`")
_CODEY = re.compile(r"[{}();=<>]|=>|\bconst\b|\breturn\b")


def strip_code(text: str) -> str:
    """Saved prose must not carry source (spec 3.4): drop fenced blocks and code-like inline spans."""
    text = _FENCE.sub(" ", text or "")
    text = _INLINE.sub(lambda m: m.group(1) if not _CODEY.search(m.group(1)) and len(m.group(1)) <= 60 else "", text)
    return re.sub(r"[ \t]{2,}", " ", re.sub(r"\n{3,}", "\n\n", text)).strip()


# ---------- understand ----------

def _kind(q: str) -> str:
    low = q.lower()
    if "what breaks" in low or "impact" in low or "if i change" in low:
        return "impact"
    if "diagram" in low:
        return "diagram"
    if low.startswith("where") or "where is" in low or "where are" in low:
        return "where"
    return "how"


def _terms(q: str) -> set[str]:
    out = set()
    for w in words(q):
        if w in STOP or len(w) < 3:
            continue
        out.add(w[:-1] if w.endswith("s") and len(w) > 4 else w)
    return out


def _overlap(a: str, b: str) -> float:
    ta, tb = _terms(a), _terms(b)
    return len(ta & tb) / len(ta | tb) if ta | tb else 0.0


def _reusable_flow(q: str, module_ids: list[str], deps) -> str | None:
    """A saved, still-fresh flow that answers the same question. Retrieval searches saved flows and
    answers only (a module summary may outrank them on the whole index); the decision is question
    overlap, a shared module and freshness, not relative search score."""
    pack = deps.pack
    by_id = {f.id: f for f in pack.flows}
    qa_flow = {x.id: x.flow_id for x in pack.qa}
    candidates: list[str] = []
    for h in search(deps.pack_dir, q, limit=20, kinds={"flow", "qa"}):
        fid = h.ref if h.kind == "flow" else qa_flow.get(h.ref)
        if fid and fid in by_id and fid not in candidates:
            candidates.append(fid)
    for fid in candidates:
        flow = by_id[fid]
        if module_ids and not set(flow.module_ids) & set(module_ids):
            continue
        if _overlap(flow.question, q) < REUSE_OVERLAP:
            continue
        if any(s.citation and is_stale(s.citation, deps.checkouts) for s in flow.steps):
            continue
        return fid
    return None


def understand(state: AgentState, deps) -> dict:
    q = state["question"]
    pack = deps.pack
    hits = search(deps.pack_dir, q, limit=40)
    module_ids = [h.ref for h in hits if h.kind == "module"][:3]
    if not module_ids:
        owner = {m: mod.id for mod in pack.modules for m in mod.members}
        counts = Counter(owner[h.ref] for h in hits if h.kind == "node" and h.ref in owner)
        module_ids = [m for m, _ in counts.most_common(3)]

    seed = state.get("seed_qa_id")
    if seed:
        qa = next((x for x in pack.qa if x.id == seed), None)
        flow = next((f for f in pack.flows if qa and f.id == qa.flow_id), None)
        for m in (flow.module_ids if flow else []):
            if m not in module_ids:
                module_ids.append(m)

    out: dict = {"kind": _kind(q), "module_ids": module_ids, "not_found": False, "reused_flow_id": None}
    if not module_ids and not any(h.kind == "node" for h in hits):
        out["not_found"] = True
        return out

    if not seed and not state.get("no_reuse"):
        out["reused_flow_id"] = _reusable_flow(q, module_ids, deps)
    return out


# ---------- collect ----------

def collect(state: AgentState, deps) -> dict:
    g = deps.pack.graph
    mods = [deps.pack.module(m) for m in state.get("module_ids", []) if any(x.id == m for x in deps.pack.modules)]
    allowed = {m for mod in mods for m in mod.members}
    starts = [e for mod in mods for e in mod.entry_points if g.has_node(e)]
    for h in search(deps.pack_dir, state["question"], limit=20, kinds={"node"}):
        # no modules (e.g. an incomplete pack): any node the search found may start the walk
        if (not mods or h.ref in allowed) and h.ref not in starts and g.has_node(h.ref):
            starts.append(h.ref)

    seen: dict[str, int] = {}
    queue = deque((s, 0) for s in starts)
    while queue and len(seen) < MAX_NODES:
        nid, depth = queue.popleft()
        if nid in seen:
            continue
        seen[nid] = depth
        if depth >= MAX_DEPTH:
            continue
        nxt = g.neighbors(nid, kinds=LINKER_KINDS, direction="both")
        nxt += [n for n in g.neighbors(nid, direction="out")]
        node = g.node(nid)
        file_id = f"{node.repo}:{node.file}"
        if node.kind != "file" and g.has_node(file_id):
            nxt.append(g.node(file_id))  # linker-made nodes (ops, resolvers) have no contains edge
        for n in sorted({n.id: n for n in nxt}.values(), key=lambda n: n.id):
            if n.id not in seen:
                queue.append((n.id, depth + 1))

    rank = {k: i for i, k in enumerate(KIND_ORDER)}
    ordered = sorted(seen, key=lambda nid: (rank.get(g.node(nid).kind, len(KIND_ORDER)), seen[nid], nid))
    return {"shortlist": ordered[:MAX_NODES]}


# ---------- trace ----------

class TraceStep(BaseModel):
    side: str = Field(description="'ui' for what happens in the app, 'backend' for server/background work")
    text: str = Field(description="One plain-English sentence describing this step")
    repo: str = Field(description="Repo name the cited code is in")
    file: str = Field(description="Path relative to that repo's root")
    line_start: int
    line_end: int


class TraceOut(BaseModel):
    steps: list[TraceStep]
    answer: str = Field(description="2-5 sentence plain-English answer")


def _repos_line(deps) -> str:
    return "\n".join(f"- {name}: {co.root}" for name, co in deps.checkouts.items())


def trace(state: AgentState, deps) -> dict:
    g = deps.pack.graph
    pack = deps.pack
    no_source = not deps.checkouts
    mods = [pack.module(m) for m in state.get("module_ids", []) if any(x.id == m for x in pack.modules)]
    shortlist = state.get("shortlist", [])
    lines = []
    for nid in shortlist:
        if g.has_node(nid):
            n = g.node(nid)
            lines.append(f"- {n.repo} | {n.file}:{n.line or 1} | {n.name} ({n.kind})")
    gaps = [e for e in g.edges() if not e.resolved and e.src in set(shortlist)]

    parts = [f"Question: {state['question']}"]
    if no_source:
        parts.append("NOTE: no source available — answer only from the module summaries and code map below. "
                     "Still cite repo/file/lines from the map where you can.")
    else:
        parts.append("Repos (read files with these roots):\n" + _repos_line(deps))
    parts.append("Modules:\n" + "\n".join(f"- {m.name}: {pack.module_notes.get(m.id) or m.summary}" for m in mods))
    parts.append("Code map (start here; follow imports and calls as needed):\n" + ("\n".join(lines) or "(none)"))
    if gaps:
        parts.append("Links the map could not resolve (search for these):\n"
                     + "\n".join(f"- {g.node(e.src).name} -{e.kind}-> {e.dst.split(':', 2)[-1]}" for e in gaps))
    seed = state.get("seed_qa_id")
    if seed:
        qa = next((x for x in pack.qa if x.id == seed), None)
        if qa:
            parts.append(f"This is a follow-up to an earlier answer.\nEarlier question: {qa.question}\nEarlier answer: {qa.answer}")
    if deps.context:
        parts.append("Glossary:\n" + deps.context[:6000])
    if deps.extra.get("trace_context"):  # set by test planning: the PR's changed code and decision points
        parts.append(deps.extra["trace_context"])
    parts.append("Return 3-12 steps in the order they happen, UI steps first. Every step must cite the repo, "
                 "the file path relative to that repo's root, and the line range you read. "
                 "For the step where the app sends a request to the server, cite the operation's definition "
                 "(the graphql_op entries in the code map) so readers see exactly which request links UI and server. "
                 "If you could not find part of the flow, say so in the answer instead of guessing.")

    roots = [co.root for co in deps.checkouts.values()]
    req = LLMRequest(prompt="\n\n".join(parts), system=SYSTEM, cwd=roots[0] if roots else Path(deps.pack_dir),
                     extra_dirs=roots[1:], allow_tools=not no_source)
    out = ask_json(deps.provider, req, TraceOut, deps.tracer, "trace")
    return {"draft_steps": [s.model_dump() for s in out.steps], "answer": out.answer, "no_source": no_source}


# ---------- verify ----------

def _locate(step: dict, checkouts: dict) -> tuple[str, str] | None:
    file = str(step.get("file", "")).strip()
    order = [step.get("repo")] + [r for r in checkouts if r != step.get("repo")]
    for repo in order:
        co = checkouts.get(repo)
        if co is None:
            continue
        root = Path(co.root).resolve()
        p = Path(file)
        target = (p if p.is_absolute() else root / file.removeprefix("./")).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            continue  # outside this checkout (e.g. "../../x") or missing
        return repo, target.relative_to(root).as_posix()
    return None


def verify(state: AgentState, deps) -> dict:
    steps = []
    for d in state.get("draft_steps", []):
        side = d.get("side") if d.get("side") in ("ui", "backend") else "backend"
        loc = _locate(d, deps.checkouts)
        citation, ok = None, False
        if loc:
            repo, rel = loc
            root = deps.checkouts[repo].root
            n = len((Path(root) / rel).read_text(errors="replace").splitlines())
            a, b = int(d.get("line_start", 0)), int(d.get("line_end", 0))
            if 1 <= a <= b <= n:
                citation = Citation(repo=repo, file=rel, line_start=a, line_end=b, content_hash=hash_span(root, rel, a, b))
                ok = True
        steps.append(FlowStep(side=side, text=strip_code(str(d.get("text", ""))), citation=citation, verified=ok))
    return {"steps": steps}


# ---------- render ----------

def _label(s: FlowStep) -> str:
    text = s.text.replace('"', "'").replace("\n", " ")
    if len(text) > 80:
        text = text[:77] + "..."
    return text + ("" if s.verified else " (unverified)")


def mermaid_for(steps: list[FlowStep]) -> str:
    ordered = [s for s in steps if s.side == "ui"] + [s for s in steps if s.side != "ui"]
    lines = ["flowchart LR"]
    for i, s in enumerate(ordered, 1):
        lines.append(f'  s{i}["{_label(s)}"]')
    lines += [f"  s{i} --> s{i + 1}" for i in range(1, len(ordered))]
    return "\n".join(lines)


def render(state: AgentState, deps) -> dict:
    pack = deps.pack
    if state.get("not_found"):
        terms = ", ".join(sorted(_terms(state["question"]))) or state["question"]
        answer = (f"I couldn't find code for this in {pack.manifest.project}@{pack.manifest.branch}. "
                  f"Searched modules and symbols for: {terms}.")
        if pack.manifest.status == "incomplete":
            answer += " This pack is incomplete (module map not finished); run `kb analyze --resume` and ask again."
        return {"answer": answer, "steps": [], "mermaid": ""}
    fid = state.get("reused_flow_id")
    if fid:
        flow = next(f for f in pack.flows if f.id == fid)
        qa = next((x for x in pack.qa if x.flow_id == fid), None)
        return {"answer": qa.answer if qa else flow.question, "steps": flow.steps, "mermaid": flow.mermaid}
    answer = strip_code(state.get("answer", ""))
    if state.get("no_source"):
        answer = f"No source verification — {answer}"
    return {"answer": answer, "mermaid": mermaid_for(state.get("steps", []))}


# ---------- learn ----------

class ModuleNote(BaseModel):
    module_id: str
    improved_summary: str


class LearnOut(BaseModel):
    suggestions: list[str] = Field(description="3-5 related questions a newcomer would ask next")
    module_notes: list[ModuleNote] = Field(default_factory=list,
                                           description="Improved 3-5 sentence summaries, only for modules this answer taught you something new about")


def learn(state: AgentState, deps) -> dict:
    m = deps.pack.manifest
    now = datetime.now(timezone.utc)
    steps = state.get("steps", [])
    flow = Flow(id=uuid.uuid4().hex[:12], question=state["question"], module_ids=state.get("module_ids", []),
                steps=steps, mermaid=state.get("mermaid", ""), created_at=now)
    qa = QAItem(id=uuid.uuid4().hex[:12], question=state["question"], answer=state.get("answer", ""), flow_id=flow.id,
                citations=[s.citation for s in steps if s.citation], author=deps.author, created_at=now)
    deps.store.save_flow(m.project, m.branch, flow)
    deps.store.save_qa(m.project, m.branch, qa)

    mods = [deps.pack.module(x) for x in flow.module_ids if any(y.id == x for y in deps.pack.modules)]
    prompt = "\n\n".join([
        f"A user asked: {state['question']}",
        f"Answer given: {qa.answer}",
        "Steps:\n" + "\n".join(f"- [{s.side}] {s.text}" for s in steps),
        "Modules involved (id: current summary):\n" + "\n".join(f"- {x.id}: {deps.pack.module_notes.get(x.id) or x.summary}" for x in mods),
        "Suggest related questions and improve module summaries where this answer adds something.",
    ])
    suggestions: list[str] = []
    try:
        out = ask_json(deps.provider, LLMRequest(prompt=prompt, system=SYSTEM, cwd=Path(deps.pack_dir)), LearnOut,
                       deps.tracer, "learn")
        suggestions = [strip_code(s) for s in out.suggestions if strip_code(s)][:5]
        deps.store.add_suggestions(m.project, m.branch, [Suggestion(id=uuid.uuid4().hex[:12], question=s, source_qa_id=qa.id)
                                                         for s in suggestions])
        valid = {x.id for x in mods}
        for note in out.module_notes:
            text = strip_code(note.improved_summary)
            if note.module_id in valid and text:
                deps.store.save_module_note(m.project, m.branch, note.module_id, text)
    except KbError as e:  # the answer is already saved; suggestions are optional
        deps.extra.setdefault("warnings", []).append(f"suggestions skipped: {e}")
    build_index(deps.pack_dir, deps.store.load(m.project, m.branch))
    return {"saved_qa_id": qa.id, "suggestions": suggestions}
