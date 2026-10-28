import hashlib

import pytest

from helpers import seeded
from test_api_bases import push, upload

NOW = "2026-10-28T00:00:00Z"


def qa(id="q1", answer="It saves the task."):
    return {"id": id, "kind": "qa", "payload": {"id": id, "question": "How?", "answer": answer, "flow_id": "f1",
                                                "citations": [], "author": "c", "created_at": NOW, "visibility": "private"}}


def flow(id="f1"):
    return {"id": id, "kind": "flow", "payload": {"id": id, "question": "How?", "module_ids": ["tasks"], "steps": [],
                                                  "mermaid": "flowchart LR", "created_at": NOW}}


def note(text="Better summary"):
    nid = f"note:tasks:{hashlib.sha256(text.encode()).hexdigest()[:12]}"
    return {"id": nid, "kind": "module_note", "payload": {"module_id": "tasks", "text": text}}


@pytest.fixture
def reg(tmp_path):
    r = seeded(tmp_path)
    assert push(r, {"a": "1"}).status_code == 201
    return r


def pub(reg, items, email="contrib@x", branch="main"):
    return reg.client(email).post(f"/api/v1/projects/mini/branches/{branch}/overlay", json={"items": items})


def test_publish_then_latest_contains_items_marked_published(reg):
    r = pub(reg, [qa(), flow(), note(),
                  {"id": "s1", "kind": "suggestion", "payload": {"id": "s1", "question": "Next?", "source_qa_id": "q1"}}])
    assert r.status_code == 200 and sorted(r.json()["added"]) == sorted(["q1", "f1", note()["id"], "s1"])
    lt = reg.client("view@x").get("/api/v1/projects/mini/branches/main/latest").json()
    assert lt["version"] == 1 and set(lt["files"]) == {"base/links.json", "base/system.md"}
    assert lt["manifest"]["project"] == "mini"
    items = {i["id"]: i for i in lt["overlay"]}
    assert items["q1"]["payload"]["visibility"] == "published" and items["q1"]["author"] == "contrib@x"
    assert items["f1"]["payload"]["visibility"] == "published"


def test_republish_same_payload_is_existing(reg):
    pub(reg, [qa()])
    r = pub(reg, [qa()])
    assert r.json() == {"added": [], "existing": ["q1"]}


def test_same_id_different_payload_409(reg):
    pub(reg, [qa()])
    r = pub(reg, [qa(answer="Different."), flow()])
    assert r.status_code == 409 and "q1" in r.json()["detail"]
    lt = reg.client("view@x").get("/api/v1/projects/mini/branches/main/latest").json()
    assert [i["id"] for i in lt["overlay"]] == ["q1"]  # nothing partial written


def test_publish_without_base_404(reg):
    r = pub(reg, [qa()], branch="dev")
    assert r.status_code == 404 and r.json()["detail"] == "no analysed base for mini@dev — push the analysis first"


def test_payload_validation_400(reg):
    assert pub(reg, [{"id": "q1", "kind": "qa", "payload": {"id": "q1"}}]).status_code == 400
    assert pub(reg, [{"id": "x", "kind": "weird", "payload": {}}]).status_code == 400
    bad_note = note()
    bad_note["id"] = "note:tasks:wrong"
    assert pub(reg, [bad_note]).status_code == 400
    mismatch = qa()
    mismatch["id"] = "other"
    assert pub(reg, [mismatch]).status_code == 400


def test_viewer_cannot_publish_403(reg):
    assert pub(reg, [qa()], email="view@x").status_code == 403


def test_item_and_count_limits_413(reg):
    assert pub(reg, [qa(answer="x" * 300_000)]).status_code == 413
    assert pub(reg, [qa(id=f"q{i}") for i in range(501)]).status_code == 413


def test_latest_unknown_branch_404(reg):
    assert reg.client("view@x").get("/api/v1/projects/mini/branches/dev/latest").status_code == 404
