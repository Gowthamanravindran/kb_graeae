import subprocess
import time

import pytest

from kb.config import RepoConfig
from kb.errors import KbError
from kb.ingest.checkout import checkout_repo


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def make_git_repo(path, branch="main"):
    path.mkdir()
    git(path, "init", "-q", "-b", branch)
    git(path, "config", "user.email", "t@t")
    git(path, "config", "user.name", "t")
    (path / "a.ts").write_text("export const a = 1;\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "init")
    return git(path, "rev-parse", "HEAD")


def test_git_repo_checkout_records_head(tmp_path):
    sha = make_git_repo(tmp_path / "r")
    co = checkout_repo(RepoConfig(name="r", role="backend", local_path=tmp_path / "r"), "main", tmp_path / "work")
    assert co.commit == sha
    assert co.root == tmp_path / "r"


def test_url_repo_is_cloned_at_branch(tmp_path):
    sha = make_git_repo(tmp_path / "origin", branch="dev")
    co = checkout_repo(RepoConfig(name="r", role="backend", url=str(tmp_path / "origin")), "dev", tmp_path / "work")
    assert co.commit == sha
    assert (co.root / "a.ts").exists()


def test_local_non_git_folder_gets_content_hash_commit(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "x.ts").write_text("1")
    (d / "node_modules").mkdir()
    (d / "node_modules" / "junk.js").write_text("ignored")
    repo = RepoConfig(name="p", role="frontend", local_path=d)
    c1 = checkout_repo(repo, "anything", tmp_path / "w").commit
    assert c1.startswith("local-") and len(c1) == len("local-") + 12
    (d / "node_modules" / "junk.js").write_text("changed but ignored")
    assert checkout_repo(repo, "other-label", tmp_path / "w").commit == c1
    time.sleep(0.01)
    (d / "x.ts").write_text("22")
    assert checkout_repo(repo, "anything", tmp_path / "w").commit != c1


def test_local_git_repo_wrong_branch_raises(tmp_path):
    make_git_repo(tmp_path / "r", branch="main")
    with pytest.raises(KbError):
        checkout_repo(RepoConfig(name="r", role="backend", local_path=tmp_path / "r"), "release", tmp_path / "w")


def test_subpath_applied_to_root(tmp_path):
    d = tmp_path / "mono"
    (d / "packages" / "web").mkdir(parents=True)
    co = checkout_repo(RepoConfig(name="w", role="frontend", local_path=d, path="packages/web"), "x", tmp_path / "w")
    assert co.root == d / "packages" / "web"


def test_missing_local_path_raises(tmp_path):
    with pytest.raises(KbError):
        checkout_repo(RepoConfig(name="w", role="frontend", local_path=tmp_path / "nope"), "x", tmp_path / "w")


def test_folder_inside_another_git_repo_is_plain_folder(tmp_path):
    make_git_repo(tmp_path / "outer", branch="main")
    inner = tmp_path / "outer" / "copy"
    inner.mkdir()
    (inner / "x.ts").write_text("1")
    co = checkout_repo(RepoConfig(name="c", role="frontend", local_path=inner), "release", tmp_path / "w")
    assert co.commit.startswith("local-")
