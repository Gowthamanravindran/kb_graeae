from pathlib import Path

import yaml

from kb.packs.model import Citation, FlowStep
from kb.golden import load_golden, score


def step(repo, file, ok=True):
    return FlowStep(side="ui", text="t", verified=ok,
                    citation=Citation(repo=repo, file=file, line_start=1, line_end=1, content_hash="h") if ok else None)


def test_score_recall_and_verified():
    entry = {"question": "q", "expect_files": ["a:x.ts", "b:y.ts"], "min_recall": 1.0, "min_verified_ratio": 0.75}
    r = score(entry, [step("a", "x.ts"), step("b", "y.ts"), step("b", "z.ts"), step("b", "w", ok=False)])
    assert r["recall"] == 1.0 and r["verified_ratio"] == 0.75 and r["passed"]
    r2 = score(entry, [step("a", "x.ts")])
    assert r2["recall"] == 0.5 and not r2["passed"]


def test_defaults_and_file_loads():
    entries = load_golden(Path(__file__).parent / "golden" / "sop_questions.yaml")
    assert len(entries) == 10
    assert entries[0]["min_recall"] == 1.0 and entries[1]["min_recall"] == 0.5 and entries[1]["min_verified_ratio"] == 0.6
    assert score(entries[1], [])["passed"] is False


def test_run_golden_is_fresh_and_isolated(tmp_path):
    import yaml as _y

    from kb.config import ProjectConfig
    from kb.golden import run_golden
    from kb.tracing import NoopTracer

    from agent_helpers import FIX, GOOD_STEPS, LEARN, build_pack, save_flow, trace_reply
    from fakes import FakeProvider

    store, cos = build_pack(tmp_path)
    save_flow(store, cos, question="How does scheduling of repeating tasks work?")
    g = tmp_path / "g.yaml"
    g.write_text(_y.safe_dump([{"question": "How does scheduling of repeating tasks work?",
                                "expect_files": ["mini-be:src/module/tasks/task/resolvers.ts"]}]))
    cfg = ProjectConfig(project="mini", repos=[{"name": r, "role": "x", "local_path": str(FIX / r)} for r in ("mini-fe", "mini-be")])
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN])
    [r] = run_golden(cfg, "main", g, store, p, NoopTracer(), "me")
    assert len(p.requests) >= 1 and r["recall"] == 1.0 and not r["reused"]
    assert [q.id for q in store.load("mini", "main").qa] == ["q1"]  # user's overlay untouched
