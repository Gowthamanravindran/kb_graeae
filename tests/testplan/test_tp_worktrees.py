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


def _commit_file(repo, name, text, msg):
    (repo / name).write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", msg)
    return git(repo, "rev-parse", "HEAD")


def test_url_repo_base_follows_origin(tmp_path):
    from kb.config import RepoConfig

    fx = make_pr_fixture(tmp_path)
    repo = RepoConfig(name="mini-be", role="backend", url=str(fx.be))
    first = worktree_at(repo, "develop", tmp_path / "work").commit
    new = _commit_file(fx.be, "later.ts", "export const later = 1;\n", "master moved on")
    assert new != first
    assert worktree_at(repo, "develop", tmp_path / "work", refresh=True).commit == new  # what an impact run does


def test_remote_only_pr_branch_new_commit_reaches_mirror(tmp_path):
    from kb.config import RepoConfig

    fx = make_pr_fixture(tmp_path)
    # the user's clone only has the PR branch as origin/feature/repeat-end
    user = tmp_path / "user-be"
    git(tmp_path, "clone", "-q", str(fx.be), str(user))
    repo = RepoConfig(name="mini-be", role="backend", local_path=user)
    worktree_at(repo, "origin/feature/repeat-end", tmp_path / "work")
    git(fx.be, "checkout", "-q", "feature/repeat-end")
    new = _commit_file(fx.be, "pr2.ts", "export const two = 2;\n", "second PR commit")
    git(fx.be, "checkout", "-q", "develop")
    git(user, "fetch", "-q", "origin")
    co = worktree_at(repo, "origin/feature/repeat-end", tmp_path / "work")
    assert co.commit == new and (co.root / "pr2.ts").exists()


def test_git_source_fetches_once_per_minute(tmp_path, monkeypatch):
    from kb.config import RepoConfig
    from kb.testplan import worktrees

    fx = make_pr_fixture(tmp_path)
    repo = RepoConfig(name="mini-be", role="backend", url=str(fx.be))
    worktrees.git_source(repo, tmp_path / "work")
    calls = []
    real = worktrees._git
    monkeypatch.setattr(worktrees, "_git", lambda cwd, *a: (calls.append(a), real(cwd, *a))[1])
    worktrees.git_source(repo, tmp_path / "work")
    worktrees.git_source(repo, tmp_path / "work")
    assert [a for a in calls if a[0] == "fetch"] == []


def _fork_pr(tmp_path, fx):
    """GitHub stand-in: the PR's commits live only under refs/pull/7/head (a fork's branch)."""
    gh = tmp_path / "github-be.git"
    git(tmp_path, "clone", "-q", "--bare", str(fx.be), str(gh))
    fork = tmp_path / "fork"
    git(tmp_path, "clone", "-q", str(gh), str(fork))
    git(fork, "config", "user.email", "f@x"), git(fork, "config", "user.name", "f")
    sha = _commit_file(fork, "fork.ts", "export const fork = 1;\n", "fork change")
    git(fork, "push", "-q", str(gh), f"HEAD:refs/pull/7/head")
    return gh, sha


def test_fork_pr_ref_for_a_local_repo(tmp_path):
    from kb.config import RepoConfig
    from kb.testplan.worktrees import diff_source

    fx = make_pr_fixture(tmp_path)
    gh, sha = _fork_pr(tmp_path, fx)
    user = tmp_path / "user-be"
    git(tmp_path, "clone", "-q", str(gh), str(user))
    repo = RepoConfig(name="mini-be", role="backend", local_path=user)
    co = worktree_at(repo, "pull/7/head", tmp_path / "work")
    assert co.commit == sha and (co.root / "fork.ts").exists()
    assert "refs/pull" not in git(user, "for-each-ref")  # the user's repo is untouched
    assert worktree_at(repo, sha, tmp_path / "work").commit == sha  # a later design run asks by commit
    src = diff_source(repo, tmp_path / "work", sha)
    assert git(src, "cat-file", "-t", sha) == "commit"


def test_fork_pr_ref_for_a_url_repo(tmp_path):
    from kb.config import RepoConfig

    fx = make_pr_fixture(tmp_path)
    gh, sha = _fork_pr(tmp_path, fx)
    repo = RepoConfig(name="mini-be", role="backend", url=str(gh))
    assert worktree_at(repo, "pull/7/head", tmp_path / "work").commit == sha


def test_prune_and_worktree_share_a_lock(tmp_path):
    from kb.testplan import worktrees

    assert isinstance(worktrees._wt_lock, type(threading_lock()))
    held = []
    real = worktrees.shutil.rmtree
    def rmtree(p, **k):
        held.append(worktrees._wt_lock.locked())
        return real(p, **k)
    import os, time
    d = tmp_path / "work" / "worktrees" / "r" / "abc"
    d.mkdir(parents=True)
    os.utime(d, (time.time() - 40 * 86400,) * 2)
    worktrees.shutil.rmtree = rmtree
    try:
        worktrees.prune_work(tmp_path / "work")
    finally:
        worktrees.shutil.rmtree = real
    assert held == [True]


def threading_lock():
    import threading
    return threading.Lock()


def test_pull_ref_must_match_exactly():
    from kb.testplan.worktrees import PULL_REF

    assert PULL_REF.fullmatch("pull/12/head") and not PULL_REF.fullmatch("pull/12/head\n")
