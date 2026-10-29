"""Final-review fixes on the client side."""
import json
from datetime import datetime, timezone

import pytest

from kb.errors import KbError
from kb.packs.model import QAItem
from kb.packs.store import PackStore
from kb.sync.commands import publish_items, pull_pack, push_pack
from kb.sync.outbox import Outbox
from kb.sync.pack_io import apply_overlay

from agent_helpers import build_pack, save_flow
from test_publish import offline_client
from test_push import cfg

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


# C1 client side -------------------------------------------------------
def test_apply_overlay_skips_unsafe_ids(tmp_path):
    store, _ = build_pack(tmp_path)
    evil = QAItem(id="../../../../ESCAPED", question="q", answer="a", author="x", created_at=NOW).model_dump(mode="json")
    items = [{"id": "../../../../ESCAPED", "kind": "qa", "payload": evil},
             {"id": "note:x", "kind": "module_note", "payload": {"module_id": str(tmp_path / "abs" / "evil"), "text": "t"}}]
    written, warnings = apply_overlay(store, "mini", "main", items)
    assert written == 0 and len(warnings) == 2
    assert not list(tmp_path.rglob("ESCAPED*")) and not list(tmp_path.rglob("evil*"))


def test_store_refuses_ids_that_escape(tmp_path):
    store, _ = build_pack(tmp_path)
    with pytest.raises(KbError):
        store.save_qa("mini", "main", QAItem(id="../x", question="q", answer="a", author="x", created_at=NOW))
    with pytest.raises(KbError):
        store.save_module_note("mini", "main", "../../x", "t")


# I3 -----------------------------------------------------------------
def test_rejected_queued_publish_does_not_block_pull(tmp_path, registry, client_for_user):
    a, cos = build_pack(tmp_path / "a")
    push_pack(cfg(), "main", a, client_for_user("maint@x"))
    b = PackStore(tmp_path / "b")
    pull_pack("mini", "main", b, client_for_user("view@x"))
    save_flow(b, cos)
    off = offline_client(registry)
    off.token = registry.tokens["view@x"]
    assert publish_items(cfg(), "main", b, off, ["q1"], include_notes=False) == {"queued": 2}
    out = pull_pack("mini", "main", b, client_for_user("view@x"))  # must not raise
    assert Outbox(b.kb_home).pending("mini", "main") == []
    rejected = Outbox(b.kb_home).rejected("mini", "main")
    assert {i["id"] for i in rejected} == {"q1", "f1"} and "viewer" in rejected[0]["reason"]
    assert any("rejected" in w for w in out["warnings"])


# I7 -----------------------------------------------------------------
def test_pull_will_not_silently_replace_an_unpushed_analysis(tmp_path, client_for_user):
    a, _ = build_pack(tmp_path / "a")
    push_pack(cfg(), "main", a, client_for_user("maint@x"))
    mine, _ = build_pack(tmp_path / "mine")  # same project/branch, analysed locally, never pushed
    mine.update_manifest("mini", "main", commits={"mini-fe": "new", "mini-be": "new"})
    with pytest.raises(KbError, match="kb push"):
        pull_pack("mini", "main", mine, client_for_user("maint@x"))
    assert mine.load("mini", "main").manifest.commits["mini-fe"] == "new"
    pull_pack("mini", "main", mine, client_for_user("maint@x"), force=True)
    assert mine.load("mini", "main").manifest.commits["mini-fe"] == "x"


def test_pull_after_push_or_pull_needs_no_force(tmp_path, client_for_user):
    a, _ = build_pack(tmp_path / "a")
    push_pack(cfg(), "main", a, client_for_user("maint@x"))
    assert a.load("mini", "main").manifest.synced is True
    pull_pack("mini", "main", a, client_for_user("maint@x"))
    b = PackStore(tmp_path / "b")
    pull_pack("mini", "main", b, client_for_user("view@x"))
    assert b.load("mini", "main").manifest.synced is True
