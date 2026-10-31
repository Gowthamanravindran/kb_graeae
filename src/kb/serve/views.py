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


MAX_ITEMS = 40
_KIND_UNRESOLVED = {"handled_by": "graphql", "sends_graphql": "graphql", "enqueues": "queue", "consumed_by": "queue",
                    "routes_to": "route", "calls_http": "http", "served_by": "http", "publishes": "message"}


def _unresolved_kind(e) -> str:
    if e.dst.startswith("unresolved:"):   # `unresolved:<what>:…` says which kind of link it was
        what = e.dst.split(":", 2)[1]
        if what in ("http", "message", "queue", "graphql", "route"):
            return what
    return _KIND_UNRESOLVED.get(e.kind, e.kind)


def connections_view(cfg, pack) -> dict:
    """How the repos are wired together, at the level of each repo's part of a module: GraphQL and REST calls
    from one repo to another, queue producers → topic → consumers, messages through Kafka / SQS / RabbitMQ, and
    the routes that open each screen. Built from the linker edges only (no LLM)."""
    g = pack.graph
    roles = {r.name: r.role for r in cfg.repos}
    names = {m.id: m.name for m in pack.modules}
    owner: dict[str, str] = {}
    for mod in pack.modules:
        for m in mod.members:
            owner.setdefault(m, mod.id)
            owner.setdefault(m.split("#", 1)[0], mod.id)  # its file

    def node(i: str):
        return g.node(i) if g.has_node(i) else None

    def loc(i: str) -> dict | None:
        n = node(i)
        return {"repo": n.repo, "file": n.file, "line": n.line or 1} if n else None

    def group(i: str) -> str | None:
        n = node(i)
        if n is None:
            return None
        mod = owner.get(i) or owner.get(f"{n.repo}:{n.file}")
        key = f"{n.repo}::{mod or '(other)'}"
        if key not in nodes:
            nodes[key] = {"id": key, "lane": n.repo, "kind": "module", "module_id": mod,
                          "name": names.get(mod, f"Other code in {n.repo}") if mod else f"Other code in {n.repo}",
                          "code_name": mod or "(other)", "routes": [], "links": 0}
        return key

    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    unresolved: dict[str, int] = {}

    def link(a: str | None, b: str | None, kind: str, label: str, frm: dict | None, to: dict | None):
        if not a or not b:
            return
        e = edges.setdefault((a, b, kind), {"id": f"{a}->{b}:{kind}", "source": a, "target": b, "kind": kind,
                                            "count": 0, "items": []})
        item = {"label": label, "from": frm, "to": to}
        if item in e["items"]:
            return
        e["count"] += 1
        if len(e["items"]) < MAX_ITEMS:
            e["items"].append(item)
        nodes[a]["links"] += 1
        nodes[b]["links"] += 1

    def workers(consumer: str | None) -> str:
        """Jobs are grouped by the code that runs them (one box per worker area, not per queue topic)."""
        if consumer is None:
            key, name, code = "jobs::(none)", "Queued, no worker found", "(no consumer)"
        else:
            c = node(consumer)
            mod = owner.get(consumer) or (owner.get(f"{c.repo}:{c.file}") if c else None)
            key = f"jobs::{c.repo if c else ''}::{mod or '(other)'}"
            name = f"{names.get(mod, 'Other')} jobs" if mod else f"Other jobs in {c.repo if c else '?'}"
            code = f"{mod or '(other)'} (jobs)"
        nodes.setdefault(key, {"id": key, "lane": "jobs", "kind": "jobs", "module_id": None, "name": name,
                               "code_name": code, "routes": [], "links": 0})
        return key

    linker = [e for e in g.edges() if e.origin == "linker"]
    for e in linker:
        if not e.resolved or e.dst.startswith("unresolved:"):
            k = _unresolved_kind(e)
            unresolved[k] = unresolved.get(k, 0) + 1
    out_by: dict[tuple[str, str], list[str]] = {}
    in_by: dict[tuple[str, str], list[str]] = {}
    for e in linker:
        if e.resolved and not e.dst.startswith("unresolved:"):
            out_by.setdefault((e.src, e.kind), []).append(e.dst)
            in_by.setdefault((e.dst, e.kind), []).append(e.src)
    for e in linker:
        if not e.resolved or e.dst.startswith("unresolved:"):
            continue
        src, dst = node(e.src), node(e.dst)
        if src is None or dst is None:
            continue
        if e.kind == "routes_to":
            k = group(e.dst)
            if k:
                nodes[k]["routes"].append({"path": src.name, "repo": src.repo, "file": src.file, "line": src.line or 1})
        elif e.kind == "sends_graphql":  # screen → operation → resolver: one line per operation
            for r in out_by.get((e.dst, "handled_by"), []):
                link(group(e.src), group(r), "graphql", dst.name, loc(e.src), loc(r))
        elif e.kind == "handled_by":
            continue  # drawn from the sending side
        elif e.kind == "enqueues":  # producer → topic → consumer: one line from producer to the worker area
            consumers = out_by.get((e.dst, "consumed_by"), [])
            for c in consumers or [None]:
                link(group(e.src), workers(c), "queue", dst.name, loc(e.src), loc(c) if c else loc(e.dst))
        elif e.kind == "calls_http":  # client → endpoint → handler: one line per endpoint
            for h in out_by.get((e.dst, "served_by"), []):
                link(group(e.src), group(h), "http", dst.name, loc(e.src), loc(h))
        elif e.kind == "publishes":  # sender → topic → handler, coloured by broker (`<repo>:msg:<broker>:<name>`)
            broker = e.dst.split(":msg:", 1)[1].split(":", 1)[0] if ":msg:" in e.dst else "message"
            for c in out_by.get((e.dst, "consumed_by"), []):
                link(group(e.src), group(c), broker, dst.name, loc(e.src), loc(c))
        elif e.kind in ("consumed_by", "served_by"):
            continue  # drawn from the sending side
        else:
            link(group(e.src), group(e.dst), e.kind, f"{src.name} → {dst.name}", loc(e.src), loc(e.dst))
    keep = {k for k, n in nodes.items() if n["links"] or n["routes"]}
    order = [r.name for r in cfg.repos if roles.get(r.name) in ("frontend", "web", "mobile")] + \
            [r.name for r in cfg.repos if roles.get(r.name) not in ("frontend", "web", "mobile")]
    used = {nodes[k]["lane"] for k in keep}
    lanes = [{"id": r, "label": r, "role": roles[r]} for r in order if r in used]
    if "jobs" in used:
        lanes.append({"id": "jobs", "label": "Background jobs", "role": "jobs"})
    for n in nodes.values():
        n["routes"].sort(key=lambda r: r["path"])
    return {"lanes": lanes,
            "nodes": sorted((nodes[k] for k in keep), key=lambda n: (-n["links"], n["name"])),
            "edges": sorted(edges.values(), key=lambda e: -e["count"]),
            "unresolved": unresolved}


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
