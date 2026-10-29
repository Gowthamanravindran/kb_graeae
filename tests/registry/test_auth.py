import pytest
from fastapi import Depends
from typer.testing import CliRunner

from kb_registry.auth import current_user, get_session, require_role

from helpers import seeded


def add_probe(app):
    @app.get("/api/v1/_probe/{project}/{role}")
    def probe(project: str, role: str, user=Depends(current_user), s=Depends(get_session)):
        p = require_role(s, user, project, role)
        return {"email": user.email, "project": p.name}


@pytest.fixture
def reg(tmp_path):
    r = seeded(tmp_path)
    add_probe(r.app)
    return r


def test_missing_or_bad_token_is_401(reg):
    assert reg.client().get("/api/v1/_probe/mini/viewer").status_code == 401
    c = reg.client()
    c.headers["Authorization"] = "Bearer kbr_nope"
    r = c.get("/api/v1/_probe/mini/viewer")
    assert r.status_code == 401 and "kb login" in r.json()["detail"]


def test_require_role_ladder(reg):
    c = reg.client("view@x")
    assert c.get("/api/v1/_probe/mini/viewer").json()["project"] == "mini"
    r = c.get("/api/v1/_probe/mini/contributor")
    assert r.status_code == 403
    assert r.json()["detail"] == "your role on mini is viewer; this needs contributor"
    assert reg.client("maint@x").get("/api/v1/_probe/mini/contributor").status_code == 200


def test_non_member_gets_404(reg):
    r = reg.client("stranger@x").get("/api/v1/_probe/mini/viewer")
    assert r.status_code == 404 and r.json()["detail"] == "project not found: mini"
    assert reg.client("maint@x").get("/api/v1/_probe/nope/viewer").status_code == 404


def test_admin_passes_everything(reg):
    assert reg.client("admin@x").get("/api/v1/_probe/mini/maintainer").status_code == 200


def test_admin_cli_create_user_prints_working_token(reg, monkeypatch):
    from kb_registry.cli import app as cli

    monkeypatch.setenv("KB_REGISTRY_DATABASE_URL", reg.settings.database_url)
    r = CliRunner().invoke(cli, ["admin", "create-user", "new@x", "--name", "New"])
    assert r.exit_code == 0, r.output
    token = r.output.strip().splitlines()[-1]
    assert token.startswith("kbr_")
    assert CliRunner().invoke(cli, ["admin", "grant", "new@x", "mini", "viewer"]).exit_code == 0
    c = reg.client()
    c.headers["Authorization"] = f"Bearer {token}"
    assert c.get("/api/v1/_probe/mini/viewer").status_code == 200
    bad = CliRunner().invoke(cli, ["admin", "grant", "new@x", "mini", "owner"])
    assert bad.exit_code != 0


def test_admin_cli_create_project_and_token(reg, monkeypatch, tmp_path):
    from kb_registry.cli import app as cli

    monkeypatch.setenv("KB_REGISTRY_DATABASE_URL", reg.settings.database_url)
    y = tmp_path / "kb.yaml"
    y.write_text("project: sop\nrepos: []\n")
    assert CliRunner().invoke(cli, ["admin", "create-project", "sop", "--config", str(y)]).exit_code == 0
    r = CliRunner().invoke(cli, ["admin", "token", "maint@x", "--name", "laptop"])
    assert r.exit_code == 0 and r.output.strip().splitlines()[-1].startswith("kbr_")
    dup = CliRunner().invoke(cli, ["admin", "create-project", "sop"])
    assert dup.exit_code != 0 and "exists" in dup.output
