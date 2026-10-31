"""Group the code graph into candidate product modules.

1. Seed: one group per (repo, first two folders under src/app/lib).
2. Group graph: edge weight = number of code edges between different groups; linker edges count
   LINKER_WEIGHT times, so a frontend folder and the backend folder it calls end up together.
3. Louvain communities (seed=0, resolution=RESOLUTION) on the group graph. Resolution 4 was
   tuned on the SOP repos: at 1, shared UI folders glued unrelated features into 5k-node blobs.
4. Communities smaller than `min_size` members join their most-connected neighbour;
   then the smallest merge into neighbours until at most `max_clusters` remain.
Deterministic for the same graph.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import networkx as nx

from kb.graph.model import CodeGraph

LINKER_KINDS = {"sends_graphql", "handled_by", "routes_to", "enqueues", "consumed_by", "calls_http", "served_by",
                "publishes"}
LINKER_WEIGHT = 5
RESOLUTION = 4.0
_ROOTS = {"src", "app", "lib"}


@dataclass
class Cluster:
    id: str
    members: list[str]
    top_paths: list[str]


def folder_key(repo: str, file: str) -> str:
    parts = file.split("/")[:-1]
    while parts and parts[0] in _ROOTS:
        parts = parts[1:]
    return f"{repo}:{'/'.join(parts[:2]) or '(root)'}"


def _slug(key: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", key.split(":", 1)[-1].lower()).strip("-") or "root"


def cluster_graph(g: CodeGraph, max_clusters: int = 60, min_size: int = 5, resolution: float = RESOLUTION) -> list[Cluster]:
    nodes = sorted(g.nodes(), key=lambda n: n.id)
    group_of = {n.id: folder_key(n.repo, n.file) for n in nodes}
    members: dict[str, list[str]] = defaultdict(list)
    for nid, key in group_of.items():
        members[key].append(nid)

    gg = nx.Graph()
    gg.add_nodes_from(sorted(members))
    w: Counter = Counter()
    for e in g.edges():
        if not e.resolved or e.src not in group_of or e.dst not in group_of:
            continue
        a, b = sorted((group_of[e.src], group_of[e.dst]))
        if a == b:
            continue  # inside-folder edges say nothing about which folders belong together
        w[(a, b)] += LINKER_WEIGHT if e.kind in LINKER_KINDS else 1
    for (a, b), weight in sorted(w.items()):
        gg.add_edge(a, b, weight=weight)

    comms = [sorted(c) for c in nx.community.louvain_communities(gg, weight="weight", resolution=resolution, seed=0)]
    comms.sort(key=lambda c: c[0])

    def size(c: list[str]) -> int:
        return sum(len(members[k]) for k in c)

    def link(c1: list[str], c2: list[str]) -> int:
        s1, s2 = set(c1), set(c2)
        return sum(d["weight"] for a, b, d in gg.edges(data=True) if a != b and ((a in s1 and b in s2) or (a in s2 and b in s1)))

    def merge_into_neighbour(i: int) -> bool:
        best, best_w = None, 0
        for j, other in enumerate(comms):
            if j != i:
                lw = link(comms[i], other)
                if lw > best_w or (lw == best_w and lw > 0 and best is not None and other[0] < comms[best][0]):
                    best, best_w = j, lw
        if best is None:
            return False
        comms[best] = sorted(comms[best] + comms[i])
        del comms[i]
        return True

    changed = True
    while changed:
        changed = False
        for i in sorted(range(len(comms)), key=lambda k: (size(comms[k]), comms[k][0])):
            if size(comms[i]) < min_size and len(comms) > 1 and merge_into_neighbour(i):
                changed = True
                break
    while len(comms) > max_clusters:
        i = min(range(len(comms)), key=lambda k: (size(comms[k]), comms[k][0]))
        if not merge_into_neighbour(i):
            j = min((k for k in range(len(comms)) if k != i), key=lambda k: (size(comms[k]), comms[k][0]))
            comms[j] = sorted(comms[j] + comms[i])
            del comms[i]

    out: list[Cluster] = []
    used: set[str] = set()
    for c in sorted(comms, key=lambda c: (-size(c), c[0])):
        top = sorted(c, key=lambda k: (-len(members[k]), k))
        cid = base = _slug(top[0])
        n = 2
        while cid in used:
            cid, n = f"{base}-{n}", n + 1
        used.add(cid)
        out.append(Cluster(cid, sorted(m for k in c for m in members[k]), top))
    return out
