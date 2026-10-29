import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from kb.analyze import run_analyze
from kb.config import load_config
from kb.errors import LLMUnavailable
from kb.packs.store import PackStore

from fakes import FakeProvider, RecordingTracer

pytestmark = pytest.mark.skipif(shutil.which("graphify") is None, reason="graphify not installed")
FIX = Path(__file__).parent / "fixtures"
DRAFT = "```json\n" + json.dumps({"name": "Tasks", "summary": "People create and schedule tasks.", "entry_points": []}) + "\n```"


@pytest.fixture
def setup(tmp_path):
    for r in ("mini-fe", "mini-be"):
        shutil.copytree(FIX / r, tmp_path / r)
    (tmp_path / "mini-be" / "CONTEXT.md").write_text("Entity: a brand. Location: a franchise.")
    y = tmp_path / "kb.yaml"
    y.write_text(f"""
project: mini
repos:
  - {{name: mini-fe, role: frontend, local_path: {tmp_path / 'mini-fe'}}}
  - {{name: mini-be, role: backend, local_path: {tmp_path / 'mini-be'}}}
linkers:
  - {{type: graphql, from: mini-fe, to: mini-be, options: {{backend_style: graphql-compose}}}}
  - {{type: bull-queue, in: mini-be}}
  - {{type: react-router, in: mini-fe}}
context_docs: ["mini-be:CONTEXT.md"]
""")
    return load_config(y), PackStore(tmp_path / "home"), tmp_path


def test_analyze_writes_complete_pack(setup):
    cfg, store, root = setup
    p = FakeProvider(default=DRAFT)
    m = run_analyze(cfg, "main", store, p, RecordingTracer(), author="me")
    assert m.status == "complete" and m.failed_modules == []
    assert set(m.commits) == {"mini-fe", "mini-be"} and m.commits["mini-fe"].startswith("local-")
    assert m.stats["links_resolved"] >= 3 and m.stats["links_unresolved"] >= 1 and m.stats["modules"] >= 1
    pack = store.load("mini", "main")
    assert any(e.kind == "handled_by" and e.resolved for e in pack.graph.edges())
    assert "```mermaid" in pack.system_md
    assert (pack.dir / "index.sqlite").exists()
    assert any("Entity: a brand" in r.prompt for r in p.requests)
    assert not (root / "mini-be" / "graphify-out").exists()


def test_analyze_without_llm_saves_structure_and_marks_incomplete(setup):
    cfg, store, _ = setup
    with pytest.raises(LLMUnavailable):
        run_analyze(cfg, "main", store, FakeProvider(unavailable="not signed in"), RecordingTracer(), author="me")
    d = store.pack_dir("mini", "main")
    assert (d / "base" / "links.json").exists()
    assert json.loads((d / "manifest.json").read_text())["status"] == "incomplete"
    assert (d / "index.sqlite").exists()  # questions can still find code by name


def test_resume_completes_failed_modules(setup):
    cfg, store, _ = setup
    m1 = run_analyze(cfg, "main", store, FakeProvider(["bad", "bad"], default=DRAFT), RecordingTracer(), author="me")
    assert m1.status == "incomplete" and len(m1.failed_modules) == 1
    p2 = FakeProvider(default=DRAFT)
    m2 = run_analyze(cfg, "main", store, p2, RecordingTracer(), resume=True, author="me")
    assert m2.status == "complete" and m2.failed_modules == []
    assert len([r for r in p2.requests if "Name this group" in r.prompt]) == 1


def test_analyze_emits_spans_in_order(setup):
    cfg, store, _ = setup
    tr = RecordingTracer()
    run_analyze(cfg, "main", store, FakeProvider(default=DRAFT), tr, author="me")
    [(name, tags)] = tr.traces
    assert name == "analyze" and tags["project"] == "mini" and tags["branch"] == "main"
    root = tr.all[0]
    assert [c.name for c in root.children if c.kind == "span"] == ["checkout", "graph", "link", "module_map"]


