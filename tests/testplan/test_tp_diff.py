from kb.testplan.diff import Hunk, repo_diff

from fixture_repos import RESOLVERS, git, make_pr_fixture


def test_repo_diff_fixture(tmp_path):
    fx = make_pr_fixture(tmp_path)
    hunks = repo_diff(fx.be, "mini-be", "develop", "feature/repeat-end", [])
    assert {h.file for h in hunks} == {RESOLVERS}
    added = [line for h in hunks for line in h.added]
    removed = [line for h in hunks for line in h.removed]
    assert "    if (!context.user.isAdmin) {" in added
    assert "    if (task.notify) {" in removed
    assert all(isinstance(h, Hunk) and h.repo == "mini-be" for h in hunks)
    h = next(h for h in hunks if "    if (!context.user.isAdmin) {" in h.added)
    assert h.new_start > 0 and h.new_len >= 1


def test_diff_follows_renames(tmp_path):
    fx = make_pr_fixture(tmp_path)
    git(fx.be, "checkout", "-q", "feature/repeat-end")
    model = fx.be / "src/module/tasks/task/model.ts"
    text = model.read_text()
    model.unlink()
    (fx.be / "src/module/tasks/task/task-model.ts").write_text(text + "// renamed\n")
    git(fx.be, "add", "-A")
    git(fx.be, "commit", "-qm", "rename")
    git(fx.be, "checkout", "-q", "develop")
    hunks = [h for h in repo_diff(fx.be, "mini-be", "develop", "feature/repeat-end", [])
             if h.file.endswith("task-model.ts")]
    assert len(hunks) == 1 and hunks[0].old_file == "src/module/tasks/task/model.ts"
    assert hunks[0].added == ["// renamed"]


def test_ignore_and_subpath(tmp_path):
    fx = make_pr_fixture(tmp_path)
    assert repo_diff(fx.be, "mini-be", "develop", "feature/repeat-end", ["**/resolvers.ts"]) == []
    hunks = repo_diff(fx.be, "mini-be", "develop", "feature/repeat-end", [], subpath="src/module")
    assert {h.file for h in hunks} == {"tasks/task/resolvers.ts"}
