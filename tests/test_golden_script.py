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
