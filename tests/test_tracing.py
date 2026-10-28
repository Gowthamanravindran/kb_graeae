import pytest
from pydantic import BaseModel

from kb.config import ProjectConfig
from kb.errors import LLMOutputError
from kb.llm.base import LLMRequest
from kb.llm.json_call import ask_json
from kb.tracing import LangfuseTracer, NoopTracer, make_tracer

from fakes import FakeProvider, RecordingTracer, step


def cfg(langfuse=None):
    return ProjectConfig(project="p", repos=[{"name": "a", "role": "b", "local_path": "/tmp"}], langfuse=langfuse)


def test_make_tracer_noop_without_keys(monkeypatch):
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_SECRET_KEY", raising=False)
    assert isinstance(make_tracer(cfg("http://localhost:3000")), NoopTracer)


def test_make_tracer_noop_without_url(monkeypatch):
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    assert isinstance(make_tracer(cfg(None)), NoopTracer)


def test_make_tracer_langfuse_with_keys(monkeypatch):
    seen = {}

    class FakeLangfuse:
        def __init__(self, **kw):
            seen.update(kw)

    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk")
    monkeypatch.setattr("kb.tracing.Langfuse", FakeLangfuse)
    t = make_tracer(cfg("http://localhost:3000"))
    assert isinstance(t, LangfuseTracer)
    assert seen["host"] == "http://localhost:3000" and seen["public_key"] == "pk"


def test_noop_tracer_is_usable():
    t = NoopTracer()
    with t.trace("ask", {"project": "p"}) as root:
        with t.span("x") as s:
            t.record_llm(s, None, "claude", "p")
        root.child("y").end()
    assert t.langgraph_callbacks() == []


class Out(BaseModel):
    name: str


def test_record_llm_creates_child_per_tool_step(tmp_path):
    tr = RecordingTracer()
    p = FakeProvider(['```json\n{"name": "Tasks"}\n```'], steps=[step("Read", "src/a.ts"), step("Grep", "addTask")])
    ask_json(p, LLMRequest(prompt="q", system="s", cwd=tmp_path), Out, tr, "name_module")
    [span] = [s for s in tr.all if s.name == "name_module"]
    [gen] = span.children
    assert gen.kind == "generation"
    assert [c.name for c in gen.children] == ["Read src/a.ts", "Grep addTask"]


def test_ask_json_parses_block(tmp_path):
    p = FakeProvider(['Sure.\n```json\n{"name": "first"}\n```\nand\n```json\n{"name": "Tasks"}\n```'])
    assert ask_json(p, LLMRequest(prompt="q", system="s", cwd=tmp_path), Out, NoopTracer(), "x").name == "Tasks"
    assert "schema" in p.requests[0].prompt.lower()


def test_ask_json_accepts_bare_json(tmp_path):
    p = FakeProvider(['{"name": "Tasks"}'])
    assert ask_json(p, LLMRequest(prompt="q", system="s", cwd=tmp_path), Out, NoopTracer(), "x").name == "Tasks"


def test_ask_json_retries_once_then_raises(tmp_path):
    p = FakeProvider(["not json", "still not"])
    with pytest.raises(LLMOutputError):
        ask_json(p, LLMRequest(prompt="q", system="s", cwd=tmp_path), Out, NoopTracer(), "x")
    assert len(p.requests) == 2
    assert "could not be parsed" in p.requests[1].prompt


def test_ask_json_retry_succeeds(tmp_path):
    p = FakeProvider(["oops", '```json\n{"name": "ok"}\n```'])
    assert ask_json(p, LLMRequest(prompt="q", system="s", cwd=tmp_path), Out, NoopTracer(), "x").name == "ok"
