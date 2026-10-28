import json

import pytest

from kb.errors import LLMUnavailable
from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.mapper.cluster import cluster_graph
from kb.mapper.mapper import map_modules, write_system_overview
from kb.tracing import NoopTracer

from fakes import FakeProvider


def add_folder(g, repo, folder, n=6):
    ids = []
    for i in range(n):
        f = f"src/{folder}/f{i}.ts"
        fid = f"{repo}:{f}"
        g.add_node(CodeNode(id=fid, repo=repo, kind="file", name=f, file=f, line=1))
        sid = f"{fid}#fn{i}"
        g.add_node(CodeNode(id=sid, repo=repo, kind="function", name=f"{folder.split('/')[-1]}Fn{i}", file=f, line=2))
        g.add_edge(CodeEdge(src=fid, dst=sid, kind="contains", origin="extracted"))
        ids.append(fid)
    for a, b in zip(ids, ids[1:]):
        g.add_edge(CodeEdge(src=a, dst=b, kind="imports", origin="extracted"))
    return ids


def graph():
    g = CodeGraph()
    fe = add_folder(g, "fe", "tasks/create")
    be = add_folder(g, "be", "module/tasks")
    add_folder(g, "be", "module/audits")
    g.add_edge(CodeEdge(src=fe[0], dst=be[0], kind="handled_by", origin="linker"))
    g.add_edge(CodeEdge(src=fe[1], dst=be[1], kind="handled_by", origin="linker"))
    return g


def test_cluster_deterministic():
    a = cluster_graph(graph(), min_size=1)
    b = cluster_graph(graph(), min_size=1)
    assert [(c.id, c.members) for c in a] == [(c.id, c.members) for c in b]


def test_linked_folders_merge():
    # resolution 1: on a 3-folder toy graph the SOP-tuned default (4) splits any pair
    clusters = cluster_graph(graph(), min_size=1, resolution=1.0)
    by_member = {m: c.id for c in clusters for m in c.members}
    assert by_member["fe:src/tasks/create/f0.ts"] == by_member["be:src/module/tasks/f0.ts"]
    assert by_member["be:src/module/audits/f0.ts"] != by_member["be:src/module/tasks/f0.ts"]
    assert all(c.top_paths for c in clusters)


def test_max_clusters_respected():
    assert len(cluster_graph(graph(), max_clusters=1, min_size=1)) == 1


def draft(name, entry):
    return "```json\n" + json.dumps({"name": name, "summary": f"{name} does things.", "entry_points": entry}) + "\n```"


def test_entry_points_outside_members_dropped(tmp_path):
    g = graph()
    clusters = cluster_graph(g, min_size=1)
    first = clusters[0]
    p = FakeProvider(default=draft("Thing", [first.members[0], "nope:not-a-member"]))
    mods, failed = map_modules(clusters, g, "Entity = brand", p, NoopTracer(), tmp_path / "ck", resume=False)
    assert not failed and len(mods) == len(clusters)
    assert mods[0].entry_points == [first.members[0]]
    assert "Entity = brand" in p.requests[0].prompt


def test_resume_skips_checkpointed(tmp_path):
    g = graph()
    clusters = cluster_graph(g, min_size=1)
    map_modules(clusters, g, "", FakeProvider(default=draft("T", [])), NoopTracer(), tmp_path / "ck", resume=False)
    p2 = FakeProvider()
    mods, failed = map_modules(clusters, g, "", p2, NoopTracer(), tmp_path / "ck", resume=True)
    assert p2.requests == [] and len(mods) == len(clusters) and not failed


def test_bad_json_retried_then_module_marked_failed(tmp_path):
    g = graph()
    clusters = cluster_graph(g, min_size=1)
    p = FakeProvider(["junk", "junk again"], default=draft("Ok", []))
    mods, failed = map_modules(clusters, g, "", p, NoopTracer(), tmp_path / "ck", resume=False)
    assert failed == [clusters[0].id]
    assert len(mods) == len(clusters) - 1
    assert not (tmp_path / "ck" / f"{clusters[0].id}.json").exists()


def test_empty_entry_points_fall_back_to_member(tmp_path):
    g = graph()
    clusters = cluster_graph(g, min_size=1)
    mods, _ = map_modules(clusters, g, "", FakeProvider(default=draft("T", [])), NoopTracer(), tmp_path / "ck", resume=False)
    assert all(m.entry_points and m.entry_points[0] in m.members for m in mods)


def test_llm_unavailable_propagates(tmp_path):
    g = graph()
    with pytest.raises(LLMUnavailable):
        map_modules(cluster_graph(g, min_size=1), g, "", FakeProvider(unavailable="not signed in"), NoopTracer(),
                    tmp_path / "ck", resume=False)


def test_system_overview_gets_mermaid_fallback(tmp_path):
    g = graph()
    clusters = cluster_graph(g, min_size=1)
    mods, _ = map_modules(clusters, g, "", FakeProvider(default=draft("Tasks", [])), NoopTracer(), tmp_path / "ck", resume=False)
    md = write_system_overview(mods, g, FakeProvider(["The system has parts."]), NoopTracer(), cwd=tmp_path)
    assert md.startswith("The system has parts.") and "```mermaid" in md
