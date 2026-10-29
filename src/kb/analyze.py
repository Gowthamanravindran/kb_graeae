"""`kb analyze`: checkout -> graph -> link -> (LLM) module map -> pack.

Steps 1-3 never call an LLM; their output is saved (status "incomplete") before step 4,
so a missing Claude login or a crash in step 4 loses nothing. `resume` reuses per-module
checkpoints for the same commits.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from kb.config import ProjectConfig
from kb.graph.model import CodeEdge, CodeGraph
from kb.ingest.checkout import Checkout, checkout_repo
from kb.ingest.graphify import parse_graphify_json, run_graphify
from kb.linkers import ts
from kb.linkers.base import LinkContext, get_linker
from kb.llm.base import LLMProvider
from kb.mapper.cluster import cluster_graph
from kb.mapper.mapper import map_modules, write_system_overview
from kb.packs.index import build_index
from kb.packs.model import Manifest, commits_key
from kb.packs.staleness import is_stale
from kb.packs.store import PackStore


def _work_dir(store: PackStore, project: str, branch: str) -> Path:
    return store.kb_home / "work" / f"{project}@{branch.replace('/', '__')}"


def resolve_checkouts(cfg: ProjectConfig, branch: str, store: PackStore,
                      branches: dict[str, str] | None = None) -> dict[str, Checkout]:
    work = _work_dir(store, cfg.project, branch)
    return {r.name: checkout_repo(r, (branches or {}).get(r.name, branch), work / "repos") for r in cfg.repos}


def context_text(cfg: ProjectConfig, checkouts: dict[str, Checkout]) -> str:
    parts = []
    for repo, pattern in cfg.context_doc_globs():
        if repo not in checkouts:
            continue  # no source for that repo on this machine
        root = checkouts[repo].root
        for p in sorted(root.glob(pattern)):
            if p.is_file():
                parts.append(f"## {repo}:{p.relative_to(root).as_posix()}\n{p.read_text(errors='replace')}")
    return "\n\n".join(parts)


@dataclass
class GraphBuild:
    graphs: dict[str, CodeGraph]
    merged: CodeGraph
    links: list[CodeEdge]
    link_nodes: list
    n_files: int = 0
    unparsed: int = 0


def build_graph(cfg: ProjectConfig, checkouts: dict[str, Checkout], work: Path, tracer=None, on_link=None) -> GraphBuild:
    """Steps 2-3 of analysis (no LLM): Graphify per repo, then the linkers across repos."""
    from kb.tracing import NoopTracer

    tracer = tracer or NoopTracer()
    with tracer.span("graph") as sp:
        graphs: dict[str, CodeGraph] = {}
        unparsed = 0
        for name, co in checkouts.items():
            out = run_graphify(co, Path(work) / "graphify" / name, cfg.ignore)
            graphs[name] = parse_graphify_json(out, name, co.root, cfg.ignore)
            files = {n.file for n in graphs[name].nodes(kind="file")}
            unparsed += sum(1 for p in ts.iter_source_files(co.root, cfg.ignore)
                            if p.relative_to(co.root).as_posix() not in files)
        merged = CodeGraph()
        for g in graphs.values():
            merged.merge(g)
        n_files = sum(len(g.nodes(kind="file")) for g in graphs.values())
        sp.end(output={"files": n_files, "nodes": len(merged.nodes()), "unparsed": unparsed})
    if on_link:
        on_link()
    with tracer.span("link") as sp:
        repo_ids = {n.id for g in graphs.values() for n in g.nodes()}
        links: list[CodeEdge] = []
        for lk in cfg.linkers:
            edges = get_linker(lk.type).run(LinkContext(merged, checkouts, lk, tuple(cfg.ignore)))
            for e in edges:
                merged.add_edge(e)
            links += edges
        link_nodes = [n for n in merged.nodes() if n.id not in repo_ids]
        resolved = sum(1 for e in links if e.resolved)
        sp.end(output={"links_resolved": resolved, "links_unresolved": len(links) - resolved})
    return GraphBuild(graphs, merged, links, link_nodes, n_files, unparsed)


def _commits_key(commits: dict[str, str]) -> str:
    return commits_key(commits)


def run_analyze(cfg: ProjectConfig, branch: str, store: PackStore, provider: LLMProvider, tracer,
                resume: bool = False, author: str = "unknown", progress=None,
                branches: dict[str, str] | None = None) -> Manifest:
    """`branch` is the pack's label; `branches` (a branch set) picks each repo's branch, default `branch`."""
    report = progress or (lambda text, frac: None)
    work = _work_dir(store, cfg.project, branch)
    project = cfg.project
    with tracer.trace("analyze", {"project": project, "branch": branch}) as root:
        report("Checking out code", 0.02)
        with tracer.span("checkout") as sp:
            checkouts = resolve_checkouts(cfg, branch, store, branches)
            commits = {k: v.commit for k, v in checkouts.items()}
            sp.end(output=commits)

        report("Building the code graph", 0.1)
        gb = build_graph(cfg, checkouts, work, tracer, on_link=lambda: report("Linking frontend and backend", 0.3))
        graphs, merged, links, link_nodes = gb.graphs, gb.merged, gb.links, gb.link_nodes
        n_files, unparsed = gb.n_files, gb.unparsed
        resolved = sum(1 for e in links if e.resolved)

        stats = {"files": n_files, "unparsed": unparsed, "nodes": len(merged.nodes()),
                 "links_resolved": resolved, "links_unresolved": len(links) - resolved, "modules": 0}
        manifest = Manifest(project=project, branch=branch, commits=commits, author=author,
                            branches={r.name: (branches or {}).get(r.name, branch) for r in cfg.repos},
                            created_at=datetime.now(timezone.utc), status="incomplete", stats=stats)
        store.write_base(project, branch, manifest, graphs, links, [], "", link_nodes=link_nodes)
        _mark_stale(store, project, branch, checkouts)
        build_index(store.pack_dir(project, branch), store.load(project, branch))  # usable even if step 4 fails

        provider.check()
        with tracer.span("module_map") as sp:
            clusters = cluster_graph(merged)
            modules, failed = map_modules(clusters, merged, context_text(cfg, checkouts), provider, tracer,
                                          work / "modules" / _commits_key(commits), resume,
                                          on_module=lambda i, n: report(f"Naming modules ({i}/{n})", 0.35 + 0.55 * i / n))
            report("Writing the overview", 0.92)
            overview = write_system_overview(modules, merged, provider, tracer, cwd=work) if modules else ""
            sp.end(output={"modules": [m.name for m in modules], "failed": failed})

        store.write_modules(project, branch, modules, overview)
        manifest = store.update_manifest(project, branch, status="incomplete" if failed else "complete",
                                         failed_modules=failed, stats={**stats, "modules": len(modules)})
        build_index(store.pack_dir(project, branch), store.load(project, branch))
        root.end(output={"status": manifest.status, "stats": manifest.stats})
    tracer.flush()
    return manifest


def _mark_stale(store: PackStore, project: str, branch: str, checkouts: dict[str, Checkout]) -> None:
    pack = store.load(project, branch)
    for q in pack.qa:
        stale = any(is_stale(c, checkouts) for c in q.citations)
        if stale != q.stale:
            store.save_qa(project, branch, q.model_copy(update={"stale": stale}))
