import pytest

from kb_registry.models import BaseRow, OverlayItem, Project, User

from helpers import seeded

GOOD = "project: mini\nrepos:\n  - {name: a, role: backend, url: 'git@x:a.git'}\n"


@pytest.fixture
def reg(tmp_path):
    return seeded(tmp_path)


def add_base(reg, branch, version, key, commits=None):
    with reg.S() as s:
        p = s.query(Project).filter_by(name="mini").one()
        u = s.query(User).filter_by(email="maint@x").one()
        b = BaseRow(project_id=p.id, branch=branch, commits_key=key, commits=commits or {"a": key}, manifest={},
                    version=version, parent=version - 1 if version > 1 else None, created_by=u.id)
        s.add(b)
        s.commit()
        return b.id


def test_auth_config_without_oidc(reg):
    assert reg.client().get("/api/v1/auth/config").json() == {"oidc": None}


def test_me(reg):
    d = reg.client("contrib@x").get("/api/v1/me").json()
    assert d == {"email": "contrib@x", "is_admin": False, "projects": {"mini": "contributor"}}


def test_projects_lists_only_memberships(reg):
    reg.project("secret")
    assert reg.client("view@x").get("/api/v1/projects").json() == [{"name": "mini", "role": "viewer"}]
    assert reg.client("stranger@x").get("/api/v1/projects").json() == []
    names = [p["name"] for p in reg.client("admin@x").get("/api/v1/projects").json()]
    assert names == ["mini", "secret"]


def test_put_config_validates_and_requires_maintainer(reg):
    c = reg.client("maint@x")
    assert c.put("/api/v1/projects/mini/config", json={"config_yaml": GOOD}).status_code == 200
    assert reg.client("view@x").get("/api/v1/projects/mini").json() == {"name": "mini", "config_yaml": GOOD}
    bad = c.put("/api/v1/projects/mini/config", json={"config_yaml": "project: mini\nrepos: [{name: a}]\n"})
    assert bad.status_code == 400 and "role" in bad.json()["detail"]
    other = c.put("/api/v1/projects/mini/config", json={"config_yaml": GOOD.replace("mini", "other")})
    assert other.status_code == 400 and "project" in other.json()["detail"]
    assert reg.client("contrib@x").put("/api/v1/projects/mini/config", json={"config_yaml": GOOD}).status_code == 403


def test_branches_and_versions_shapes(reg):
    add_base(reg, "main", 1, "k1")
    bid = add_base(reg, "main", 2, "k2")
    add_base(reg, "dev", 1, "k3")
    with reg.S() as s:
        s.add(OverlayItem(project_id=1, branch="main", item_id="q1", kind="qa", payload={}, payload_sha="x",
                          author_id=1, base_id=bid))
        s.commit()
    br = reg.client("view@x").get("/api/v1/projects/mini/branches").json()
    assert [(b["branch"], b["latest_version"], b["overlay_count"]) for b in br] == [("dev", 1, 0), ("main", 2, 1)]
    assert br[1]["commits"] == {"a": "k2"} and br[1]["created_by"] == "maint@x"
    vs = reg.client("view@x").get("/api/v1/projects/mini/branches/main/versions").json()
    assert [(v["version"], v["parent"]) for v in vs] == [(2, 1), (1, None)]


def test_branch_with_slash(reg):
    add_base(reg, "feature/x", 1, "k1")
    vs = reg.client("view@x").get("/api/v1/projects/mini/branches/feature/x/versions").json()
    assert [v["version"] for v in vs] == [1]
    assert reg.client("view@x").get("/api/v1/projects/mini/branches/nope/versions").status_code == 404
