"""Three people, one branch: analyse+push, pull+ask+publish, pull+reuse, then re-analysis marks it stale."""

import json
import shutil

import pytest

from kb.analyze import run_analyze
from kb.ask import run_ask
from kb.config import ProjectConfig
from kb.packs.store import PackStore
from kb.sync.commands import publish_items, pull_pack, push_pack
from kb.tracing import NoopTracer

from agent_helpers import FIX, GOOD_STEPS, LEARN, trace_reply
from fakes import FakeProvider

pytestmark = pytest.mark.skipif(shutil.which("graphify") is None, reason="graphify not installed")
DRAFT = "```json\n" + json.dumps({"name": "Tasks", "summary": "People create and schedule repeating tasks.",
                                  "entry_points": []}) + "\n```"
Q = "How do people schedule repeating tasks?"


def test_three_users_share_one_branch(tmp_path, client_for_user):
    for r in ("mini-fe", "mini-be"):
        shutil.copytree(FIX / r, tmp_path / "src" / r)
    cfg = ProjectConfig(project="mini", registry="http://testserver",
                        repos=[{"name": r, "role": "x", "local_path": str(tmp_path / "src" / r)} for r in ("mini-fe", "mini-be")])
    a, b, c = (PackStore(tmp_path / h) for h in ("alice", "bob", "carol"))

    # 1. maintainer analyses and pushes
    run_analyze(cfg, "main", a, FakeProvider(default=DRAFT), NoopTracer(), author="alice")
    assert push_pack(cfg, "main", a, client_for_user("maint@x"))["version"] == 1

    # 2. contributor pulls, asks, publishes
    pull_pack("mini", "main", b, client_for_user("contrib@x"), cfg=cfg)
    st = run_ask(cfg, "main", Q, b, FakeProvider([trace_reply(GOOD_STEPS), LEARN]), NoopTracer(), author="bob")
    assert st["saved_qa_id"] and all(s.verified for s in st["steps"])
    publish_items(cfg, "main", b, client_for_user("contrib@x"), [st["saved_qa_id"]], include_notes=False)

    # 3. viewer pulls and gets the answer back with no Claude call
    pull_pack("mini", "main", c, client_for_user("view@x"), cfg=cfg)
    pc = c.load("mini", "main")
    assert pc.modules and [q.visibility for q in pc.qa] == ["published"]
    p = FakeProvider()
    again = run_ask(cfg, "main", Q, c, p, NoopTracer(), author="carol")
    assert again["reused_flow_id"] and p.requests == []
    assert again["answer"] == st["answer"]

    # 4. code changes; maintainer re-analyses and pushes v2; viewer's copy marks the answer stale
    f = tmp_path / "src" / "mini-be" / "src" / "module" / "tasks" / "task" / "resolvers.ts"
    f.write_text(f.read_text().replace("TaskModel.create(args.input)", "TaskModel.insertOne(args.input)"))
    run_analyze(cfg, "main", a, FakeProvider(default=DRAFT), NoopTracer(), author="alice")
    out = push_pack(cfg, "main", a, client_for_user("maint@x"))
    assert (out["version"], out["parent"]) == (2, 1)
    pull_pack("mini", "main", c, client_for_user("view@x"), cfg=cfg)
    pc = c.load("mini", "main")
    assert pc.manifest.version == 2 and [q.stale for q in pc.qa] == [True]
