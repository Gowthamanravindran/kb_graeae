import json
import subprocess

from serve_helpers import wait_job


def _repo(d, deps, branch="main"):
    d.mkdir(parents=True)
    (d / "package.json").write_text(json.dumps({"dependencies": deps}))
    for a in (["init", "-q", "-b", branch], ["add", "-A"],
              ["-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "i"]):
        subprocess.run(["git", *a], cwd=d, check=True, capture_output=True)
    return d


def test_inspect_then_create_project(tmp_path, serve):
    c, settings, *_ = serve
    fe = _repo(tmp_path / "web-app", {"react": "18", "@apollo/client": "3"}, "master")
    be = _repo(tmp_path / "api-main", {"apollo-server": "3"}, "develop")
    r = c.post("/api/projects/inspect", json={"sources": [str(fe), str(be)]})
    j = wait_job(c, r.json()["job_id"])
    assert j["state"] == "done", j["error"]
    got = j["result"]
    assert [x["name"] for x in got["repos"]] == ["web-app", "api"]
    assert got["linkers"] == [{"type": "graphql", "from": "web-app", "to": "api", "options": {"backend_style": "sdl"}}]
    body = {"project": "shop",
            "repos": [{"name": x["name"], "role": x["role"], "source": x["source"], "path": x["path"],
                       "default_branch": x["default_branch"]} for x in got["repos"]],
            "linkers": got["linkers"], "context_docs": []}
    made = c.post("/api/projects/create", json=body)
    assert made.status_code == 200, made.text
    row = made.json()
    assert row["name"] == "shop" and row["config_path"] == str(settings.kb_home / "projects" / "shop" / "kb.yaml")
    assert [p["name"] for p in c.get("/api/projects").json()] == ["shop"]
    assert [x["default_branch"] for x in row["repos"]] == ["master", "develop"]
    again = c.post("/api/projects/create", json=body)
    assert again.status_code == 400 and "already exists" in again.json()["detail"]


def test_inspect_bad_folder_fails_the_job(tmp_path, serve):
    c, *_ = serve
    j = wait_job(c, c.post("/api/projects/inspect", json={"sources": [str(tmp_path / "nope")]}).json()["job_id"])
    assert j["state"] == "failed" and "no folder" in j["error"]
