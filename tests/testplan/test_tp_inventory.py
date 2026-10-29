from collections import Counter

from kb.analyze import build_graph
from kb.config import load_config
from kb.testplan.delta import graph_delta, head_graph
from kb.testplan.diff import Hunk, repo_diff
from kb.testplan.inventory import extract_decisions, inventory
from kb.testplan.worktrees import worktree_at

from fixture_repos import make_pr_fixture


def _fixture_inventory(tmp_path):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    work = tmp_path / "work"
    base = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.base.items()}
    head = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.head.items()}
    hunks = repo_diff(fx.be, "mini-be", "develop", fx.head_branch, []) + \
        repo_diff(fx.web, "mini-fe", "master", fx.head_branch, [])
    d = graph_delta(build_graph(cfg, base, work / "b").merged, head_graph(cfg, head, work), hunks, base, head)
    return inventory(base, head, d, hunks)


def test_inventory_fixture(tmp_path):
    items = _fixture_inventory(tmp_path)
    assert Counter(i.kind for i in items) == Counter({"permission": 1, "validation": 1, "branch": 1, "removed": 1, "field": 1})
    br = next(i for i in items if i.kind == "branch")
    assert br.change == "changed" and br.outcomes == ["true", "false"]
    assert "isAdmin" in next(i for i in items if i.kind == "permission").summary
    assert next(i for i in items if i.kind == "permission").outcomes == ["allowed", "denied"]
    assert next(i for i in items if i.kind == "validation").outcomes == ["valid", "invalid"]
    assert "notify" in next(i for i in items if i.kind == "removed").summary
    assert "repeatEndsAt" in next(i for i in items if i.kind == "field").summary
    assert all(len(i.id) == 12 for i in items)


def test_inventory_ids_stable_across_whitespace():
    a = extract_decisions("function f(x) {\n  if (x.a  &&  x.b) {\n    go();\n  }\n}\n", "a.ts", 1, 5)
    b = extract_decisions("function f(x) {\n  if (x.a && x.b) { go(); }\n}\n", "a.ts", 1, 3)
    assert a[0].norm == b[0].norm


def test_switch_outcomes():
    d = extract_decisions("function f(k) {\n  switch (k) {\n    case 'a': return 1;\n    case 'b': return 2;\n  }\n}\n", "a.ts", 1, 6)
    assert d[0].kind == "branch" and d[0].outcomes == ["'a'", "'b'", "default"]


def test_unknown_language_gives_code_item(tmp_path):
    from kb.ingest.checkout import Checkout
    from kb.testplan.delta import GraphDelta

    (tmp_path / "b").mkdir()
    (tmp_path / "h").mkdir()
    (tmp_path / "b" / "x.py").write_text("def f():\n    return 1\n")
    (tmp_path / "h" / "x.py").write_text("def f():\n    return 2\n")
    h = Hunk(repo="r", file="x.py", old_file="x.py", old_start=2, old_len=1, new_start=2, new_len=1,
             added=["    return 2"], removed=["    return 1"])
    d = GraphDelta(hunk_owner={0: "r:x.py#f"})
    items = inventory({"r": Checkout("r", tmp_path / "b", "1")}, {"r": Checkout("r", tmp_path / "h", "2")}, d, [h])
    assert [(i.kind, i.function, i.outcomes) for i in items] == [("code", "f", ["changed"])]
