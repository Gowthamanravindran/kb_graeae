"""Usage: uv run python scripts/golden.py <kb.yaml> --branch <b> [--golden tests/golden/sop_questions.yaml]"""

import argparse
import sys
from pathlib import Path

from kb.cli import _author, get_provider
from kb.config import load_config
from kb.golden import run_golden
from kb.packs.store import PackStore
from kb.tracing import make_tracer


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("config")
    ap.add_argument("--branch", required=True)
    ap.add_argument("--golden", default=str(Path(__file__).parent.parent / "tests" / "golden" / "sop_questions.yaml"))
    a = ap.parse_args()
    cfg = load_config(Path(a.config))
    results = run_golden(cfg, a.branch, Path(a.golden), PackStore(), get_provider(), make_tracer(cfg), _author())
    for r in results:
        mark = "PASS" if r["passed"] else "FAIL"
        print(f"{mark}  recall {r['recall']:.2f}  verified {r['verified_ratio']:.2f}  {r['question']}"
              + (f"\n      missing: {', '.join(r['missing'])}" if r["missing"] else ""))
    passed = sum(r["passed"] for r in results)
    print(f"\n{passed}/{len(results)} passed")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())
