import hashlib
from datetime import datetime, timezone

import pytest
import zstandard

from kb.packs.model import Manifest, commits_key

from helpers import seeded

ZH = {"Content-Type": "application/zstd"}


def upload(c, data: bytes) -> str:
    h = hashlib.sha256(data).hexdigest()
    c.put(f"/api/v1/blobs/{h}", content=zstandard.ZstdCompressor().compress(data), headers=ZH)
    return h


def manifest(commits, branch="main", project="mini"):
    return Manifest(project=project, branch=branch, commits=commits, author="m", status="complete",
                    created_at=datetime(2026, 10, 28, tzinfo=timezone.utc)).model_dump(mode="json")


@pytest.fixture
def reg(tmp_path):
    return seeded(tmp_path)


def push(reg, commits, files=None, email="maint@x", branch="main", m=None):
    c = reg.client(email)
    files = files if files is not None else {"base/links.json": upload(reg.client("maint@x"), b"{}"),
                                             "base/system.md": upload(reg.client("maint@x"), b"# hi")}
    return c.post(f"/api/v1/projects/mini/branches/{branch}/bases", json={"manifest": m or manifest(commits, branch), "files": files})


def test_first_push_is_version_1_then_2_with_parent(reg):
    r1 = push(reg, {"a": "1"})
    assert r1.status_code == 201 and r1.json()["version"] == 1 and r1.json()["parent"] is None
    r2 = push(reg, {"a": "2"})
    assert r2.json()["version"] == 2 and r2.json()["parent"] == 1
    vs = reg.client("view@x").get("/api/v1/projects/mini/branches/main/versions").json()
    assert [v["version"] for v in vs] == [2, 1]


def test_branch_with_slash_and_commit_key_matches_client(reg):
    r = push(reg, {"a": "1"}, branch="feature/x")
    assert r.status_code == 201
    from kb_registry.models import BaseRow

    with reg.S() as s:
        assert s.query(BaseRow).one().commits_key == commits_key({"a": "1"})


def test_duplicate_commit_set_409_mentions_pull(reg):
    push(reg, {"a": "1"})
    r = push(reg, {"a": "1"})
    assert r.status_code == 409
    assert r.json()["detail"] == "already exists — pull instead" and r.json()["version"] == 1


def test_unknown_blob_400(reg):
    r = push(reg, {"a": "1"}, files={"base/links.json": "b" * 64})
    assert r.status_code == 400 and "b" * 64 in r.json()["detail"]


def test_disallowed_path_400(reg):
    h = upload(reg.client("maint@x"), b"{}")
    for bad in ("../x.json", "base/index.sqlite", "manifest.json"):
        r = push(reg, {"a": "1"}, files={bad: h})
        assert r.status_code == 400, bad


def test_contributor_cannot_push_403(reg):
    assert push(reg, {"a": "1"}, email="contrib@x").status_code == 403


def test_manifest_project_mismatch_400(reg):
    assert push(reg, {"a": "1"}, m=manifest({"a": "1"}, project="other")).status_code == 400
    assert push(reg, {"a": "1"}, m=manifest({"a": "1"}, branch="dev")).status_code == 400
    assert push(reg, {"a": "1"}, m={"nope": 1}).status_code == 400
