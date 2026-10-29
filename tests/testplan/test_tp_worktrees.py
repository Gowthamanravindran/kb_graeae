import pytest

from kb.config import load_config
from kb.errors import KbError
from kb.testplan.worktrees import require_git, worktree_at

from fixture_repos import CREATION, RESOLVERS, git, make_pr_fixture


def test_fixture_has_expected_content(tmp_path):
    fx = make_pr_fixture(tmp_path)
    assert "notifyAssignee" in (fx.be / RESOLVERS).read_text()  # base checked out
    assert "isAdmin" in git(fx.be, "show", f"feature/repeat-end:{RESOLVERS}")
    assert "repeatEndsAt" in git(fx.web, "show", f"feature/repeat-end:{CREATION}")


def test_worktree_at_does_not_touch_working_copy(tmp_path):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    (fx.be / "scratch.txt").write_text("my edit")
    co = worktree_at(cfg.repo("mini-be"), "feature/repeat-end", tmp_path / "work")
    assert git(fx.be, "rev-parse", "--abbrev-ref", "HEAD") == "develop"
    assert (fx.be / "scratch.txt").read_text() == "my edit"
    assert "isAdmin" in (co.root / RESOLVERS).read_text()
    assert co.commit == git(fx.be, "rev-parse", "feature/repeat-end")
    assert git(fx.be, "worktree", "list").count("\n") == 0  # nothing registered in the user's .git


def test_worktree_reused_for_same_commit(tmp_path):
    fx = make_pr_fixture(tmp_path)
    cfg = load_config(fx.cfg_path)
    a = worktree_at(cfg.repo("mini-be"), "feature/repeat-end", tmp_path / "work")
    b = worktree_at(cfg.repo("mini-be"), "feature/repeat-end", tmp_path / "work")
    assert a.root == b.root


def test_require_git_lists_plain_folders(tmp_path):
    fx = make_pr_fixture(tmp_path)
    plain = tmp_path / "plain"
    plain.mkdir()
    y = tmp_path / "plain.yaml"
    y.write_text(fx.cfg_path.read_text().replace(str(fx.web), str(plain)))
    cfg = load_config(y)
    with pytest.raises(KbError, match="PR analysis needs git repos for: mini-fe"):
        require_git(cfg, ["mini-fe", "mini-be"], tmp_path / "work")
