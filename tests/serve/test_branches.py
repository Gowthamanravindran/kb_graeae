import json
import shutil

import pytest
from fastapi.testclient import TestClient

from kb.config import load_config
from kb.packs.store import PackStore
from kb.sync.client import RegistryClient
from kb.sync.commands import push_pack

from agent_helpers import build_pack
from serve_helpers import registry_serve, wait_job, write_cfg


def states(c):
    return {r["branch"]: r["state"] for r in c.get("/api/projects/mini/branches").json()["rows"]}


def test_branch_rows_states(tmp_path, serve):
    c, settings, store, _, _ = serve
    reg, cfg_path = registry_serve(tmp_path, serve)
    cfg = load_config(cfg_path)
    assert states(c) == {"main": "local-only"}
    rc = RegistryClient("http://testserver", reg.tokens["maint@x"], http=TestClient(reg.app))
    push_pack(cfg, "main", store, rc)
    assert states(c) == {"main": "synced"}
    other, _ = build_pack(tmp_path / "other")
    other.update_manifest("mini", "main", commits={"mini-fe": "v2", "mini-be": "v2"})
    push_pack(cfg, "main", other, rc)
    dev, _ = build_pack(tmp_path / "dev")
    shutil.move(str(dev.pack_dir("mini", "main")), str(dev.pack_dir("mini", "dev")))
    mf = dev.pack_dir("mini", "dev") / "manifest.json"
    mf.write_text(json.dumps({**json.loads(mf.read_text()), "branch": "dev"}))
    push_pack(cfg, "dev", dev, rc)
    rows = {r["branch"]: r for r in c.get("/api/projects/mini/branches").json()["rows"]}
    assert rows["main"]["state"] == "update-available" and rows["main"]["remote"]["latest_version"] == 2
    assert rows["dev"]["state"] == "not-pulled" and rows["dev"]["local"] is None
    assert rows["main"]["local"]["modules"] == 2 and rows["main"]["local"]["qa_total"] == 0


def test_branches_survive_unreachable_registry(tmp_path, serve):
    c, settings, *_ = serve
    cfg = write_cfg(tmp_path, registry="http://127.0.0.1:9")
    c.post("/api/projects", json={"config_path": str(cfg)})
    d = c.get("/api/projects/mini/branches").json()
    assert [r["branch"] for r in d["rows"]] == ["main"] and "unreachable" in d["registry_error"]


@pytest.mark.skipif(shutil.which("graphify") is None, reason="graphify not installed")
def test_analyze_job_reports_module_progress(tmp_path, serve):
    c, settings, _, provider, cfg = serve
    from agent_helpers import FIX

    for r in ("mini-fe", "mini-be"):
        shutil.copytree(FIX / r, tmp_path / "src" / r)
    y = tmp_path / "copy.kb.yaml"
    y.write_text(cfg.read_text().replace(str(FIX), str(tmp_path / "src")).replace("project: mini", "project: mini2"))
    c.post("/api/projects", json={"config_path": str(y)})
    provider.default = "```json\n" + json.dumps({"name": "Tasks", "summary": "S.", "entry_points": []}) + "\n```"
    steps = []
    job = c.post("/api/projects/mini2/branches/main/analyze", json={"resume": False}).json()["job_id"]
    import time

    end = time.time() + 60
    while time.time() < end:
        j = c.get(f"/api/jobs/{job}").json()
        steps.append(j["step"])
        if j["state"] in ("done", "failed"):
            break
        time.sleep(0.01)
    assert j["state"] == "done", j["error"]
    assert j["result"]["status"] == "complete"
    assert any(s.startswith("Naming modules (") for s in steps) or j["step"].startswith("Writing")
    second = c.post("/api/projects/mini2/branches/main/analyze", json={"resume": False})
    assert second.status_code in (200, 409)


def test_analyze_accepts_branch_map(tmp_path, serve):
    import subprocess
    import time

    c, settings, store, provider, cfg = serve
    from agent_helpers import FIX

    for r, br in (("mini-fe", "master"), ("mini-be", "develop")):
        d = tmp_path / "src" / r
        shutil.copytree(FIX / r, d)
        for args in (["init", "-q", "-b", br], ["config", "user.email", "t@t"], ["config", "user.name", "t"],
                     ["add", "."], ["commit", "-qm", "init"]):
            subprocess.run(["git", *args], cwd=d, check=True, capture_output=True)
    y = tmp_path / "copy.kb.yaml"
    text = cfg.read_text().replace(str(FIX), str(tmp_path / "src")).replace("project: mini", "project: mini3")
    y.write_text(text.replace("role: frontend,", "role: frontend, default_branch: master,"))
    c.post("/api/projects", json={"config_path": str(y)})
    assert [r.get("default_branch") for r in c.get("/api/projects").json()[-1]["repos"]] == ["master", None]
    provider.default = "```json\n" + json.dumps({"name": "Tasks", "summary": "S.", "entry_points": []}) + "\n```"
    job = c.post("/api/projects/mini3/branches/production/analyze",
                 json={"resume": False, "branches": {"mini-fe": "master", "mini-be": "develop"}}).json()["job_id"]
    end = time.time() + 60
    while time.time() < end and c.get(f"/api/jobs/{job}").json()["state"] not in ("done", "failed"):
        time.sleep(0.05)
    assert c.get(f"/api/jobs/{job}").json()["state"] == "done", c.get(f"/api/jobs/{job}").json()["error"]
    row = next(r for r in c.get("/api/projects/mini3/branches").json()["rows"] if r["branch"] == "production")
    assert row["local"]["branches"] == {"mini-fe": "master", "mini-be": "develop"}
