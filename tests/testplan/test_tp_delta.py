import pytest

from kb.analyze import build_graph
from kb.config import load_config
from kb.testplan.delta import graph_delta, head_graph, symbol_spans
from kb.testplan.diff import repo_diff
from kb.testplan.worktrees import worktree_at

from fixture_repos import make_pr_fixture

BE = "mini-be:src/module/tasks/task/resolvers.ts"


def _graphs(tmp_path):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    work = tmp_path / "work"
    base = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.base.items()}
    head = {n: worktree_at(cfg.repo(n), b, work) for n, b in fx.head.items()}
    bg = build_graph(cfg, base, work / "base").merged
    hg = head_graph(cfg, head, work)
    hunks = repo_diff(fx.be, "mini-be", "develop", fx.head_branch, []) + \
        repo_diff(fx.web, "mini-fe", "master", fx.head_branch, [])
    return cfg, base, head, bg, hg, hunks


def test_symbol_spans_top_level():
    src = "const A = {\n  x: 1,\n};\n\nfunction b() {\n  return 1;\n}\nexport { A };\n"
    assert symbol_spans(src, "a.ts") == [("A", 1, 3), ("b", 5, 7)]


def test_graph_delta_fixture(tmp_path):
    cfg, base, head, bg, hg, hunks = _graphs(tmp_path)
    d = graph_delta(bg, hg, hunks, base, head)
    assert f"{BE}#AddTask" in d.nodes_changed
    assert f"{BE}#scheduleRepeat" in d.nodes_changed
    assert f"{BE}#notifyAssignee" in d.nodes_removed
    assert "mini-fe:src/tasks/TaskCreation.tsx#TaskCreation" in d.nodes_changed
    assert (BE, f"{BE}#notifyAssignee", "contains") in d.edges_removed
    owner = {hunks[i].file: v for i, v in d.hunk_owner.items()}
    assert owner["src/tasks/TaskCreation.tsx"] == "mini-fe:src/tasks/TaskCreation.tsx#TaskCreation"


def test_head_graph_cached(tmp_path):
    cfg, base, head, bg, hg, hunks = _graphs(tmp_path)
    again = head_graph(cfg, head, tmp_path / "work")
    assert {n.id for n in again.nodes()} == {n.id for n in hg.nodes()}
    assert list((tmp_path / "work" / "head_graphs").glob("*.json"))
