"""Shapes the desktop screens need, built from packs, the registry and jobs. Pure functions."""

from __future__ import annotations

import json
from pathlib import Path


def local_branches(store, project: str) -> dict[str, dict]:
    out = {}
    root = Path(store.kb_home) / "packs"
    for d in sorted(root.glob(f"{project}@*")) if root.exists() else []:
        m = d / "manifest.json"
        if not m.exists():
            continue
        manifest = json.loads(m.read_text())
        pack = store.load(project, manifest["branch"])
        out[manifest["branch"]] = {
            "version": manifest.get("version", 1), "status": manifest["status"], "commits": manifest["commits"],
            "author": manifest.get("author"), "created_at": manifest.get("created_at"),
            "modules": len(pack.modules), "qa_total": len(pack.qa),
            "qa_published": sum(1 for q in pack.qa if q.visibility == "published"),
            "synced": bool(manifest.get("synced")), "stats": manifest.get("stats", {}),
            "branches": manifest.get("branches") or {r: manifest["branch"] for r in manifest["commits"]},
            "testplans": len(list((d / "overlay" / "testplans").glob("*/changeset.json"))),
        }
    return out


def branch_rows(project: str, store, remote: list[dict] | None, active: dict[str, str]) -> list[dict]:
    """active: branch -> running analyze job id."""
    local = local_branches(store, project)
    remote_by = {r["branch"]: r for r in (remote or [])}
    rows = []
    for b in sorted(set(local) | set(remote_by) | set(active)):  # a first analysis has nothing saved yet
        lo, re_ = local.get(b), remote_by.get(b)
        rem = None if re_ is None else {"latest_version": re_["latest_version"], "overlay_count": re_["overlay_count"],
                                        "created_by": re_["created_by"], "commits": re_["commits"]}
        if b in active:
            state = "in-progress"
        elif lo is None:
            state = "not-pulled"
        elif rem is None or not lo["synced"]:
            state = "local-only"
        elif rem["latest_version"] > lo["version"]:
            state = "update-available"
        else:
            state = "synced"
        rows.append({"branch": b, "local": lo, "remote": rem, "state": state, "job_id": active.get(b)})
    return rows


def ask_payload(state: dict, stats, seconds: float, pack) -> dict:
    names = {m.id: m.name for m in pack.modules}
    return {
        "answer": state.get("answer", ""), "reused": bool(state.get("reused_flow_id")),
        "reused_flow_id": state.get("reused_flow_id"), "saved_qa_id": state.get("saved_qa_id"),
        "suggestions": state.get("suggestions", []), "warnings": state.get("warnings", []),
        "mermaid": state.get("mermaid", ""), "not_found": bool(state.get("not_found")),
        "steps": [{"side": s.side, "text": s.text, "verified": s.verified,
                   "citation": s.citation.model_dump() if s.citation else None} for s in state.get("steps", [])],
        "how": {"modules": [names.get(m, m) for m in state.get("module_ids", [])],
                "shortlist_size": len(state.get("shortlist", [])), "claude_calls": stats.claude_calls,
                "files_read": stats.files_read, "tool_steps": stats.tool_steps, "seconds": round(seconds, 1),
                "trace_id": stats.trace_id},
    }


def map_view(cfg, pack) -> dict:
    from collections import Counter

    roles = {r.name: r.role for r in cfg.repos}
    g = pack.graph
    owner = {m: mod.id for mod in pack.modules for m in mod.members}
    answered: dict[str, list[dict]] = {}
    flows = {f.id: f for f in pack.flows}
    for q in pack.qa:
        f = flows.get(q.flow_id) if q.flow_id else None
        for mid in (f.module_ids if f else []):
            answered.setdefault(mid, []).append({"qa_id": q.id, "question": q.question, "author": q.author,
                                                  "visibility": q.visibility})
    modules = []
    for mod in pack.modules:
        repos = {m.split(":", 1)[0] for m in mod.members}
        entries = []
        for e in mod.entry_points:
            if g.has_node(e):
                n = g.node(e)
                entries.append({"id": n.id, "name": n.name, "kind": n.kind, "file": n.file, "line": n.line})
        top = sorted({f"{m.split(':', 1)[0]}:{'/'.join(m.split(':', 1)[1].split('#')[0].split('/')[:-1])}" for m in mod.members})[:6]
        modules.append({
            "id": mod.id, "name": mod.name, "summary": pack.module_notes.get(mod.id) or mod.summary,
            "layer": "product" if any(roles.get(r, "backend") != "backend" for r in repos) else "behind",
            "size": len(mod.members), "top_paths": top, "entry_points": entries,
            "answered": answered.get(mod.id, []),
        })
    counts: Counter = Counter()
    for e in g.edges():
        a, b = owner.get(e.src), owner.get(e.dst)
        if e.resolved and a and b and a != b:
            counts[(a, b)] += 1
    edges = [{"source": a, "target": b, "count": n} for (a, b), n in counts.most_common(60)]
    apps = [{"repo": r.name, "role": r.role, "label": r.name} for r in cfg.repos
            if r.role in ("frontend", "web", "mobile")]
    return {"apps": apps, "modules": modules, "edges": edges, "system_md": pack.system_md}


def memory_view(pack, queued_ids: set[str]) -> dict:
    flows = {f.id: f for f in pack.flows}
    names = {m.id: m.name for m in pack.modules}
    items = []
    for q in sorted(pack.qa, key=lambda q: q.created_at, reverse=True):
        f = flows.get(q.flow_id) if q.flow_id else None
        state = "queued" if q.id in queued_ids else q.visibility
        items.append({"qa_id": q.id, "question": q.question, "answer_preview": q.answer[:200], "state": state,
                      "stale": q.stale, "author": q.author, "created_at": q.created_at.isoformat(),
                      "modules": [names.get(m, m) for m in (f.module_ids if f else [])],
                      "citations": len(q.citations), "has_flow": bool(f and f.steps)})
    sugg = [{"id": s.id, "question": s.question, "source_qa_id": s.source_qa_id} for s in pack.suggestions]
    counts = {"all": len(items), "private": sum(1 for i in items if i["state"] in ("private", "queued")),
              "published": sum(1 for i in items if i["state"] == "published"),
              "stale": sum(1 for i in items if i["stale"]), "suggested": len(sugg)}
    return {"items": items, "suggestions": sugg, "counts": counts}
