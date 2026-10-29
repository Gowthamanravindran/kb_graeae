"""Nodes of the Impact graph (spec §3.1). Only `name_flows` calls Claude (once)."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from pydantic import BaseModel

from kb.errors import KbError
from kb.llm.base import LLMRequest
from kb.llm.json_call import ask_json
from kb.testplan.delta import find_span, graph_delta, head_graph
from kb.testplan.diff import repo_diff, repo_renames
from kb.testplan.inventory import ALIGN, _similar, inventory
from kb.testplan.models import BranchRef, ChangeSet, ImpactReport, PrInfo
from kb.testplan.reach import reach, structural_items
from kb.testplan.slots import assign_priorities, estimate_calls, plan_slots, risk
from kb.testplan.store import PlanStore
from kb.testplan.worktrees import diff_source, prune_work, require_git, worktree_at


class ChangeSetRequest(BaseModel):
    project: str
    base: dict[str, str]
    head: dict[str, str | None]  # None / "" = this repo is unchanged
    pr_links: dict[str, str] = {}
    title: str = ""


@dataclass
class ImpactDeps:
    cfg: object
    store: object  # PackStore
    provider: object
    tracer: object
    author: str = "unknown"
    report: object = None
    extra: dict = field(default_factory=dict)


def describe(branches: dict[str, str]) -> str:
    vals = set(branches.values())
    return next(iter(vals)) if len(vals) == 1 else " · ".join(f"{k} {v}" for k, v in branches.items())


def plan_id_for(project: str, base: dict[str, str], head: dict[str, str]) -> str:
    key = "|".join(f"{r}:{base[r]}->{head.get(r) or ''}" for r in sorted(base))
    return "pr-" + hashlib.sha1(f"{project}|{key}".encode()).hexdigest()[:10]


def resolve_change_set(state: dict, deps: ImpactDeps) -> dict:
    from kb.testplan.github import pr_info

    cfg, req = deps.cfg, state["request"]
    deps.report("Reading the change set", 0.02)
    # base: what the request names, else the PR's own base, else kb.yaml's default_branch
    base = {r.name: (req.base.get(r.name) or "").strip() for r in cfg.repos}
    head = {r.name: (req.head.get(r.name) or "").strip() or None for r in cfg.repos}
    prs: dict[str, PrInfo] = {}
    for repo, url in (req.pr_links or {}).items():
        if not url:
            continue
        pr = pr_info(url)
        prs[repo] = PrInfo(url=url, title=pr.title, body=pr.body, issues=pr.issues)
        head[repo] = head.get(repo) or pr.head_ref
        base[repo] = base.get(repo) or pr.base_ref
    base = {r.name: base[r.name] or (r.default_branch or "") for r in cfg.repos}
    missing = [r for r, b in base.items() if not b]
    if missing:
        raise KbError(f"pick a base branch for: {', '.join(missing)}")
    work = deps.store.kb_home / "work" / cfg.project
    prune_work(work)
    require_git(cfg, [r.name for r in cfg.repos], work)
    label = deps.store.find_pack_by_branches(cfg.project, base)
    if label is None:
        raise KbError(f"Analyse {describe(base)} first")
    base_cos = {r: worktree_at(cfg.repo(r), b, work, refresh=True) for r, b in base.items()}
    head_cos = {r: (worktree_at(cfg.repo(r), h, work) if h else base_cos[r]) for r, h in head.items()}
    pid = plan_id_for(cfg.project, base, {r: h or "" for r, h in head.items()})
    title = req.title or next((p.title for p in prs.values() if p.title), "") or \
        f"{describe({r: h for r, h in head.items() if h}) or 'No changes'} vs {describe(base)}"
    cs = ChangeSet(id=pid, kind="pr", project=cfg.project, title=title, base_pack=label,
                   base={r: BranchRef(branch=b, commit=base_cos[r].commit) for r, b in base.items()},
                   head={r: BranchRef(branch=head[r] or base[r], commit=head_cos[r].commit) for r in base},
                   prs=prs, created_by=deps.author)
    PlanStore(deps.store).save_change_set(label, cs)
    return {"change_set": cs, "label": label, "base_cos": base_cos, "head_cos": head_cos, "work": work}


def diff(state: dict, deps: ImpactDeps) -> dict:
    cs, cfg = state["change_set"], deps.cfg
    hunks, renames = [], {}
    for r, ref in cs.base.items():
        if cs.head[r].commit == ref.commit:
            continue
        src = diff_source(cfg.repo(r), state["work"], ref.commit, cs.head[r].commit)
        hunks += repo_diff(src, r, ref.commit, cs.head[r].commit, cfg.ignore, subpath=cfg.repo(r).path)
        renames.update({(r, o): n for o, n in repo_renames(src, ref.commit, cs.head[r].commit, cfg.repo(r).path).items()})
    return {"hunks": hunks, "renames": renames}


def head_graph_node(state: dict, deps: ImpactDeps) -> dict:
    deps.report("Building the PR's code graph", 0.15)
    return {"head_graph": head_graph(deps.cfg, state["head_cos"], state["work"]),
            "base_graph": head_graph(deps.cfg, state["base_cos"], state["work"])}


def delta(state: dict, deps: ImpactDeps) -> dict:
    deps.report("Comparing with the base", 0.5)
    return {"delta": graph_delta(state["base_graph"], state["head_graph"], state["hunks"],
                                 state["base_cos"], state["head_cos"], state.get("renames"))}


def inventory_node(state: dict, deps: ImpactDeps) -> dict:
    items = inventory(state["base_cos"], state["head_cos"], state["delta"], state["hunks"], state.get("renames"))
    items += structural_items(state["delta"], state["base_graph"], state["head_graph"])
    return {"inventory": items}


def reach_node(state: dict, deps: ImpactDeps) -> dict:
    deps.report("Finding the affected flows", 0.6)
    pack = deps.store.load(deps.cfg.project, state["label"])
    flows = reach(state["head_graph"], state["base_graph"], pack, state["delta"], state["inventory"])
    return {"flows": flows}


class FlowNames(BaseModel):
    names: dict[str, str]


def name_flows(state: dict, deps: ImpactDeps) -> dict:
    flows = state["flows"]
    if not flows:
        return {}
    deps.report("Naming the affected flows", 0.7)
    cs = state["change_set"]
    intent = "\n".join(f"PR {r}: {p.title}\n{p.body[:1500]}" for r, p in cs.prs.items())
    lines = [f"- {f.id}: entry {f.entry_points[0]}; changed: {', '.join(c.split(':', 1)[-1] for c in f.changed_functions[:8])}"
             for f in flows]
    prompt = ("Name each user-facing flow below in plain English, as a QA tester would describe it "
              "(for example 'Admin sets a task to repeat weekly'). Use what each entry point and changed function "
              "suggest; keep each name under 10 words.\n\n" + (intent + "\n\n" if intent else "") + "\n".join(lines) +
              '\n\nReturn JSON: {"names": {"<flow id>": "<name>"}}')
    try:
        out = ask_json(deps.provider, LLMRequest(prompt=prompt, system="You name software flows for QA testers.",
                                                 cwd=Path(state["work"]), allow_tools=False),
                       FlowNames, deps.tracer, "name_flows")
        for f in flows:
            if out.names.get(f.id):
                f.name = out.names[f.id].strip()
    except KbError as e:  # Claude unavailable or unusable answer: keep entry-point names
        deps.extra.setdefault("warnings", []).append(f"flow names from code: {e}")
    return {"flows": flows}


def plan_slots_node(state: dict, deps: ImpactDeps) -> dict:
    flows, inv = state["flows"], state["inventory"]
    for f in flows:
        f.risk, f.risk_reasons = risk(f, inv)
    slots = plan_slots(flows, inv, "pr")
    assign_priorities(slots, flows, inv)
    for f in flows:
        mine = [s for s in slots if s.flow_id == f.id]
        f.est_claude_calls = estimate_calls(mine, flows, set(), set(), "pr")
    return {"slots": slots, "flows": sorted(flows, key=lambda f: (-f.risk, f.id))}


def full_reanalysis_check(state: dict, deps: ImpactDeps) -> dict:
    pack = deps.store.load(deps.cfg.project, state["label"])
    module_files = {m.split("#", 1)[0] for mod in pack.modules for m in mod.members}
    added = [(h.repo, h.file, len(h.added)) for h in state["hunks"]]
    total = sum(n for *_, n in added) or 1
    outside = sum(n for r, f, n in added if f"{r}:{f}" not in module_files)
    base_tops = {(n.repo, n.file.split("/", 1)[0]) for n in state["base_graph"].nodes(kind="file")}
    new_tops = sorted({f"{r}:{f.split('/', 1)[0]}" for r, f, _ in added if "/" in f and (r, f.split("/", 1)[0]) not in base_tops})
    flag = bool(pack.modules) and (outside / total > 0.3 or bool(new_tops))
    reason = ""
    if flag:
        reason = (f"New top-level folder {', '.join(new_tops)} isn't part of any module." if new_tops else
                  f"{round(100 * outside / total)}% of the changed lines are outside every module.")
    return {"needs_full": flag, "full_reason": reason}


def _function_hashes(state: dict) -> dict[str, str]:
    """Hash of each changed function's head source, to tell later which written cases went stale."""
    out = {}
    for fid in {f for fl in state["flows"] for f in fl.changed_functions}:
        repo, rest = fid.split(":", 1)
        file, _, name = rest.partition("#")
        co = state["head_cos"].get(repo)
        try:
            src = (Path(co.root) / file).read_text(encoding="utf-8", errors="replace") if co else ""
        except OSError:
            src = ""
        span = find_span(src, file, name) if name else None
        text = "\n".join(src.splitlines()[span[0] - 1:span[1]]) if span else src
        out[fid] = hashlib.sha1(text.encode()).hexdigest()[:12]
    return out


