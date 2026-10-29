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


def _move(fx, edit=False):
    from fixture_repos import RESOLVERS, git

    git(fx.be, "checkout", "-q", "feature/repeat-end")
    new = RESOLVERS.replace("resolvers.ts", "task-resolvers.ts")
    git(fx.be, "mv", RESOLVERS, new)
    if edit:
        p = fx.be / new
        p.write_text(p.read_text().replace("if (task.repeat === 'weekly') {", "if (task.repeat === 'monthly') {"))
    git(fx.be, "commit", "-qam", "move")
    head = git(fx.be, "rev-parse", "HEAD")
    git(fx.be, "reset", "-q", "--hard", "HEAD~1")  # the move goes on its own branch
    git(fx.be, "branch", "-q", "moved", head)
    git(fx.be, "checkout", "-q", "develop")
    return head


def _inv_between(tmp_path, fx, base_ref, head_ref):
    from kb.testplan.diff import repo_renames
    from kb.testplan.inventory import inventory

    cfg = load_config(fx.cfg_path)
    work = tmp_path / "work2"
    base = {"mini-be": worktree_at(cfg.repo("mini-be"), base_ref, work), "mini-fe": worktree_at(cfg.repo("mini-fe"), "master", work)}
    head = {"mini-be": worktree_at(cfg.repo("mini-be"), head_ref, work), "mini-fe": base["mini-fe"]}
    hunks = repo_diff(fx.be, "mini-be", base_ref, head_ref, [])
    renames = {("mini-be", o): n for o, n in repo_renames(fx.be, base_ref, head_ref).items()}
    d = graph_delta(build_graph(cfg, base, work / "b").merged, head_graph(cfg, head, work), hunks, base, head, renames)
    return d, inventory(base, head, d, hunks, renames)


def test_pure_move_is_not_removal(tmp_path):
    fx = make_pr_fixture(tmp_path)
    _move(fx)
    d, items = _inv_between(tmp_path, fx, "feature/repeat-end", "moved")
    assert not [n for n in d.nodes_removed if "resolvers.ts#" in n]
    assert items == []


def test_move_with_edit_reports_only_the_edit(tmp_path):
    fx = make_pr_fixture(tmp_path)
    _move(fx, edit=True)
    d, items = _inv_between(tmp_path, fx, "feature/repeat-end", "moved")
    assert [(i.kind, i.change) for i in items] == [("branch", "changed")]
    assert "monthly" in items[0].summary and items[0].file.endswith("task-resolvers.ts")
