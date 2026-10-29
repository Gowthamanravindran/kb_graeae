from datetime import datetime, timezone

from agent_helpers import GOOD_STEPS, LEARN, save_flow, trace_reply
from kb.ingest.checkout import Checkout
from kb.packs.model import QAItem, Suggestion
from kb.sync.outbox import Outbox
from serve_helpers import wait_job

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def setup(serve):
    c, settings, store, provider, cfg = serve
    c.post("/api/projects", json={"config_path": str(cfg)})
    return c, settings, store, provider


def test_map_view_layers_and_edges(serve):
    c, _, store, _ = setup(serve)
    m = c.get("/api/projects/mini/branches/main/map").json()
    assert m["apps"] == [{"repo": "mini-fe", "role": "frontend", "label": "mini-fe"}]
    mods = {x["id"]: x for x in m["modules"]}
    assert mods["tasks"]["layer"] == "product" and mods["audits"]["layer"] == "behind"
    assert mods["tasks"]["entry_points"][0] == {"id": "mini-fe:route:/tasks/create-task", "name": "/tasks/create-task",
                                                "kind": "route", "file": "src/App.tsx", "line": 8}
    assert mods["tasks"]["size"] == 10 and m["system_md"] == "Overview"
    assert isinstance(m["edges"], list)


def test_map_uses_module_note_summary(serve):
    c, _, store, _ = setup(serve)
    store.save_module_note("mini", "main", "tasks", "Sharper summary.")
    mods = {x["id"]: x for x in c.get("/api/projects/mini/branches/main/map").json()["modules"]}
    assert mods["tasks"]["summary"] == "Sharper summary."


def test_memory_view_states_and_counts(serve):
    c, settings, store, _ = setup(serve)
    for i, vis in enumerate(["private", "published", "private", "private"]):
        store.save_qa("mini", "main", QAItem(id=f"q{i}", question=f"Q{i}", answer="A" * 300, author="me",
                                             created_at=NOW, visibility=vis, stale=(i == 3)))
    Outbox(settings.kb_home).add("mini", "main", [{"id": "q2", "kind": "qa", "payload": {}}])
    store.add_suggestions("mini", "main", [Suggestion(id="s1", question="Next?", source_qa_id="q0")])
    v = c.get("/api/projects/mini/branches/main/memory").json()
    states = {i["qa_id"]: (i["state"], i["stale"]) for i in v["items"]}
    assert states == {"q0": ("private", False), "q1": ("published", False), "q2": ("queued", False), "q3": ("private", True)}
    assert len(v["items"][0]["answer_preview"]) <= 200
    assert v["counts"] == {"all": 4, "private": 3, "published": 1, "stale": 1, "suggested": 1}
    assert v["suggestions"] == [{"id": "s1", "question": "Next?", "source_qa_id": "q0"}]


def test_open_saved_answer(serve):
    c, settings, store, _ = setup(serve)
    from agent_helpers import FIX

    save_flow(store, {"mini-be": Checkout("mini-be", FIX / "mini-be", "y")})
    d = c.get("/api/projects/mini/branches/main/qa/q1").json()
    assert d["answer"] == "Saved answer." and d["flow"]["steps"][0]["text"] == "AddTask saves the task"
    assert c.get("/api/projects/mini/branches/main/qa/nope").status_code == 404


def test_retrace_runs_fresh(serve):
    c, settings, store, provider = setup(serve)
    from agent_helpers import FIX

    save_flow(store, {"mini-be": Checkout("mini-be", FIX / "mini-be", "y")}, question="How does scheduling of repeating tasks work?")
    provider.responses = [trace_reply(GOOD_STEPS), LEARN]
    j = wait_job(c, c.post("/api/projects/mini/branches/main/qa/q1/retrace").json()["job_id"])
    assert j["state"] == "done", j["error"]
    assert j["result"]["reused"] is False and j["result"]["how"]["claude_calls"] == 2


def test_run_suggestion(serve):
    c, settings, store, provider = setup(serve)
    store.add_suggestions("mini", "main", [Suggestion(id="s1", question="How does scheduling of repeating tasks work?",
                                                      source_qa_id="q0")])
    provider.responses = [trace_reply(GOOD_STEPS), LEARN]
    j = wait_job(c, c.post("/api/projects/mini/branches/main/suggestions/s1/run").json()["job_id"])
    assert j["state"] == "done" and j["result"]["answer"]


def test_publish_without_registry_is_400(serve):
    c, *_ = setup(serve)
    r = c.post("/api/projects/mini/branches/main/publish", json={"qa_ids": ["q1"], "notes": False})
    assert r.status_code == 400 and "registry" in r.json()["detail"]


def test_feedback_without_langfuse(serve):
    c, *_ = setup(serve)
    assert c.post("/api/feedback", json={"trace_id": "t1", "value": 1}).json() == {"recorded": False}


def test_feedback_langfuse_down_is_502(serve, monkeypatch):
    c, *_ = setup(serve)
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")

    class Down:
        def __init__(self, **kw):
            pass

        def create_score(self, **kw):
            raise ConnectionError("refused")

        def flush(self):
            pass

    import langfuse
    monkeypatch.setattr(langfuse, "Langfuse", Down)
    r = c.post("/api/feedback", json={"trace_id": "t1", "value": 1})
    assert r.status_code == 502 and r.json()["detail"] == "Langfuse is unreachable: refused"
