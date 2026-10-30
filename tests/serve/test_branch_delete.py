"""Delete an analysed branch, or analyse it again from scratch."""

import threading

from serve_helpers import wait_job


def _added(c, serve):
    _, _, store, _, cfg = serve
    c.post("/api/projects", json={"config_path": str(cfg)})
    return store


def test_delete_branch_removes_pack_and_work(serve):
    c, settings, store, *_ = serve
    _added(c, serve)
    work = settings.kb_home / "work" / "mini@main"
    work.mkdir(parents=True)
    j = wait_job(c, c.delete("/api/projects/mini/branches/main").json()["job_id"])
    assert j["state"] == "done", j["error"]
    assert not store.pack_dir("mini", "main").exists() and not work.exists()
    assert c.get("/api/projects/mini/branches").json()["rows"] == []


def test_delete_unknown_branch_is_404(serve):
    c, *_ = serve
    _added(c, serve)
    assert c.delete("/api/projects/mini/branches/nope").status_code == 404


def test_delete_refused_while_the_branch_is_busy(serve, monkeypatch):
    c, settings, store, provider, cfg = serve
    _added(c, serve)
    gate = threading.Event()
    import kb.analyze as an
    monkeypatch.setattr(an, "run_analyze", lambda *a, **k: gate.wait(10))
    first = c.post("/api/projects/mini/branches/main/analyze", json={"resume": False})
    second = c.delete("/api/projects/mini/branches/main")
    gate.set()
    assert first.status_code == 200 and second.status_code == 409
    wait_job(c, first.json()["job_id"])
    assert store.pack_dir("mini", "main").exists()


def test_fresh_analysis_drops_saved_answers_and_reuses_branch_set(tmp_path, serve, monkeypatch):
    c, settings, store, provider, cfg = serve
    _added(c, serve)
    import kb.analyze as an

    store.update_manifest("mini", "main", branches={"mini-fe": "master", "mini-be": "develop"})
    (store.pack_dir("mini", "main") / "overlay" / "qa" / "q1.json").parent.mkdir(parents=True, exist_ok=True)
    (store.pack_dir("mini", "main") / "overlay" / "qa" / "q1.json").write_text("{}")
    seen = {}

    def fake(cfg_, branch, store_, *a, **k):
        seen["branches"] = k.get("branches")
        seen["overlay_gone"] = not (store_.pack_dir("mini", "main") / "overlay" / "qa" / "q1.json").exists()
        return type("M", (), {"status": "complete", "stats": {}, "failed_modules": []})()
    monkeypatch.setattr(an, "run_analyze", fake)
    j = wait_job(c, c.post("/api/projects/mini/branches/main/analyze", json={"fresh": True}).json()["job_id"])
    assert j["state"] == "done", j["error"]
    assert seen == {"branches": {"mini-fe": "master", "mini-be": "develop"}, "overlay_gone": True}


def test_reanalyse_keeps_answers_and_branch_set(serve, monkeypatch):
    c, settings, store, *_ = serve
    _added(c, serve)
    import kb.analyze as an

    store.update_manifest("mini", "main", branches={"mini-fe": "master", "mini-be": "develop"})
    q = store.pack_dir("mini", "main") / "overlay" / "qa" / "q1.json"
    q.parent.mkdir(parents=True, exist_ok=True)
    q.write_text("{}")
    seen = {}

    def fake(cfg_, branch, store_, *a, **k):
        seen["branches"] = k.get("branches")
        return type("M", (), {"status": "complete", "stats": {}, "failed_modules": []})()
    monkeypatch.setattr(an, "run_analyze", fake)
    wait_job(c, c.post("/api/projects/mini/branches/main/analyze", json={}).json()["job_id"])
    assert seen["branches"] == {"mini-fe": "master", "mini-be": "develop"} and q.exists()
