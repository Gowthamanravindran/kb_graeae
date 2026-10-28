import json

from typer.testing import CliRunner

from kb.cli import app

from agent_helpers import FIX, GOOD_STEPS, LEARN, build_pack, trace_reply
from fakes import FakeProvider

Q = "How does scheduling of repeating tasks work?"


def setup(tmp_path, monkeypatch, provider, repos=("mini-fe", "mini-be")):
    store, _ = build_pack(tmp_path)
    y = tmp_path / "kb.yaml"
    lines = ["project: mini", "repos:"]
    for r in ("mini-fe", "mini-be"):
        path = FIX / r if r in repos else tmp_path / "missing" / r
        lines.append(f"  - {{name: {r}, role: x, local_path: {path}}}")
    y.write_text("\n".join(lines) + "\n")
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("kb.cli.get_provider", lambda: provider)
    return store, y


def test_ask_prints_flows_and_saves(tmp_path, monkeypatch):
    store, y = setup(tmp_path, monkeypatch, FakeProvider([trace_reply(GOOD_STEPS), LEARN]))
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    assert r.exit_code == 0, r.output
    out = r.stdout
    assert "UI flow" in out and "Backend flow" in out
    assert "mini-be:src/module/tasks/task/resolvers.ts:6-15" in out
    assert "```mermaid" in out and "Saved as" in out and "How are repeats ended?" in out
    assert len(store.load("mini", "main").qa) == 1


def test_ask_json_output(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider([trace_reply(GOOD_STEPS), LEARN]))
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", "--json", Q])
    d = json.loads(r.stdout)
    assert d["saved_qa_id"] and d["steps"][0]["citation"]["repo"] == "mini-fe"


def test_ask_without_pack_errors_cleanly(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider())
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "other", Q])
    assert r.exit_code == 1 and "Run kb analyze first" in r.output and "Traceback" not in r.output


def test_ask_llm_unavailable_exit_2(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider(unavailable="not signed in"))
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    assert r.exit_code == 2 and "Claude Code is not available" in r.output


def test_ask_not_found_needs_no_llm(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider(unavailable="not signed in"))
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", "How does billing reconciliation work?"])
    assert r.exit_code == 0 and "couldn't find code" in r.stdout


def test_ask_from_seeds_context(tmp_path, monkeypatch):
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN, trace_reply(GOOD_STEPS, "Follow-up answer."), LEARN])
    store, y = setup(tmp_path, monkeypatch, p)
    CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    qa_id = store.load("mini", "main").qa[0].id
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", "--from", qa_id, "What ends the repeats?"])
    assert r.exit_code == 0, r.output
    assert "Earlier answer: Admins pick repeat" in p.requests[2].prompt


def test_ask_from_unknown_id(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider())
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", "--from", "nope", "x"])
    assert r.exit_code == 1 and "nope" in r.output


def test_missing_repo_checkout_means_no_source(tmp_path, monkeypatch):
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN])
    _, y = setup(tmp_path, monkeypatch, p, repos=("mini-fe",))
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    assert r.exit_code == 0, r.output
    assert "mini-be" not in [str(d) for d in p.requests[0].extra_dirs]
    assert "unverified" in r.stdout


def test_memory_list_shows_private_item(tmp_path, monkeypatch):
    _, y = setup(tmp_path, monkeypatch, FakeProvider([trace_reply(GOOD_STEPS), LEARN]))
    CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    r = CliRunner().invoke(app, ["memory", "list", str(y), "--branch", "main"])
    assert r.exit_code == 0 and "private" in r.stdout and Q in r.stdout


def test_context_doc_repo_without_checkout_does_not_crash(tmp_path, monkeypatch):
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN])
    _, y = setup(tmp_path, monkeypatch, p, repos=("mini-fe",))
    y.write_text(y.read_text() + "context_docs: ['mini-be:CONTEXT.md']\n")
    r = CliRunner().invoke(app, ["ask", str(y), "--branch", "main", Q])
    assert r.exit_code == 0, r.output
