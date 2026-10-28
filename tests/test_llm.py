import asyncio

import pytest
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock

from kb.errors import LLMUnavailable
from kb.llm.base import LLMRequest
from kb.llm.claude_code import ClaudeCodeProvider

from fakes import FakeProvider


def test_provider_unavailable_raises_llm_unavailable(tmp_path):
    with pytest.raises(LLMUnavailable, match="not signed in"):
        FakeProvider(unavailable="not signed in").run(LLMRequest(prompt="x", system="", cwd=tmp_path))


def _fake_query(captured):
    async def q(*, prompt, options=None, transport=None):
        captured["options"] = options
        yield AssistantMessage(content=[ToolUseBlock(id="1", name="Read", input={"file_path": "/r/src/a.ts"}),
                                        ToolUseBlock(id="2", name="Grep", input={"pattern": "addTask"})], model="m")
        yield AssistantMessage(content=[TextBlock(text="done")], model="m")
        yield ResultMessage(subtype="success", duration_ms=50, duration_api_ms=40, is_error=False, num_turns=2,
                            session_id="s", total_cost_usd=0.01, usage={"input_tokens": 100, "output_tokens": 20},
                            result="done")
    return q


def test_claude_provider_maps_tool_blocks(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr("kb.llm.claude_code.query", _fake_query(captured))
    r = ClaudeCodeProvider().run(LLMRequest(prompt="p", system="s", cwd=tmp_path, extra_dirs=[tmp_path / "b"], allow_tools=True))
    assert [(s.tool, s.input_summary) for s in r.steps] == [("Read", "/r/src/a.ts"), ("Grep", "addTask")]
    assert (r.text, r.input_tokens, r.output_tokens, r.cost_usd) == ("done", 100, 20, 0.01)
    opts = captured["options"]
    assert opts.tools == ["Read", "Grep", "Glob"] and opts.allowed_tools == ["Read", "Grep", "Glob"]
    assert opts.add_dirs == [tmp_path / "b"] and opts.cwd == tmp_path


def test_claude_provider_without_tools_disables_all(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr("kb.llm.claude_code.query", _fake_query(captured))
    ClaudeCodeProvider().run(LLMRequest(prompt="p", system="s", cwd=tmp_path))
    assert captured["options"].tools == [] and captured["options"].allowed_tools == []


def test_claude_provider_error_result_raises(tmp_path, monkeypatch):
    async def q(*, prompt, options=None, transport=None):
        yield ResultMessage(subtype="error", duration_ms=1, duration_api_ms=1, is_error=True, num_turns=1,
                            session_id="s", result="Invalid API key · Please run /login")
    monkeypatch.setattr("kb.llm.claude_code.query", q)
    with pytest.raises(LLMUnavailable, match="not signed in"):
        ClaudeCodeProvider().run(LLMRequest(prompt="p", system="s", cwd=tmp_path))


def test_check_without_cli(monkeypatch):
    monkeypatch.setattr("kb.llm.claude_code.shutil.which", lambda _: None)
    with pytest.raises(LLMUnavailable, match="not found"):
        ClaudeCodeProvider().check()


def _result(**kw):
    base = dict(subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s",
                result="ok", usage={"input_tokens": 1, "output_tokens": 1})
    return ResultMessage(**{**base, **kw})


def _seq(results):
    calls = {"n": 0}

    async def q(*, prompt, options=None, transport=None):
        r = results[min(calls["n"], len(results) - 1)]
        calls["n"] += 1
        if isinstance(r, Exception):
            raise r
        yield r
    return q, calls


def test_max_turns_is_a_per_call_failure_not_unavailable(tmp_path, monkeypatch):
    from kb.errors import LLMCallError, LLMOutputError

    q, _ = _seq([_result(subtype="error_max_turns", is_error=True, result=None)])
    monkeypatch.setattr("kb.llm.claude_code.query", q)
    with pytest.raises(LLMCallError) as e:
        ClaudeCodeProvider(sleep=lambda s: None).run(LLMRequest(prompt="p", system="s", cwd=tmp_path))
    assert isinstance(e.value, LLMOutputError) and not isinstance(e.value, LLMUnavailable)


def test_transient_error_retried_with_backoff(tmp_path, monkeypatch):
    q, calls = _seq([_result(is_error=True, subtype="error", result="API Error: 529 overloaded", api_error_status=529),
                     _result(result="fine")])
    monkeypatch.setattr("kb.llm.claude_code.query", q)
    slept = []
    r = ClaudeCodeProvider(sleep=slept.append).run(LLMRequest(prompt="p", system="s", cwd=tmp_path))
    assert r.text == "fine" and calls["n"] == 2 and slept == [2]


def test_persistent_transient_error_becomes_call_error(tmp_path, monkeypatch):
    from kb.errors import LLMCallError

    q, calls = _seq([_result(is_error=True, subtype="error", result="rate limit exceeded", api_error_status=429)])
    monkeypatch.setattr("kb.llm.claude_code.query", q)
    slept = []
    with pytest.raises(LLMCallError):
        ClaudeCodeProvider(sleep=slept.append).run(LLMRequest(prompt="p", system="s", cwd=tmp_path))
    assert calls["n"] == 3 and slept == [2, 8]


def test_settings_from_user_repo_not_loaded(tmp_path, monkeypatch):
    captured = {}
    monkeypatch.setattr("kb.llm.claude_code.query", _fake_query(captured))
    ClaudeCodeProvider().run(LLMRequest(prompt="p", system="s", cwd=tmp_path))
    assert captured["options"].setting_sources == []
