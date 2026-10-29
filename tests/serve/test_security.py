import os
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from fakes import FakeProvider
from serve_helpers import TOKEN, write_cfg


def test_requests_need_token_and_local_host(serve):
    c, settings, *_ = serve
    from kb.serve.app import create_app

    app = c.app
    raw = TestClient(app, base_url="http://127.0.0.1:5555")
    assert raw.get("/api/status").status_code == 401
    assert raw.get("/api/status", headers={"X-KB-Token": "nope"}).status_code == 401
    evil = TestClient(app, base_url="http://evil.com")
    assert evil.get("/api/status", headers={"X-KB-Token": TOKEN}).status_code == 421
    r = raw.get("/api/status", headers={"X-KB-Token": TOKEN, "Origin": "http://evil.com"})
    assert r.status_code == 200 and "access-control-allow-origin" not in r.headers
    assert TestClient(app, base_url="http://localhost:9").get("/api/status", headers={"X-KB-Token": TOKEN}).status_code == 200


def test_status_reports_claude(tmp_path):
    from kb.serve.app import ServeSettings, create_app

    p = FakeProvider(unavailable="not signed in")
    app = create_app(ServeSettings(kb_home=tmp_path, token=TOKEN, provider_factory=lambda: p))
    d = TestClient(app, base_url="http://127.0.0.1:1").get("/api/status", headers={"X-KB-Token": TOKEN}).json()
    assert d["claude"] == {"available": False, "reason": "not signed in"}
    assert d["version"] == "0.1.0" and "configured" in d["langfuse"]


def test_add_project_and_list(serve, tmp_path):
    c, _, _, _, cfg = serve
    r = c.post("/api/projects", json={"config_path": str(cfg)})
    assert r.status_code == 200 and r.json()["name"] == "mini"
    assert c.post("/api/projects", json={"config_path": str(cfg)}).status_code == 200  # idempotent
    rows = c.get("/api/projects").json()
    assert [p["name"] for p in rows] == ["mini"] and rows[0]["config_path"] == str(cfg)
    bad = tmp_path / "bad.yaml"
    bad.write_text("project: [oops")
    r = c.post("/api/projects", json={"config_path": str(bad)})
    assert r.status_code == 400 and "cannot read" in r.json()["detail"]
    assert c.delete("/api/projects/mini").status_code == 200 and c.get("/api/projects").json() == []


def test_registry_status_without_registry(serve):
    c, _, _, _, cfg = serve
    c.post("/api/projects", json={"config_path": str(cfg)})
    assert c.get("/api/projects/mini/registry").json() == {"url": None, "logged_in": False, "email": None,
                                                           "reachable": False, "sso": False}


def test_serve_cli_prints_ready_line(tmp_path):
    env = {**os.environ, "KB_SERVE_TOKEN": TOKEN, "KB_HOME": str(tmp_path)}
    p = subprocess.Popen([sys.executable, "-m", "kb.cli", "serve", "--port", "0"], env=env, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True)
    try:
        line = p.stdout.readline().strip()
        assert line.startswith("KB_SERVE_READY ")
        import json

        import httpx

        port = json.loads(line.split(" ", 1)[1])["port"]
        r = httpx.get(f"http://127.0.0.1:{port}/api/status", headers={"X-KB-Token": TOKEN}, timeout=10)
        assert r.status_code == 200
    finally:
        p.terminate()
        p.wait(10)


def test_serve_cli_requires_token(tmp_path):
    env = {k: v for k, v in os.environ.items() if k != "KB_SERVE_TOKEN"}
    r = subprocess.run([sys.executable, "-m", "kb.cli", "serve"], env=env, capture_output=True, text=True, timeout=30)
    assert r.returncode == 2 and "KB_SERVE_TOKEN not set" in r.stderr


def test_ui_dir_served_same_origin_behind_token(tmp_path):
    from kb.serve.app import ServeSettings, create_app

    ui = tmp_path / "ui"
    ui.mkdir()
    (ui / "index.html").write_text("<h1>KB Desktop</h1>")
    app = create_app(ServeSettings(kb_home=tmp_path, token=TOKEN, provider_factory=FakeProvider, ui_dir=ui))
    c = TestClient(app, base_url="http://127.0.0.1:1")
    assert c.get("/").status_code == 401  # pages need the token too (Electron injects it)
    r = c.get("/", headers={"X-KB-Token": TOKEN})
    assert r.status_code == 200 and "KB Desktop" in r.text
    assert c.get("/api/status", headers={"X-KB-Token": TOKEN}).status_code == 200  # API still wins over static


def test_status_reports_langfuse_host(tmp_path, monkeypatch):
    from kb.serve.app import ServeSettings, create_app

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setenv("LANGFUSE_HOST", "http://localhost:3000/")
    app = create_app(ServeSettings(kb_home=tmp_path, token=TOKEN, provider_factory=lambda: FakeProvider()))
    d = TestClient(app, base_url="http://127.0.0.1:1").get("/api/status", headers={"X-KB-Token": TOKEN}).json()
    assert d["langfuse"] == {"configured": True, "host": "http://localhost:3000"}


def test_serve_exits_when_parent_closes_stdin(tmp_path):
    env = {**os.environ, "KB_SERVE_TOKEN": TOKEN, "KB_HOME": str(tmp_path)}
    p = subprocess.Popen([sys.executable, "-m", "kb.cli", "serve", "--port", "0", "--exit-on-stdin-eof"], env=env,
                         stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        assert p.stdout.readline().startswith("KB_SERVE_READY ")
        p.stdin.close()  # what the OS does when Electron dies, however it dies
        assert p.wait(15) == 0
    finally:
        if p.poll() is None:
            p.kill()


def test_status_fresh_bypasses_cache(tmp_path):
    from kb.errors import LLMUnavailable
    from kb.serve.app import ServeSettings, create_app

    p = FakeProvider(unavailable="not signed in")
    app = create_app(ServeSettings(kb_home=tmp_path, token=TOKEN, provider_factory=lambda: p))
    c = TestClient(app, base_url="http://127.0.0.1:1")
    c.headers["X-KB-Token"] = TOKEN
    assert c.get("/api/status").json()["claude"]["available"] is False
    p.unavailable = None
    assert c.get("/api/status").json()["claude"]["available"] is False  # cached
    assert c.get("/api/status?fresh=1").json()["claude"]["available"] is True
