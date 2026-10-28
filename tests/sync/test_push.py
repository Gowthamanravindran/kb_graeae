import hashlib

import pytest
import yaml
from typer.testing import CliRunner

from kb.cli import app
from kb.config import ProjectConfig
from kb.errors import KbError
from kb.sync.commands import push_pack
from kb.sync.pack_io import base_files

from agent_helpers import FIX, build_pack

URL = "http://testserver"


def cfg():
    return ProjectConfig(project="mini", registry=URL,
                         repos=[{"name": "mini-fe", "role": "frontend", "local_path": str(FIX / "mini-fe")},
                                {"name": "mini-be", "role": "backend", "url": "git@github.com:acme/mini-be.git"}])


@pytest.fixture
def local(tmp_path):
    store, _ = build_pack(tmp_path)
    return store


def test_base_files_lists_only_allowed(local):
    d = local.pack_dir("mini", "main")
    (d / "index.sqlite").write_bytes(b"x")
    files = base_files(d)
    assert set(files) == {"base/graph/mini-fe.json", "base/graph/mini-be.json", "base/links.json",
                          "base/modules.json", "base/system.md"}


def test_push_creates_version_and_updates_local_manifest(local, client_for_user):
    out = push_pack(cfg(), "main", local, client_for_user("maint@x"))
    assert out["version"] == 1 and out["uploaded"] == out["files"] == 5
    m = local.load("mini", "main").manifest
    assert m.version == 1 and m.parent is None
    lt = client_for_user("view@x").latest("mini", "main")
    assert lt["version"] == 1 and set(lt["files"]) == set(base_files(local.pack_dir("mini", "main")))


def test_second_push_same_commits_says_pull(local, client_for_user):
    push_pack(cfg(), "main", local, client_for_user("maint@x"))
    with pytest.raises(KbError, match=r"already on the registry \(version 1\) — run kb pull instead"):
        push_pack(cfg(), "main", local, client_for_user("maint@x"))


def test_push_incomplete_pack_refused(local, client_for_user):
    local.update_manifest("mini", "main", status="incomplete")
    with pytest.raises(KbError, match="kb analyze --resume"):
        push_pack(cfg(), "main", local, client_for_user("maint@x"))


def test_repush_after_partial_upload_sends_only_missing(local, client_for_user):
    c = client_for_user("maint@x")
    files = base_files(local.pack_dir("mini", "main"))
    pre = sorted(files)[:2]
    for p in pre:
        c.put_blob(hashlib.sha256(files[p]).hexdigest(), files[p])
    sent = []
    orig = c.put_blob
    c.put_blob = lambda h, raw: (sent.append(h), orig(h, raw))
    out = push_pack(cfg(), "main", local, c)
    assert out["version"] == 1 and len(sent) == 3 and out["uploaded"] == 3


def test_push_as_viewer_explains_role(local, tmp_path, monkeypatch, as_user):
    y = tmp_path / "kb.yaml"
    y.write_text(yaml.safe_dump(cfg().model_dump(mode="json", by_alias=True, exclude_none=True)))
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    as_user("view@x")
    r = CliRunner().invoke(app, ["push", str(y), "--branch", "main"])
    assert r.exit_code == 1
    assert "your role on mini is viewer; this needs maintainer" in r.output and "Traceback" not in r.output


def test_cli_push_prints_summary(local, tmp_path, monkeypatch, as_user):
    y = tmp_path / "kb.yaml"
    y.write_text(yaml.safe_dump(cfg().model_dump(mode="json", by_alias=True, exclude_none=True)))
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    as_user("maint@x")
    r = CliRunner().invoke(app, ["push", str(y), "--branch", "main"])
    assert r.exit_code == 0, r.output
    assert "pushed mini@main as version 1 (5 files, 5 uploaded)" in r.stdout


def test_first_push_seeds_registry_config_without_local_paths(local, client_for_user):
    push_pack(cfg(), "main", local, client_for_user("maint@x"))
    text = client_for_user("view@x").project("mini")["config_yaml"]
    data = yaml.safe_load(text)
    assert text.startswith("# repos without url: add local_path in your own copy")
    assert data["project"] == "mini" and data["registry"] == URL
    assert all("local_path" not in r for r in data["repos"])
    assert {"name": "mini-be", "role": "backend", "url": "git@github.com:acme/mini-be.git"} in data["repos"]
