import pytest

from agent_helpers import FIX


def setup(serve):
    c, settings, store, provider, cfg = serve
    c.post("/api/projects", json={"config_path": str(cfg)})
    return c


def test_resolve_citation(serve):
    c = setup(serve)
    r = c.get("/api/projects/mini/branches/main/citation", params={"repo": "mini-be", "file": "src/queue/user.ts", "line": 5})
    assert r.status_code == 200
    assert r.json() == {"path": str((FIX / "mini-be" / "src/queue/user.ts").resolve()), "line": 5}


@pytest.mark.parametrize("repo,file", [("mini-be", "../../etc/passwd"), ("mini-be", "/etc/passwd"),
                                       ("nope", "src/queue/user.ts"), ("mini-be", "src/missing.ts"),
                                       ("mini-be", "src/../../mini-fe/src/App.tsx")])
def test_resolve_citation_refuses_escape(serve, repo, file):
    c = setup(serve)
    r = c.get("/api/projects/mini/branches/main/citation", params={"repo": repo, "file": file, "line": 1})
    assert r.status_code == 404
