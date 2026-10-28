"""Golden-question checks: does `kb ask` cite the files we know implement each feature?"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import yaml

DEFAULTS = {"min_recall": 0.5, "min_verified_ratio": 0.6}


def load_golden(path: Path) -> list[dict]:
    return [{**DEFAULTS, **e} for e in yaml.safe_load(Path(path).read_text())]


def score(entry: dict, steps) -> dict:
    cited = {f"{s.citation.repo}:{s.citation.file}" for s in steps if s.citation}
    expected = entry["expect_files"]
    recall = sum(1 for f in expected if f in cited) / len(expected) if expected else 1.0
    verified = sum(1 for s in steps if s.verified) / len(steps) if steps else 0.0
    return {"question": entry["question"], "recall": recall, "verified_ratio": verified,
            "missing": [f for f in expected if f not in cited],
            "passed": recall >= entry["min_recall"] and verified >= entry["min_verified_ratio"]}


def run_golden(cfg, branch: str, golden_path: Path, store, provider, tracer, author: str) -> list[dict]:
    from kb.ask import run_ask

    results = []
    for e in load_golden(golden_path):
        st = run_ask(cfg, branch, e["question"], store, provider, tracer, author=author)
        r = score(e, st.get("steps", []))
        r["reused"] = bool(st.get("reused_flow_id"))
        results.append(r)
    client = getattr(tracer, "client", None)
    if client is not None:  # Langfuse on: record the run as a dataset
        name = f"golden-{date.today().isoformat()}"
        client.create_dataset(name=name)
        for r in results:
            client.create_dataset_item(dataset_name=name, input={"question": r["question"]},
                                       expected_output={"files": r["missing"]}, metadata=r)
        client.flush()
    return results
