import os

import pytest
from fastapi.testclient import TestClient

from fakes import FakeProvider
from serve_helpers import TOKEN

KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "GITHUB_TOKEN")


@pytest.fixture
def client(tmp_path, monkeypatch):
    for k in KEYS:
        monkeypatch.delenv(k, raising=False)
    from kb import envfile

    monkeypatch.setattr(envfile, "_applied", set())

    def make():
        from kb.serve.app import ServeSettings, create_app

        app = create_app(ServeSettings(kb_home=tmp_path, token=TOKEN, provider_factory=lambda: FakeProvider()))
        c = TestClient(app, base_url="http://127.0.0.1:1")
        c.headers["X-KB-Token"] = TOKEN
        return c
    yield make
    for k in KEYS:
        os.environ.pop(k, None)


def test_settings_masks_secrets(client):
    c = client()
    r = c.put("/api/settings", json={"values": {"LANGFUSE_SECRET_KEY": "sk-lf-abcd1234"}})
    assert r.status_code == 200
    v = c.get("/api/settings").json()["values"]["LANGFUSE_SECRET_KEY"]
    assert v == {"set": True, "last4": "1234", "source": "file"}


def test_settings_host_is_returned_in_full(client):
    c = client()
    c.put("/api/settings", json={"values": {"LANGFUSE_HOST": "http://localhost:3000"}})
    assert c.get("/api/settings").json()["values"]["LANGFUSE_HOST"]["value"] == "http://localhost:3000"


def test_settings_reports_environment_source(client, monkeypatch):
    monkeypatch.setenv("GITHUB_TOKEN", "from-shell")
    c = client()
    assert c.get("/api/settings").json()["values"]["GITHUB_TOKEN"]["source"] == "environment"
    c.put("/api/settings", json={"values": {"GITHUB_TOKEN": "ghp_new"}})
    assert os.environ["GITHUB_TOKEN"] == "from-shell"
    assert c.get("/api/settings").json()["values"]["GITHUB_TOKEN"]["source"] == "environment"


def test_settings_rejects_unknown_key(client):
    r = client().put("/api/settings", json={"values": {"PATH": "/x"}})
    assert r.status_code == 400 and r.json()["detail"] == "unknown setting PATH"


def test_settings_applies_langfuse_to_status(client):
    c = client()
    c.put("/api/settings", json={"values": {"LANGFUSE_PUBLIC_KEY": "pk", "LANGFUSE_SECRET_KEY": "sk",
                                            "LANGFUSE_HOST": "http://localhost:3000"}})
    assert c.get("/api/status").json()["langfuse"] == {"configured": True, "host": "http://localhost:3000"}


def test_settings_clear_value(client):
    c = client()
    c.put("/api/settings", json={"values": {"GITHUB_TOKEN": "ghp_x"}})
    c.put("/api/settings", json={"values": {"GITHUB_TOKEN": None}})
    assert c.get("/api/settings").json()["values"]["GITHUB_TOKEN"] == {"set": False, "last4": None, "source": None}
    assert "GITHUB_TOKEN" not in os.environ


def test_logout_forgets_token(client, tmp_path):
    from kb.sync.credentials import get_token, save_token

    save_token(tmp_path, "http://reg", "tok", "static")
    r = client().post("/api/logout", json={"registry": "http://reg/"})
    assert r.status_code == 200 and r.json() == {"forgotten": True}
    assert get_token(tmp_path, "http://reg") is None


def test_settings_rejects_newlines(client):
    c = client()
    r = c.put("/api/settings", json={"values": {"GITHUB_TOKEN": "x\nANTHROPIC_BASE_URL=http://evil"}})
    assert r.status_code == 400 and "one line" in r.json()["detail"]
    assert c.get("/api/settings").json()["values"]["GITHUB_TOKEN"]["set"] is False
