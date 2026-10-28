"""Final-review fixes on the registry side."""
import asyncio
import hashlib
import time

import httpx
import jwt
import pytest
import zstandard
from typer.testing import CliRunner

from kb_registry.paths import is_safe_id

from helpers import seeded
from test_api_bases import push
from test_api_overlay import pub, qa
from test_oidc import AUD, ISS, KEY, FakeJWK


@pytest.fixture
def reg(tmp_path):
    r = seeded(tmp_path)
    assert push(r, {"a": "1"}).status_code == 201
    return r


# C1 ---------------------------------------------------------------
@pytest.mark.parametrize("i,ok", [("q1", True), ("4f53720a38de", True), ("note:tasks:abc123", True), ("module-tasks", True),
                                  ("../../x", False), ("/abs", False), (".hidden", False), ("a/b", False), ("", False),
                                  ("x" * 201, False), ("a\\b", False)])
def test_is_safe_id(i, ok):
    assert is_safe_id(i) is ok


def test_publish_rejects_unsafe_ids(reg):
    bad = qa(id="../../../credentials")
    assert pub(reg, [bad]).status_code == 400
    import hashlib as h

    text = "x"
    note = {"id": f"note:../x:{h.sha256(text.encode()).hexdigest()[:12]}", "kind": "module_note",
            "payload": {"module_id": "../x", "text": text}}
    assert pub(reg, [note]).status_code == 400


# C2 ---------------------------------------------------------------
@pytest.fixture
def oreg(tmp_path, monkeypatch):
    r = seeded(tmp_path, oidc_issuer=ISS, oidc_audience=AUD, oidc_client_id="kb-cli")
    monkeypatch.setattr("kb_registry.auth.signing_key", lambda app, token: FakeJWK.key)
    return r


def me(reg, **claims):
    base = {"iss": ISS, "aud": AUD, "exp": int(time.time()) + 300}
    c = reg.client()
    c.headers["Authorization"] = "Bearer " + jwt.encode(base | claims, KEY, algorithm="RS256")
    return c.get("/api/v1/me")


def test_unverified_email_cannot_take_over_existing_user(oreg):
    r = me(oreg, sub="attacker", email="admin@x", email_verified=False)
    assert r.status_code == 401
    r2 = me(oreg, sub="attacker", email="admin@x")  # claim missing: also unverified
    assert r2.status_code == 401


def test_verified_email_links_unlinked_user_case_insensitively(oreg):
    r = me(oreg, sub="real-admin", email="Admin@X", email_verified=True)
    assert r.status_code == 200 and r.json()["is_admin"] is True


def test_already_linked_user_is_not_relinked(oreg):
    assert me(oreg, sub="first", email="view@x", email_verified=True).status_code == 200
    assert me(oreg, sub="second", email="view@x", email_verified=True).status_code == 401
    assert me(oreg, sub="first", email="view@x", email_verified=True).status_code == 200


# I4 ---------------------------------------------------------------
def test_oversized_json_rejected_before_auth(reg):
    c = reg.client()  # no token
    r = c.post("/api/v1/projects/mini/branches/main/overlay", content=b"x" * (9 * 2**20),
               headers={"Content-Type": "application/json"})
    assert r.status_code == 413


def test_oversized_chunked_body_rejected(reg):
    def gen():
        for _ in range(10):
            yield b"x" * 2**20
    r = reg.client("contrib@x").post("/api/v1/projects/mini/branches/main/overlay", content=gen(),
                                     headers={"Content-Type": "application/json"})
    assert r.status_code == 413


# I5 ---------------------------------------------------------------
def test_blob_upload_does_not_block_other_requests(reg):
    store = reg.app.state.store
    orig = store.put

    def slow_put(h, data):
        time.sleep(1.0)
        orig(h, data)
    store.put = slow_put
    data = b"slow"
    h = hashlib.sha256(data).hexdigest()

    async def run():
        t = httpx.ASGITransport(app=reg.app)
        async with httpx.AsyncClient(transport=t, base_url="http://t") as c:
            hdr = {"Authorization": f"Bearer {reg.tokens['maint@x']}", "Content-Type": "application/zstd"}
            t0 = time.monotonic()
            up = asyncio.create_task(c.put(f"/api/v1/blobs/{h}", content=zstandard.ZstdCompressor().compress(data), headers=hdr))
            await asyncio.sleep(0.1)
            r = await c.get("/api/v1/auth/config")
            quick = time.monotonic() - t0  # from before the upload started; the upload takes ≥ 1 s
            await up
            return r.status_code, quick
    status, quick = asyncio.run(run())
    assert status == 200 and quick < 0.5


# I6 ---------------------------------------------------------------
def test_admin_can_revoke_access(reg, monkeypatch):
    from kb_registry.cli import app as cli

    monkeypatch.setenv("KB_REGISTRY_DATABASE_URL", reg.settings.database_url)
    run = lambda *a: CliRunner().invoke(cli, ["admin", *a])
    assert run("revoke", "view@x", "mini").exit_code == 0
    assert reg.client("view@x").get("/api/v1/projects/mini").status_code == 404
    assert run("revoke-tokens", "contrib@x").exit_code == 0
    assert reg.client("contrib@x").get("/api/v1/me").status_code == 401
    assert run("disable-user", "maint@x").exit_code == 0
    assert reg.client("maint@x").get("/api/v1/me").status_code == 401


def test_disabled_user_cannot_use_oidc(oreg, monkeypatch):
    from kb_registry.cli import app as cli

    assert me(oreg, sub="s-v", email="view@x", email_verified=True).status_code == 200
    monkeypatch.setenv("KB_REGISTRY_DATABASE_URL", oreg.settings.database_url)
    assert CliRunner().invoke(cli, ["admin", "disable-user", "view@x"]).exit_code == 0
    assert me(oreg, sub="s-v", email="view@x", email_verified=True).status_code == 401


# M3 (re-graded) ------------------------------------------------------
def test_issuer_without_audience_refuses_to_start(tmp_path):
    from kb_registry.api import create_app
    from kb_registry.settings import Settings

    with pytest.raises(ValueError, match="KB_REGISTRY_OIDC_AUDIENCE"):
        create_app(Settings(database_url=f"sqlite:///{tmp_path}/x.db", storage=f"file://{tmp_path}", oidc_issuer=ISS))


# M11 (re-graded) -----------------------------------------------------
def test_readme_covers_tls():
    from pathlib import Path

    text = (Path(__file__).parents[2] / "deploy" / "registry" / "README.md").read_text()
    assert "## TLS" in text and "reverse proxy" in text
