import hashlib

import pytest
import zstandard

from kb_registry.models import BaseFile, BaseRow, Blob

from helpers import seeded

DATA = b'{"nodes": []}'
H = hashlib.sha256(DATA).hexdigest()
Z = zstandard.ZstdCompressor().compress(DATA)
ZH = {"Content-Type": "application/zstd"}


@pytest.fixture
def reg(tmp_path):
    return seeded(tmp_path)


def test_missing_then_put_then_not_missing(reg):
    c = reg.client("maint@x")
    assert c.post("/api/v1/blobs/missing", json={"hashes": [H]}).json() == {"missing": [H]}
    r = c.put(f"/api/v1/blobs/{H}", content=Z, headers=ZH)
    assert r.status_code == 201 and r.json() == {"hash": H, "size": len(DATA)}
    assert c.put(f"/api/v1/blobs/{H}", content=Z, headers=ZH).status_code == 200
    assert c.post("/api/v1/blobs/missing", json={"hashes": [H]}).json() == {"missing": []}


def test_put_rejects_hash_mismatch_and_stores_nothing(reg):
    c = reg.client("maint@x")
    other = "a" * 64
    r = c.put(f"/api/v1/blobs/{other}", content=Z, headers=ZH)
    assert r.status_code == 400 and "hash mismatch" in r.json()["detail"]
    assert not reg.app.state.store.exists(other)
    assert c.put("/api/v1/blobs/NOTAHASH", content=Z, headers=ZH).status_code == 400


def test_get_requires_view_access_to_a_referencing_base(reg):
    reg.client("maint@x").put(f"/api/v1/blobs/{H}", content=Z, headers=ZH)
    assert reg.client("view@x").get(f"/api/v1/blobs/{H}").status_code == 404  # not referenced yet
    with reg.S() as s:
        b = BaseRow(project_id=1, branch="main", commits_key="k", commits={}, manifest={}, version=1, created_by=1)
        s.add(b)
        s.flush()
        s.add(BaseFile(base_id=b.id, path="base/links.json", blob_hash=H))
        s.commit()
    r = reg.client("view@x").get(f"/api/v1/blobs/{H}")
    assert r.status_code == 200 and zstandard.ZstdDecompressor().decompress(r.content) == DATA
    assert reg.client("stranger@x").get(f"/api/v1/blobs/{H}").status_code == 404


def test_viewer_only_user_cannot_upload(reg):
    c = reg.client("view@x")
    assert c.post("/api/v1/blobs/missing", json={"hashes": [H]}).status_code == 403
    assert c.put(f"/api/v1/blobs/{H}", content=Z, headers=ZH).status_code == 403