def test_reanalyze_marks_changed_citations_stale(setup):
    from datetime import datetime, timezone

    from kb.packs.model import Citation, QAItem
    from kb.packs.staleness import hash_span

    cfg, store, root = setup
    run_analyze(cfg, "main", store, FakeProvider(default=DRAFT), RecordingTracer(), author="me")
    f = "src/queue/user.ts"
    c = Citation(repo="mini-be", file=f, line_start=1, line_end=2, content_hash=hash_span(root / "mini-be", f, 1, 2))
    store.save_qa("mini", "main", QAItem(id="q1", question="q", answer="a", citations=[c], author="me",
                                         created_at=datetime.now(timezone.utc)))
    (root / "mini-be" / f).write_text("export async function renamed(job) {\n  return 1;\n}\n")
    run_analyze(cfg, "main", store, FakeProvider(default=DRAFT), RecordingTracer(), resume=True, author="me")
    assert store.load("mini", "main").qa[0].stale is True


def test_cli_analyze(setup, monkeypatch):
    from kb.cli import app

    cfg, store, root = setup
    monkeypatch.setenv("KB_HOME", str(root / "home"))
    monkeypatch.setattr("kb.cli.get_provider", lambda: FakeProvider(default=DRAFT))
    r = CliRunner().invoke(app, ["analyze", str(root / "kb.yaml"), "--branch", "main"])
    assert r.exit_code == 0, r.output
    assert "complete" in r.stdout and "links" in r.stdout


def test_cli_analyze_llm_unavailable_exit_2(setup, monkeypatch):
    from kb.cli import app

    cfg, store, root = setup
    monkeypatch.setenv("KB_HOME", str(root / "home"))
    monkeypatch.setattr("kb.cli.get_provider", lambda: FakeProvider(unavailable="not signed in"))
    r = CliRunner().invoke(app, ["analyze", str(root / "kb.yaml"), "--branch", "main"])
    assert r.exit_code == 2
    assert "Claude Code is not available" in r.output and "Traceback" not in r.output


def _gitify(path, branch):
    import subprocess

    def g(*a):
        subprocess.run(["git", *a], cwd=path, check=True, capture_output=True)
    g("init", "-q", "-b", branch)
    g("config", "user.email", "t@t")
    g("config", "user.name", "t")
    g("add", ".")
    g("commit", "-qm", "init")


def test_analyze_records_branches_per_repo(setup):
    cfg, store, root = setup
    _gitify(root / "mini-fe", "master")
    _gitify(root / "mini-be", "develop")
    branches = {"mini-fe": "master", "mini-be": "develop"}
    m = run_analyze(cfg, "production", store, FakeProvider(default=DRAFT), RecordingTracer(), author="me",
                    branches=branches)
    assert m.branches == branches and m.branch == "production"
    assert store.load("mini", "production").manifest.branches == branches
    assert store.find_pack_by_branches("mini", branches) == "production"
    assert store.find_pack_by_branches("mini", {"mini-fe": "master", "mini-be": "main"}) is None


def test_find_pack_by_branches_legacy_manifest(setup):
    cfg, store, root = setup
    run_analyze(cfg, "main", store, FakeProvider(default=DRAFT), RecordingTracer(), author="me")
    assert store.find_pack_by_branches("mini", {"mini-fe": "main", "mini-be": "main"}) == "main"


def test_cli_analyze_per_repo_branches(setup, monkeypatch):
    from kb.cli import app

    cfg, store, root = setup
    _gitify(root / "mini-fe", "master")
    _gitify(root / "mini-be", "develop")
    monkeypatch.setenv("KB_HOME", str(root / "home"))
    monkeypatch.setattr("kb.cli.get_provider", lambda: FakeProvider(default=DRAFT))
    r = CliRunner().invoke(app, ["analyze", str(root / "kb.yaml"), "--branch", "mini-fe=master",
                                 "--branch", "mini-be=develop", "--label", "production"])
    assert r.exit_code == 0, r.output
    assert "mini@production" in r.stdout and "mini-fe=master" in r.stdout
