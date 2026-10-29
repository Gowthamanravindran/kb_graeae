import json
import subprocess
from pathlib import Path

import pytest

from kb.errors import KbError
from kb.ingest.checkout import Checkout
from kb.ingest.graphify import parse_graphify_json, run_graphify

FIX = Path(__file__).parent / "fixtures"
GRAPH = FIX / "graphify" / "mini_graph.json"
ROOT = FIX / "mini-be"


def test_nodes_are_scoped_to_repo_with_relative_files():
    g = parse_graphify_json(GRAPH, "mini-be", ROOT)
    nodes = g.nodes()
    assert nodes and all(x.repo == "mini-be" for x in nodes)
    assert all(not x.file.startswith("/") for x in nodes)
    assert "mini-be:src/queue/task/main.ts" in {x.id for x in nodes}


def test_function_node_has_line_and_id_format():
    g = parse_graphify_json(GRAPH, "mini-be", ROOT)
    fn = g.node("mini-be:src/module/tasks/task/resolvers.ts#scheduleRepeat")
    assert fn.kind == "function" and fn.line == 18 and fn.name == "scheduleRepeat"
    assert g.node("mini-be:src/module/tasks/task/resolvers.ts").kind == "file"


def test_edge_origins_and_external_refs_dropped():
    g = parse_graphify_json(GRAPH, "mini-be", ROOT)
    edges = g.edges()
    assert edges
    assert {e.origin for e in edges} <= {"extracted", "inferred"}
    assert not any("ref_types" in e.dst for e in edges)
    assert any(e.kind == "calls" for e in edges)


def test_ignore_globs_excluded():
    g = parse_graphify_json(GRAPH, "mini-be", ROOT, ignore=["**/queue/**"])
    assert not any("/queue/" in x.file or x.file.startswith("src/queue") for x in g.nodes())


def test_accepts_links_key(tmp_path):
    raw = json.loads(GRAPH.read_text())
    raw["links"] = raw.pop("edges")
    p = tmp_path / "g.json"
    p.write_text(json.dumps(raw))
    assert parse_graphify_json(p, "mini-be", ROOT).edges()


def test_run_graphify_uses_no_llm_and_out_dir(tmp_path, monkeypatch):
    seen = {}

    def fake_run(args, cwd=None, env=None, **kw):
        seen.update(args=args, cwd=cwd, env=env)
        out = Path(args[args.index("--out") + 1]) / "graphify-out"
        out.mkdir(parents=True)
        (out / "graph.json").write_text("{}")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("kb.ingest.graphify.shutil.which", lambda _: "/usr/bin/graphify")
    monkeypatch.setattr("kb.ingest.graphify.subprocess.run", fake_run)
    p = run_graphify(Checkout("mini-be", ROOT, "x"), tmp_path / "o", [])
    assert p == tmp_path / "o" / "graphify-out" / "graph.json"
    assert seen["env"]["GRAPHIFY_BACKEND"] == "none"
    assert "--code-only" in seen["args"]
    assert seen["cwd"] == ROOT


def test_missing_graphify_binary(tmp_path, monkeypatch):
    monkeypatch.setattr("kb.ingest.graphify.shutil.which", lambda _: None)
    monkeypatch.setattr("kb.ingest.graphify.importlib.util.find_spec", lambda _: None)
    with pytest.raises(KbError, match="uv tool install graphifyy"):
        run_graphify(Checkout("mini-be", ROOT, "x"), tmp_path, [])
