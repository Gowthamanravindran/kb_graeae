import pytest

from kb.graph.model import CodeEdge, CodeGraph, CodeNode


def n(repo, file, name=None, kind="function", line=1):
    nid = f"{repo}:{file}#{name}" if name else f"{repo}:{file}"
    return CodeNode(id=nid, repo=repo, kind=kind if name else "file", name=name or file, file=file, line=line, end_line=None)


def sample():
    g = CodeGraph()
    a, b = n("fe", "a.ts", "send"), n("be", "r.ts", "AddTask", kind="resolver")
    g.add_node(a)
    g.add_node(b)
    g.add_edge(CodeEdge(src=a.id, dst=b.id, kind="handled_by", origin="linker"))
    g.add_edge(CodeEdge(src=b.id, dst=a.id, kind="calls", origin="extracted"))
    return g, a, b


def test_json_roundtrip():
    g, _, _ = sample()
    g2 = CodeGraph.from_json(g.to_json())
    assert g2.to_json() == g.to_json()


def test_merge_keeps_both_repos():
    g1 = CodeGraph()
    g1.add_node(n("fe", "a.ts"))
    g2 = CodeGraph()
    g2.add_node(n("be", "b.ts"))
    g1.merge(g2)
    assert {x.repo for x in g1.nodes()} == {"fe", "be"}
    assert len(g1.nodes(repo="be")) == 1


def test_neighbors_filtered_by_kind_and_direction():
    g, a, b = sample()
    assert [x.id for x in g.neighbors(a.id, kinds={"handled_by"}, direction="out")] == [b.id]
    assert g.neighbors(a.id, kinds={"calls"}, direction="out") == []
    assert [x.id for x in g.neighbors(a.id, kinds={"calls"}, direction="in")] == [b.id]


def test_edge_with_missing_src_raises():
    g = CodeGraph()
    g.add_node(n("fe", "a.ts"))
    with pytest.raises(KeyError):
        g.add_edge(CodeEdge(src="fe:nope", dst="fe:a.ts", kind="calls", origin="extracted"))


def test_unresolved_edge_allowed_without_dst_node():
    g, a, _ = sample()
    g.add_edge(CodeEdge(src=a.id, dst="unresolved:graphql:X", kind="handled_by", origin="linker", resolved=False))
    assert [e.dst for e in g.edges(kind="handled_by") if not e.resolved] == ["unresolved:graphql:X"]