def save_impact(state: dict, deps: ImpactDeps) -> dict:
    cs, label = state["change_set"], state["label"]
    ps = PlanStore(deps.store)
    hashes = _function_hashes(state)
    d = state["delta"]
    report = ImpactReport(
        change_set_id=cs.id, head_commits={r: h.commit for r, h in cs.head.items()},
        graph_delta={"nodes_added": d.nodes_added, "nodes_removed": d.nodes_removed, "nodes_changed": d.nodes_changed,
                     "edges_added": len(d.edges_added), "edges_removed": len(d.edges_removed),
                     "function_hashes": hashes, "repos_changed": sorted({h.repo for h in state["hunks"]}),
                     "functions_changed": len({v for v in d.hunk_owner.values() if "#" in v} | set(d.nodes_removed))},
        inventory=state["inventory"], flows=state["flows"], needs_full_reanalysis=state["needs_full"],
        full_reanalysis_reason=state["full_reason"], created_by=deps.author)
    previous = ps.load_impact(cs.project, label, cs.id)
    reconcile(ps, cs.project, label, cs.id, state["slots"], previous, report)
    ps.save_impact(cs.project, label, report)
    deps.report("Impact ready", 1.0)
    return {"impact": report}


def reconcile(ps: PlanStore, project: str, label: str, pid: str, new_slots, previous: ImpactReport | None,
              report: ImpactReport) -> None:
    """Spec §5: same slot id keeps its case; its code changed → stale; gone → obsolete (case kept).
    Runs inside the slots lock, so a case written by a generation job meanwhile is never lost."""
    old_hash = (previous.graph_delta.get("function_hashes", {}) if previous else {})
    new_hash = report.graph_delta.get("function_hashes", {})
    inv = {i.id: i for i in report.inventory}
    flows = {f.id: f for f in report.flows}

    def touched(slot) -> bool:
        fns = []
        for iid in slot.inventory_ids:
            it = inv.get(iid)
            if it and it.function:
                fns += [f for f in new_hash if f.startswith(f"{it.repo}:{it.file}#{it.function}")]
        if not fns and slot.flow_id in flows:
            fns = flows[slot.flow_id].changed_functions
        # a function with no previous hash wasn't changed by the PR before, so it now differs from what the
        # case was written against; with no hashes at all (an old report) nothing can be told
        return bool(old_hash) and any(old_hash.get(f) != new_hash.get(f) for f in fns if f in new_hash)

    old_inv = {i.id: i for i in (previous.inventory if previous else [])}

    def successor(o, unmatched: list) -> object | None:
        """The new slot standing for old slot `o` whose decision point was edited, not removed
        (`isAdmin` → `isAdmin || isOwner`): same flow, type, outcome and rule, and a similar decision point
        in the same function."""
        oi = old_inv.get(o.inventory_ids[0]) if o.inventory_ids else None
        if oi is None:
            return None
        best, score = None, 0.0
        for s in unmatched:
            ni = inv.get(s.inventory_ids[0]) if s.inventory_ids else None
            if (ni is None or (s.flow_id, s.scenario_type, s.outcome, s.rule) != (o.flow_id, o.scenario_type, o.outcome, o.rule)
                    or (ni.repo, ni.function, ni.kind) != (oi.repo, oi.function, oi.kind)):
                continue
            r = _similar(oi.text or oi.summary, ni.text or ni.summary)
            if r > score:
                best, score = s, r
        return best if score >= ALIGN else None

    def carry(s, o, cases, stale: bool) -> None:
        s.status, s.case_id, s.assigned_to, s.fail_reason = o.status, o.case_id, o.assigned_to, o.fail_reason
        s.reserved_by = o.reserved_by
        if s.status in ("written", "stale") and stale:
            s.status = "stale"
            c = cases.get(s.case_id or "")
            if c is not None:
                ps.save_case(project, label, pid, c.model_copy(update={"stale": True, "status": "stale", "slot_id": s.id}))

    def merge(current: list) -> None:
        old = {s.id: s for s in current}
        cases = {c.id: c for c in ps.cases(project, label, pid)}
        merged = []
        unmatched = []
        for s in new_slots:
            o = old.pop(s.id, None)
            if o is not None:
                carry(s, o, cases, touched(s))
            else:
                unmatched.append(s)
            merged.append(s)
        for oid, o in list(old.items()):  # an edited decision point keeps its cases, now stale
            if not (o.case_id or o.status in ("written", "stale")):
                continue
            s = successor(o, unmatched)
            if s is not None:
                unmatched.remove(s)
                carry(s, o, cases, True)
                del old[oid]
        for o in old.values():  # slots whose decision point disappeared
            if o.case_id or o.status in ("written", "stale", "obsolete"):
                o.status = "obsolete"
                c = cases.get(o.case_id or "")
                if c is not None:
                    ps.save_case(project, label, pid, c.model_copy(update={"status": "obsolete"}))
                merged.append(o)
        current[:] = merged
    ps.update_slots(project, label, pid, merge)
