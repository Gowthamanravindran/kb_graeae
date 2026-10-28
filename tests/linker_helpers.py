from pathlib import Path

from kb.config import LinkerConfig
from kb.graph.model import CodeGraph
from kb.ingest.checkout import Checkout
from kb.linkers.base import LinkContext

FIX = Path(__file__).parent / "fixtures"


def ctx(cfg: dict, repos=("mini-fe", "mini-be"), roots=None) -> LinkContext:
    roots = roots or {r: FIX / r for r in repos}
    return LinkContext(
        graph=CodeGraph(),
        checkouts={r: Checkout(r, Path(roots[r]), "x") for r in repos},
        config=LinkerConfig.model_validate(cfg),
    )


def run(linker, c: LinkContext):
    edges = linker.run(c)
    for e in edges:
        c.graph.add_edge(e)
    return edges
