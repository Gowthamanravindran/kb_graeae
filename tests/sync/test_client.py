import hashlib
import os
import stat

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from kb.cli import app
from kb.errors import RegistryError, RegistryUnavailable
from kb.sync.client import RegistryClient
from kb.sync.credentials import get_token, load_credentials, save_token

URL = "http://testserver"


def test_login_saves_token_with_0600(tmp_path, monkeypatch, registry):
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("kb.sync.client.make_http", lambda url: TestClient(registry.app))
    r = CliRunner().invoke(app, ["login", URL + "/", "--token", registry.tokens["view@x"]])
    assert r.exit_code == 0, r.output
    assert "logged in to http://testserver as view@x" in r.stdout
    f = tmp_path / "home" / "credentials.json"
    assert stat.S_IMODE(os.stat(f).st_mode) == 0o600
    assert get_token(tmp_path / "home", URL)["token"] == registry.tokens["view@x"]


def test_login_with_bad_token_fails_and_saves_nothing(tmp_path, monkeypatch, registry):
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("kb.sync.client.make_http", lambda url: TestClient(registry.app))
    r = CliRunner().invoke(app, ["login", URL, "--token", "kbr_bad"])
    assert r.exit_code == 1 and "kb login" in r.output
    assert load_credentials(tmp_path / "home") == {}


def test_unreachable_raises_registry_unavailable():
    with pytest.raises(RegistryUnavailable, match="registry unreachable: http://127.0.0.1:9"):
        RegistryClient("http://127.0.0.1:9", "t").me()


def test_401_message_says_kb_login(registry):
    c = RegistryClient(URL, "kbr_nope", http=TestClient(registry.app))
    with pytest.raises(RegistryError) as e:
        c.me()
    assert e.value.status == 401 and "kb login http://testserver" in str(e.value)


def test_403_detail_passes_through(client_for_user):
    with pytest.raises(RegistryError) as e:
        client_for_user("view@x").put_config("mini", "project: mini\nrepos: []\n")
    assert e.value.status == 403 and str(e.value) == "your role on mini is viewer; this needs maintainer"


def test_blob_roundtrip_through_client(client_for_user, registry):
    from kb_registry.models import BaseFile, BaseRow

    c = client_for_user("maint@x")
    data = b'{"x": 1}'
    h = hashlib.sha256(data).hexdigest()
    assert c.missing_blobs([h]) == [h]
    c.put_blob(h, data)
    assert c.missing_blobs([h]) == []
    with registry.S() as s:
        b = BaseRow(project_id=1, branch="main", commits_key="k", commits={}, manifest={}, version=1, created_by=1)
        s.add(b)
        s.flush()
        s.add(BaseFile(base_id=b.id, path="base/links.json", blob_hash=h))
        s.commit()
    assert c.get_blob(h) == data


def test_save_token_keeps_other_registries(tmp_path):
    save_token(tmp_path, "https://a", "t1", "static")
    save_token(tmp_path, "https://b/", "t2", "oidc", refresh_token="r", expires_at=1.0)
    creds = load_credentials(tmp_path)
    assert creds["https://a"]["token"] == "t1" and creds["https://b"]["refresh_token"] == "r"


def test_cli_list_shows_projects(tmp_path, monkeypatch, as_user):
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    as_user("contrib@x")
    r = CliRunner().invoke(app, ["list", "--registry", URL])
    assert r.exit_code == 0, r.output
    assert "mini" in r.stdout and "contributor" in r.stdout


def test_cli_list_needs_registry(tmp_path, monkeypatch):
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    r = CliRunner().invoke(app, ["list"])
    assert r.exit_code == 1 and "registry" in r.output
