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


def test_reach_scans_edges_a_bounded_number_of_times(tmp_path, monkeypatch):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    work = tmp_path / "work"
    base = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.base.items()}
    head = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.head.items()}
    hunks = repo_diff(fx.be, "mini-be", "develop", fx.head_branch, [])
    bg, hg = build_graph(cfg, base, work / "b").merged, head_graph(cfg, head, work)
    d = graph_delta(bg, hg, hunks, base, head)
    inv = inventory(base, head, d, hunks)
    calls = []
    real = hg.edges
    monkeypatch.setattr(hg, "edges", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    reach(hg, bg, None, d, inv)
    assert len(calls) <= 2


def _rest_and_message_graph():
    g = CodeGraph()
    for n in (N("web:src/Users.tsx", "file"), N("api:src/users.ts", "file"), N("api:src/users.ts#getUser", "function"),
              N("api:http:GET /users/:id", "http_route"), N("api:http:GET /health", "http_route"),
              N("api:src/health.ts", "file"), N("api:src/health.ts#health", "function"),
              N("api:msg:kafka:order.created", "message_topic"), N("mail:src/on.ts", "file"),
              N("mail:src/on.ts#onOrder", "function"), N("web:route:/users", "route")):
        if n.kind in ("http_route", "message_topic", "route"):   # entries are labelled by their name
            n = n.model_copy(update={"name": n.id.split(":", 2)[-1].split(":")[-1] if n.kind != "http_route"
                                     else n.id.split("http:", 1)[1]})
        g.add_node(n)
    for s, d, k in (("web:route:/users", "web:src/Users.tsx", "routes_to"),
                    ("web:src/Users.tsx", "api:http:GET /users/:id", "calls_http"),
                    ("api:http:GET /users/:id", "api:src/users.ts#getUser", "served_by"),
                    ("api:http:GET /health", "api:src/health.ts#health", "served_by"),
                    ("api:src/users.ts", "api:msg:kafka:order.created", "publishes"),
                    ("api:msg:kafka:order.created", "mail:src/on.ts#onOrder", "consumed_by")):
        g.add_edge(CodeEdge(src=s, dst=d, kind=k, origin="linker"))
    return g


def test_rest_call_reaches_the_screen_and_an_uncalled_endpoint_is_its_own_entry():
    from kb.testplan.delta import GraphDelta

    g = _rest_and_message_graph()
    flows = reach(g, g, None, GraphDelta(nodes_changed=["api:src/users.ts#getUser", "api:src/health.ts#health"]), [])
    assert sorted((f.entry_points[0], f.name) for f in flows) == [
        ("api:http:GET /health", "API GET /health"), ("web:route:/users", "Screen /users")]
    assert next(f for f in flows if f.entry_points == ["web:route:/users"]).crosses_repos


def test_message_consumer_change_enters_at_its_topic():
    from kb.testplan.delta import GraphDelta

    g = _rest_and_message_graph()
    [f] = reach(g, g, None, GraphDelta(nodes_changed=["mail:src/on.ts#onOrder"]), [])
    assert (f.entry_points, f.name, f.has_job) == (["api:msg:kafka:order.created"], "Message order.created", True)
