"""Add a project from repo links or folders: KB inspects the repos and writes kb.yaml itself."""

import json
import subprocess
from pathlib import Path

import pytest
import yaml

from kb.config import load_config
from kb.errors import KbError
from kb.project_setup import NewProject, NewRepo, inspect_repos, is_url, suggest_linkers, write_project


def git(cwd, *a):
    return subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _pkg(d: Path, deps: dict, **extra):
    d.mkdir(parents=True, exist_ok=True)
    (d / "package.json").write_text(json.dumps({"name": d.name, "dependencies": deps, **extra}))


def _repo(root: Path, branch="main") -> Path:
    git(root, "init", "-q", "-b", branch)
    git(root, "config", "user.email", "t@x"), git(root, "config", "user.name", "t")
    git(root, "add", "-A")
    git(root, "commit", "-qm", "init")
    return root


@pytest.fixture
def repos(tmp_path):
    fe = tmp_path / "sop-fe-master"
    _pkg(fe, {}, workspaces=["packages/*"])
    _pkg(fe / "packages" / "sop-web", {"react": "18", "react-router-dom": "6", "@apollo/client": "3"})
    _pkg(fe / "packages" / "ui-kit", {"react": "18"})
    (fe / "packages" / "sop-web" / "src").mkdir()
    (fe / "packages" / "sop-web" / "src" / "a.ts").write_text("export const a = 1;\n")
    _repo(fe, "master")
    be = tmp_path / "sop-backend-main"
    _pkg(be, {"express": "4", "graphql-compose": "9", "apollo-server-express": "3", "bull": "4"})
    (be / "CONTEXT.md").write_text("# context\n")
    _repo(be, "develop")
    git(be, "branch", "feature/x")
    return fe, be


def test_is_url():
    assert is_url("https://github.com/acme/sop-fe") and is_url("git@github.com:acme/sop-fe.git")
    assert is_url("ssh://git@host/x.git") and not is_url("/Users/me/sop-fe") and not is_url("~/code/x")


def test_inspect_local_monorepo_and_backend(repos, tmp_path):
    fe, be = repos
    got = inspect_repos([str(fe), str(be)], tmp_path / "home")
    web, back = got.repos
    assert (web.name, web.role, web.kind, web.default_branch, web.path) == ("sop-web", "frontend", "local", "master", "packages/sop-web")
    assert [p.path for p in web.path_options] == ["packages/sop-web", "packages/ui-kit"]
    assert (back.name, back.role, back.default_branch) == ("sop-backend", "backend", "develop")
    assert set(back.branches) == {"develop", "feature/x"}
    assert back.context_docs == ["CONTEXT.md"]
    lk = {(x.type, x.from_, x.to, x.in_): x.options for x in got.linkers}
    assert lk == {("graphql", "sop-web", "sop-backend", None): {"backend_style": "graphql-compose"},
                  ("react-router", None, None, "sop-web"): {}, ("bull-queue", None, None, "sop-backend"): {}}


def test_inspect_url_repo_clones_to_scratch_and_reads_remote_default(repos, tmp_path):
    _, be = repos
    bare = tmp_path / "remote" / "sop-backend.git"
    git(tmp_path, "clone", "-q", "--bare", str(be), str(bare))
    got = inspect_repos([f"file://{bare}"], tmp_path / "home")
    r = got.repos[0]
    assert (r.kind, r.name, r.role, r.default_branch) == ("url", "sop-backend", "backend", "develop")
    assert "feature/x" in r.branches
    assert not list((tmp_path / "home" / "work" / "_inspect").glob("*"))  # scratch clone removed


def test_inspect_plain_folder_and_bad_sources(tmp_path):
    plain = tmp_path / "copy"
    _pkg(plain, {"react": "18"})
    r = inspect_repos([str(plain)], tmp_path / "home").repos[0]
    assert r.default_branch is None and not r.is_git and r.role == "frontend"
    with pytest.raises(KbError, match="no folder"):
        inspect_repos([str(tmp_path / "missing")], tmp_path / "home")
    with pytest.raises(KbError, match="could not read"):
        inspect_repos([f"file://{tmp_path}/nope.git"], tmp_path / "home")


def test_same_name_twice_gets_a_suffix(repos, tmp_path):
    fe, _ = repos
    got = inspect_repos([str(fe), str(fe)], tmp_path / "home")
    assert [r.name for r in got.repos] == ["sop-web", "sop-web-2"]


def test_no_graphql_link_without_both_sides(tmp_path):
    from kb.project_setup import RepoInspection

    fe = RepoInspection(source="x", kind="local", name="web", role="frontend", signals={"graphql_client", "react_router"})
    assert [x.type for x in suggest_linkers([fe])] == ["react-router"]


def test_write_project_creates_yaml_and_refuses_duplicates(repos, tmp_path):
    fe, be = repos
    home = tmp_path / "home"
    got = inspect_repos([str(fe), str(be)], home)
    spec = NewProject(project="sop", repos=[NewRepo.from_inspection(r) for r in got.repos], linkers=got.linkers,
                      context_docs=["sop-backend:CONTEXT.md"])
    path = write_project(home, spec)
    assert path == home / "projects" / "sop" / "kb.yaml"
    cfg = load_config(path)
    assert [(r.name, r.local_path, r.path, r.default_branch) for r in cfg.repos] == [
        ("sop-web", fe, "packages/sop-web", "master"), ("sop-backend", be, None, "develop")]
    assert {lk.type for lk in cfg.linkers} == {"graphql", "react-router", "bull-queue"}
    assert yaml.safe_load(path.read_text())["linkers"][0]["from"] == "sop-web"
    with pytest.raises(KbError, match="already exists"):
        write_project(home, spec)


def test_write_project_validates(tmp_path):
    with pytest.raises(KbError, match="project name"):
        write_project(tmp_path, NewProject(project="../x", repos=[NewRepo(name="a", role="frontend", source="/tmp")]))
    with pytest.raises(KbError, match="at least one repo"):
        write_project(tmp_path, NewProject(project="x", repos=[]))
    with pytest.raises(KbError, match="two repos named a"):
        write_project(tmp_path, NewProject(project="x", repos=[NewRepo(name="a", role="frontend", source="/tmp"),
                                                               NewRepo(name="a", role="backend", source="/tmp")]))


def test_monorepo_tie_picks_the_biggest_app(tmp_path):
    fe = tmp_path / "sop-fe"
    _pkg(fe, {}, workspaces=["packages/*"])
    app = {"react": "18", "react-router-dom": "6", "@apollo/client": "3"}
    _pkg(fe / "packages" / "sop-editor", app)  # same signals, alphabetically first, small
    (fe / "packages" / "sop-editor" / "src").mkdir()
    (fe / "packages" / "sop-editor" / "src" / "a.tsx").write_text("export const a = 1;\n")
    _pkg(fe / "packages" / "sop-web", app)
    (fe / "packages" / "sop-web" / "src").mkdir()
    for i in range(30):
        (fe / "packages" / "sop-web" / "src" / f"p{i}.tsx").write_text("export const p = 1;\n")
    r = inspect_repos([str(fe)], tmp_path / "home").repos[0]
    assert (r.path, r.name) == ("packages/sop-web", "sop-web")
