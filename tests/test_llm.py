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
