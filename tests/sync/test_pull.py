from datetime import datetime, timezone
from pathlib import Path

import pytest

from kb.errors import KbError
from kb.packs.index import search
from kb.packs.model import Citation, QAItem
from kb.packs.store import PackStore
from kb.sync.commands import pull_pack, push_pack

from agent_helpers import build_pack
from test_push import cfg

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


@pytest.fixture
def pushed(tmp_path, client_for_user):
    a, _ = build_pack(tmp_path / "a")
    push_pack(cfg(), "main", a, client_for_user("maint@x"))
    return a


def test_pull_materialises_pack_and_index(tmp_path, pushed, client_for_user):
    b = PackStore(tmp_path / "b")
    out = pull_pack("mini", "main", b, client_for_user("view@x"))
    p = b.load("mini", "main")
    assert out["version"] == 1 and p.manifest.version == 1 and [m.id for m in p.modules] == ["tasks", "audits"]
    assert p.graph.has_node("mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6")
    assert any(h.ref == "tasks" for h in search(p.dir, "repeating schedule"))


def test_pull_keeps_private_items(tmp_path, pushed, client_for_user):
    b = PackStore(tmp_path / "b")
    pull_pack("mini", "main", b, client_for_user("view@x"))
    b.save_qa("mini", "main", QAItem(id="mine", question="q", answer="private note", author="me", created_at=NOW))
    pushed.update_manifest("mini", "main", commits={"mini-fe": "x2", "mini-be": "y2"})
    push_pack(cfg(), "main", pushed, client_for_user("maint@x"))
    pull_pack("mini", "main", b, client_for_user("view@x"))
    p = b.load("mini", "main")
    assert p.manifest.version == 2 and p.manifest.commits["mini-fe"] == "x2"
    [mine] = [q for q in p.qa if q.id == "mine"]
    assert mine.visibility == "private" and mine.answer == "private note"


class FakeClient:
    base_url = "http://fake"

    def __init__(self, files):
        self.files = files

    def me(self):
        return {"is_admin": True, "projects": {}}

    def latest(self, p, b):
        return {"version": 1, "parent": None, "base_id": 1, "files": self.files, "overlay": [],
                "manifest": {"project": p, "branch": b, "commits": {}, "author": "x", "created_at": NOW.isoformat(),
                             "status": "complete"}}

    def get_blob(self, h):
        return b"{}"

    def project(self, p):
        return {"config_yaml": ""}


@pytest.mark.parametrize("bad", ["../../evil.json", "/abs/evil.json", "base/graph/../../x.json", "base/index.sqlite"])
def test_pull_rejects_unsafe_paths(tmp_path, bad):
    store = PackStore(tmp_path / "home")
    with pytest.raises(KbError, match="unsafe path from registry"):
        pull_pack("mini", "main", store, FakeClient({"base/links.json": "a" * 64, bad: "b" * 64}))
    assert not (tmp_path / "evil.json").exists() and not Path("/abs/evil.json").exists()
    assert not (store.pack_dir("mini", "main") / "base" / "links.json").exists()


def test_pull_uses_blob_cache(tmp_path, pushed, client_for_user):
    b = PackStore(tmp_path / "b")
    c = client_for_user("view@x")
    pull_pack("mini", "main", b, c)
    calls = []
    orig = c.get_blob
    c.get_blob = lambda h: (calls.append(h), orig(h))[1]
    pull_pack("mini", "main", b, c)
    assert calls == []


def test_pull_saves_registry_config(tmp_path, pushed, client_for_user):
    b = PackStore(tmp_path / "b")
    pull_pack("mini", "main", b, client_for_user("view@x"))
    text = (tmp_path / "b" / "projects" / "mini" / "kb.yaml").read_text()
    import yaml

    data = yaml.safe_load(text)
    assert data["project"] == "mini" and all("local_path" not in r for r in data["repos"])


def test_pull_marks_stale_with_local_checkout(tmp_path, pushed, client_for_user):
    c = Citation(repo="mini-be", file="src/queue/user.ts", line_start=1, line_end=2, content_hash="old")
    qa = QAItem(id="q9", question="q", answer="a", citations=[c], author="c", created_at=NOW)
    client_for_user("contrib@x").publish("mini", "main", [{"id": "q9", "kind": "qa", "payload": qa.model_dump(mode="json")}])
    b = PackStore(tmp_path / "b")
    from kb.config import ProjectConfig

    from agent_helpers import FIX

    local_cfg = ProjectConfig(project="mini", repos=[{"name": r, "role": "x", "local_path": str(FIX / r)}
                                                     for r in ("mini-fe", "mini-be")])
    out = pull_pack("mini", "main", b, client_for_user("view@x"), cfg=local_cfg)
    [got] = [q for q in b.load("mini", "main").qa if q.id == "q9"]
    assert got.visibility == "published" and got.stale is True and out["stale"] == 1


def test_published_item_does_not_overwrite_different_private_local(tmp_path, pushed, client_for_user):
    b = PackStore(tmp_path / "b")
    pull_pack("mini", "main", b, client_for_user("view@x"))
    b.save_qa("mini", "main", QAItem(id="q1", question="q", answer="local", author="me", created_at=NOW))
    other = QAItem(id="q1", question="q", answer="remote", author="c", created_at=NOW)
    client_for_user("contrib@x").publish("mini", "main", [{"id": "q1", "kind": "qa", "payload": other.model_dump(mode="json")}])
    out = pull_pack("mini", "main", b, client_for_user("view@x"))
    assert [q.answer for q in b.load("mini", "main").qa if q.id == "q1"] == ["local"]
    assert any("q1" in w for w in out["warnings"])
