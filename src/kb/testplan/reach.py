"""From changed code up to the entry points people use: screens (routes), queue consumers, and API
resolvers no screen calls. One ImpactFlow per entry point, across repos via the linkers' edges."""

from __future__ import annotations

import hashlib
from collections import deque

from kb.graph.model import CodeGraph
from kb.testplan.delta import GraphDelta
from kb.testplan.models import ImpactFlow, InventoryItem

MAX_DEPTH = 14
ENTRY_KINDS = {"route", "queue_topic", "message_topic"}
API_KINDS = {"resolver", "http_route"}  # an API entry unless a screen calls it


def _parents(g: CodeGraph, nid: str, by_file: dict[tuple[str, str], list[str]], incoming: dict[str, list]) -> list[str]:
    """One step towards the user: who contains, imports, routes to, calls over GraphQL or REST, or consumes this."""
    out: list[str] = []
    node = g.node(nid)
    for e in incoming.get(nid, []):
        if e.kind in ("contains", "imports", "calls", "indirect_call", "routes_to", "sends_graphql", "handled_by",
                      "calls_http", "served_by"):
            out.append(e.src)
        elif e.kind == "consumed_by":
            out.append(e.src)  # queue or message topic consumed by this code: the topic is the entry
    if node.kind == "file":
        # a server file exposes its resolvers; reaching the file means reaching them
        out += [r for r in by_file.get((node.repo, node.file), []) if g.node(r).kind == "resolver"]
    elif node.kind not in ("resolver", "graphql_op", "route", "queue_topic", "http_route", "message_topic"):
        f = f"{node.repo}:{node.file}"
        if g.has_node(f):
            out.append(f)
    return out


def _flow_id(entry: str) -> str:
    return hashlib.sha1(entry.encode()).hexdigest()[:12]


def _label(g: CodeGraph, entry: str) -> str:
    n = g.node(entry)
    if n.kind == "route":
        return f"Screen {n.name}"
    if n.kind == "queue_topic":
        return f"Background job {n.name}"
    if n.kind == "message_topic":
        return f"Message {n.name}"
    if n.kind == "http_route":
        return f"API {n.name}"
    if n.kind == "resolver":
        return f"API {n.name.split(':', 1)[-1].split('@')[0]}"
    return n.name


def structural_items(delta: GraphDelta, base: CodeGraph, head: CodeGraph) -> list[InventoryItem]:
    """Added or removed routes, screens, queue jobs and GraphQL operations become inventory items too."""
    kinds = {"route": "route", "queue_topic": "job", "graphql_op": "argument", "component": "screen"}
    out = []
    for change, ids, g in (("added", delta.nodes_added, head), ("removed", delta.nodes_removed, base)):
        for nid in ids:
            n = g.node(nid)
            k = kinds.get(n.kind)
            if not k:
                continue
            iid = hashlib.sha1(f"{n.repo}|{n.file}|{n.name}|{k}|{change}".encode()).hexdigest()[:12]
            out.append(InventoryItem(id=iid, kind=k, change=change, repo=n.repo, file=n.file,
                                     lines=(n.line or 1, n.line or 1), function=n.name, outcomes=["works"],
                                     summary=f"{k.capitalize()} {n.name} {change}", text=n.name))
    return out


def reach(head: CodeGraph, base: CodeGraph, base_pack, delta: GraphDelta, inventory: list[InventoryItem]) -> list[ImpactFlow]:
    incoming: dict[str, list] = {}
    for e in head.edges():
        incoming.setdefault(e.dst, []).append(e)
    by_file: dict[tuple[str, str], list[str]] = {}
    for n in head.nodes():
        by_file.setdefault((n.repo, n.file), []).append(n.id)

    changed = [c for c in delta.nodes_changed + delta.nodes_added if head.has_node(c)]
    # a removed function: start from its file in the PR (it was called from there or from its importers)
    for r in delta.nodes_removed:
        repo, rest = r.split(":", 1)
        f = f"{repo}:{rest.split('#', 1)[0]}"
        if head.has_node(f):
            changed.append(f)
    flows: dict[str, ImpactFlow] = {}
    for start in dict.fromkeys(changed):
        parent: dict[str, str | None] = {start: None}
        q = deque([(start, 0)])
        entries = []
        while q:
            nid, depth = q.popleft()
            node = head.node(nid)
            ups = _parents(head, nid, by_file, incoming) if depth < MAX_DEPTH else []
            if node.kind in ENTRY_KINDS or (node.kind in API_KINDS and not any(
                    head.node(u).kind == "graphql_op" or (node.kind == "http_route" and head.has_node(u)) for u in ups)):
                entries.append(nid)
                if node.kind in ENTRY_KINDS:
                    continue
            for u in ups:
                if u not in parent and head.has_node(u):
                    parent[u] = nid
                    q.append((u, depth + 1))
        if not entries:
            entries = [start]
        for entry in entries:
            path, cur = [], entry
            while cur is not None:
                path.append(cur)
                cur = parent.get(cur)
            fid = _flow_id(entry)
            f = flows.setdefault(fid, ImpactFlow(id=fid, name=_label(head, entry), entry_points=[entry],
                                                 changed_functions=[], inventory_ids=[]))
            if start not in f.changed_functions:
                f.changed_functions.append(start)
            if len(path) > len(f.path):
                f.path = path

    enqueuers = {e.src for e in head.edges() if e.kind in ("enqueues", "publishes")}  # one scan, not one per path node
    removed_by_file = {}
    for r in delta.nodes_removed:
        repo, rest = r.split(":", 1)
        removed_by_file.setdefault(f"{repo}:{rest.split('#', 1)[0]}", []).append(r)
    for f in flows.values():
        for c in list(f.changed_functions):
            for r in removed_by_file.get(c, []):
                if r not in f.changed_functions:
                    f.changed_functions.append(r)
        files = {(c.split(":", 1)[0], c.split(":", 1)[1].split("#", 1)[0]) for c in f.changed_functions}
        names = {c.split("#", 1)[1].split("@")[0] for c in f.changed_functions if "#" in c}
        path_files = {(head.node(p).repo, head.node(p).file) for p in f.path if head.has_node(p)}
        structural = ("route", "job", "argument", "screen")
        f.inventory_ids = [i.id for i in inventory
                           if ((i.repo, i.file) in files and (not i.function or i.function in names or i.kind in structural))
                           or (i.kind in structural and (i.repo, i.file) in path_files)]
        f.crosses_repos = len({p.split(":", 1)[0] for p in f.path}) > 1
        f.has_job = any(head.node(p).kind in ("queue_topic", "message_topic") for p in f.path if head.has_node(p)) or any(
            p in enqueuers or p.split("#")[0] in enqueuers for p in f.path)
        if base_pack is not None:
            f.modules = sorted({m.id for m in base_pack.modules if set(m.members) & set(f.path + f.changed_functions)})
            cited = {(c.repo, c.file) for fl in base_pack.flows for s in fl.steps if (c := s.citation)}
            if files & cited:
                best = sorted((fl for fl in base_pack.flows if any(s.citation and (s.citation.repo, s.citation.file) in files
                                                                   for s in fl.steps)), key=lambda fl: fl.created_at)
                f.master_flow_id = best[-1].id if best else None
    return sorted(flows.values(), key=lambda f: f.id)
