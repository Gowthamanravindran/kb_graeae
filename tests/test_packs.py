import shutil
from datetime import datetime, timezone
from pathlib import Path

import pytest

from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.ingest.checkout import Checkout
from kb.packs.index import build_index, search
from kb.packs.model import Citation, Flow, FlowStep, Manifest, Module, QAItem, Suggestion
from kb.packs.staleness import hash_span, is_stale
from kb.packs.store import PackStore

NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def graphs():
    fe, be = CodeGraph(), CodeGraph()
    fe.add_node(CodeNode(id="fe:src/Sched.tsx", repo="fe", kind="file", name="src/Sched.tsx", file="src/Sched.tsx", line=1))
    fe.add_node(CodeNode(id="fe:src/Sched.tsx#scheduleRepeat", repo="fe", kind="function", name="scheduleRepeat", file="src/Sched.tsx", line=3))
    fe.add_edge(CodeEdge(src="fe:src/Sched.tsx", dst="fe:src/Sched.tsx#scheduleRepeat", kind="contains", origin="extracted"))
    be.add_node(CodeNode(id="be:r.ts", repo="be", kind="file", name="r.ts", file="r.ts", line=1))
    return {"fe": fe, "be": be}


def manifest(**kw):
    base = dict(project="sop", branch="feature/x", commits={"fe": "a", "be": "b"}, author="me", created_at=NOW,
                status="complete", stats={"nodes": 3})
    return Manifest(**{**base, **kw})


MODULES = [Module(id="tasks", name="Tasks", summary="Assign and schedule recurring work for Locations.",
                  members=["fe:src/Sched.tsx"], entry_points=["fe:src/Sched.tsx"])]
LINK_NODE = CodeNode(id="be:r.ts#resolver:AddTask@1", repo="be", kind="resolver", name="AddTask", file="r.ts", line=1)
LINKS = [CodeEdge(src="fe:src/Sched.tsx", dst="be:r.ts#resolver:AddTask@1", kind="handled_by", origin="linker"),
         CodeEdge(src="fe:src/Sched.tsx", dst="unresolved:graphql:X", kind="handled_by", origin="linker", resolved=False)]


def write(tmp_path):
    s = PackStore(tmp_path / "kbhome")
    d = s.write_base("sop", "feature/x", manifest(), graphs(), LINKS, MODULES, "# Overview", link_nodes=[LINK_NODE])
    return s, d


def test_write_then_load_roundtrip(tmp_path):
    s, d = write(tmp_path)
    p = s.load("sop", "feature/x")
    assert p.manifest.commits == {"fe": "a", "be": "b"}
    assert p.graph.node("be:r.ts#resolver:AddTask@1").name == "AddTask"
    assert len(p.graph.edges(kind="handled_by")) == 2
    assert p.modules[0].name == "Tasks" and p.system_md == "# Overview"
    assert d == tmp_path / "kbhome" / "packs" / "sop@feature__x"


def test_layout_matches_spec(tmp_path):
    _, d = write(tmp_path)
    for rel in ["manifest.json", "base/graph/fe.json", "base/graph/be.json", "base/links.json", "base/modules.json",
                "base/system.md", "overlay/qa", "overlay/flows", "overlay/module_notes"]:
        assert (d / rel).exists(), rel


def test_overlay_saved_and_survives_rewrite_of_base(tmp_path):
    s, _ = write(tmp_path)
    c = Citation(repo="fe", file="src/Sched.tsx", line_start=3, line_end=4, content_hash="h")
    flow = Flow(id="f1", question="How?", module_ids=["tasks"], steps=[FlowStep(side="ui", text="x", citation=c, verified=True)],
                mermaid="flowchart LR", created_at=NOW)
    s.save_flow("sop", "feature/x", flow)
    s.save_qa("sop", "feature/x", QAItem(id="q1", question="How?", answer="A", flow_id="f1", citations=[c], author="me", created_at=NOW))
    s.add_suggestions("sop", "feature/x", [Suggestion(id="s1", question="Next?", source_qa_id="q1")])
    s.save_module_note("sop", "feature/x", "tasks", "Better summary")
    write(tmp_path)
    p = s.load("sop", "feature/x")
    assert [f.id for f in p.flows] == ["f1"] and p.qa[0].visibility == "private"
    assert p.suggestions[0].question == "Next?" and p.module_notes == {"tasks": "Better summary"}


def test_update_manifest(tmp_path):
    s, _ = write(tmp_path)
    s.update_manifest("sop", "feature/x", status="incomplete", failed_modules=["m1"])
    m = s.load("sop", "feature/x").manifest
    assert m.status == "incomplete" and m.failed_modules == ["m1"]


def test_load_missing_pack_raises(tmp_path):
    from kb.errors import KbError

    with pytest.raises(KbError, match="kb analyze"):
        PackStore(tmp_path).load("sop", "main")


@pytest.mark.skipif(shutil.which("graphify") is None, reason="graphify not installed")
def test_sanitized_graph_contains_no_source_text(tmp_path):
    from kb.ingest.graphify import parse_graphify_json, run_graphify

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.ts").write_text("export function scheduleIt() {\n  const s = 'SECRET_BODY_MARKER';\n  return s;\n}\n")
    co = Checkout("fe", repo, "x")
    g = parse_graphify_json(run_graphify(co, tmp_path / "gout", []), "fe", repo)
    assert g.nodes()
    s = PackStore(tmp_path / "home")
    d = s.write_base("p", "b", manifest(project="p", branch="b"), {"fe": g}, [], [], "")
    for f in d.rglob("*"):
        if f.is_file():
            assert b"SECRET_BODY_MARKER" not in f.read_bytes(), f


def test_fts_finds_module_by_summary_word(tmp_path):
    s, d = write(tmp_path)
    build_index(d, s.load("sop", "feature/x"))
    hits = search(d, "How does recurring work get scheduled?")
    assert hits[0].kind == "module" and hits[0].ref == "tasks"
    assert all(h.score > 0 for h in hits)
    assert any(h.kind == "node" and h.ref == "fe:src/Sched.tsx#scheduleRepeat" for h in search(d, "schedule repeat"))
    assert search(d, "zzzz qqqq") == []


def test_hash_changes_when_cited_lines_change_only(tmp_path):
    f = tmp_path / "a.ts"
    f.write_text("l1\nl2\nl3\nl4\n")
    h = hash_span(tmp_path, "a.ts", 2, 3)
    f.write_text("l1 changed\nl2\nl3\nl4 changed\n")
    assert hash_span(tmp_path, "a.ts", 2, 3) == h
    f.write_text("l1\nl2 changed\nl3\nl4\n")
    assert hash_span(tmp_path, "a.ts", 2, 3) != h
    c = Citation(repo="fe", file="a.ts", line_start=2, line_end=3, content_hash=h)
    assert is_stale(c, {"fe": Checkout("fe", tmp_path, "x")})


def test_missing_file_is_stale(tmp_path):
    c = Citation(repo="fe", file="gone.ts", line_start=1, line_end=1, content_hash="x")
    assert is_stale(c, {"fe": Checkout("fe", tmp_path, "x")})
