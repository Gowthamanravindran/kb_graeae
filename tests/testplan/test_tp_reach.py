from kb.analyze import build_graph
from kb.config import load_config
from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.testplan.delta import graph_delta, head_graph
from kb.testplan.diff import repo_diff
from kb.testplan.inventory import inventory
from kb.testplan.reach import reach, structural_items
from kb.testplan.worktrees import worktree_at

from fixture_repos import make_pr_fixture

BE = "mini-be:src/module/tasks/task/resolvers.ts"


def test_reach_fixture(tmp_path):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    work = tmp_path / "work"
    base = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.base.items()}
    head = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.head.items()}
    hunks = repo_diff(fx.be, "mini-be", "develop", fx.head_branch, []) + \
        repo_diff(fx.web, "mini-fe", "master", fx.head_branch, [])
    bg, hg = build_graph(cfg, base, work / "b").merged, head_graph(cfg, head, work)
    d = graph_delta(bg, hg, hunks, base, head)
    inv = inventory(base, head, d, hunks)
    flows = reach(hg, bg, None, d, inv)
    assert len(flows) == 1
    f = flows[0]
    assert f.entry_points == ["mini-fe:route:/tasks/create-task"]
    assert "mini-fe:src/tasks/TaskCreation.tsx#TaskCreation" in f.changed_functions
    assert f"{BE}#AddTask" in f.changed_functions
    assert sorted(f.inventory_ids) == sorted(i.id for i in inv)
    assert f.crosses_repos and f.has_job
    assert f.path[0] == "mini-fe:route:/tasks/create-task" and f.path[-1].startswith(BE)


def N(i, kind, repo="r", file="a.ts"):
    return CodeNode(id=i, repo=repo, kind=kind, name=i, file=file)


def test_resolver_without_ui_is_own_entry():
    g = CodeGraph()
    for n in (N("r:a.ts", "file"), N("r:a.ts#Fn", "function"), N("r:a.ts#resolver:Q@1", "resolver")):
        g.add_node(n)
    g.add_edge(CodeEdge(src="r:a.ts", dst="r:a.ts#Fn", kind="contains", origin="extracted"))
    from kb.testplan.delta import GraphDelta

    flows = reach(g, g, None, GraphDelta(nodes_changed=["r:a.ts#Fn"]), [])
    assert [f.entry_points for f in flows] == [["r:a.ts#resolver:Q@1"]]


def test_structural_items_route_added():
    from kb.testplan.delta import GraphDelta

    head = CodeGraph()
    head.add_node(CodeNode(id="w:route:/new", repo="w", kind="route", name="/new", file="src/routes.ts", line=3))
    items = structural_items(GraphDelta(nodes_added=["w:route:/new"]), CodeGraph(), head)
    assert [(i.kind, i.change) for i in items] == [("route", "added")]
