"""Claude Code as the model, driven headless through the Claude Agent SDK.

Uses the user's own `claude` CLI login. Tools are read-only (Read, Grep, Glob) or none.
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import datetime, timezone

from claude_agent_sdk import (AssistantMessage, ClaudeAgentOptions, ClaudeSDKError, CLINotFoundError, ResultMessage,
                              TextBlock, ToolUseBlock, query)

from kb.errors import LLMCallError, LLMUnavailable
from kb.llm.base import LLMRequest, LLMResult, ToolStep

READ_ONLY = ["Read", "Grep", "Glob"]
_AUTH_HINTS = ("login", "log in", "api key", "authenticat", "unauthorized", "401", "credit balance")
_TRANSIENT_HINTS = ("overloaded", "rate limit", "rate_limit", "timeout", "timed out", "temporarily", "econnreset",
                    "connection", "529", "503", "502", "500")
_TRANSIENT_STATUS = {429, 500, 502, 503, 504, 529}
BACKOFF = (2, 8)  # seconds before the 2nd and 3rd attempt


class _Transient(Exception):
    pass


def _summary(block: ToolUseBlock) -> str:
    i = block.input or {}
    for key in ("file_path", "pattern", "path", "command"):
        if key in i:
            return str(i[key])
    return ",".join(f"{k}={v}" for k, v in list(i.items())[:2])


def _classify(text: str, status: int | None = None) -> Exception:
    """Three kinds: can't use Claude at all / try again later / this one call failed."""
    low = (text or "").lower()
    if any(h in low for h in _AUTH_HINTS):
        return LLMUnavailable(f"not signed in ({text.strip()[:120]})")
    if (status in _TRANSIENT_STATUS) or any(h in low for h in _TRANSIENT_HINTS):
        return _Transient(text.strip()[:200])
    return LLMCallError(text.strip()[:200] or "Claude Code returned an error")


class ClaudeCodeProvider:
    name = "claude-code"

    def __init__(self, model: str | None = None, max_turns: int = 30, sleep=time.sleep) -> None:
        self.model = model
        self.max_turns = max_turns
        self.sleep = sleep
        self._checked = False

    def check(self) -> None:
        if self._checked:
            return
        if shutil.which("claude") is None:
            raise LLMUnavailable("Claude Code CLI not found (install it and run `claude` once)")
        try:
            subprocess.run(["claude", "--version"], check=True, capture_output=True, timeout=30)
        except (subprocess.SubprocessError, OSError) as e:
            raise LLMUnavailable(f"`claude --version` failed: {e}") from e
        self._checked = True

    def run(self, req: LLMRequest) -> LLMResult:
        last = ""
        for attempt in range(len(BACKOFF) + 1):
            if attempt:
                self.sleep(BACKOFF[attempt - 1])
            try:
                return asyncio.run(self._run(req))
            except CLINotFoundError as e:
                raise LLMUnavailable("Claude Code CLI not found") from e
            except _Transient as e:
                last = str(e)
            except ClaudeSDKError as e:
                err = _classify(str(e))
                if not isinstance(err, _Transient):
                    raise err from e
                last = str(err)
        raise LLMCallError(f"Claude Code kept failing after {len(BACKOFF) + 1} attempts: {last}")

    async def _run(self, req: LLMRequest) -> LLMResult:
        tools = READ_ONLY if req.allow_tools else []
        opts = ClaudeAgentOptions(
            system_prompt=req.system, cwd=req.cwd, add_dirs=list(req.extra_dirs), tools=list(tools),
            allowed_tools=list(tools), permission_mode="default", max_turns=self.max_turns, model=self.model,
            setting_sources=[],  # never load the user's repo hooks / CLAUDE.md / MCP config
        )
        steps: list[ToolStep] = []
        texts: list[str] = []
        result: ResultMessage | None = None
        start = last = time.monotonic()
        async for msg in query(prompt=req.prompt, options=opts):
            now = time.monotonic()
            if steps:
                steps[-1].duration_ms = steps[-1].duration_ms or int((now - last) * 1000)
            last = now
            if isinstance(msg, AssistantMessage):
                for b in msg.content:
                    if isinstance(b, ToolUseBlock):
                        steps.append(ToolStep(b.name, _summary(b), datetime.now(timezone.utc), 0))
                    elif isinstance(b, TextBlock):
                        texts.append(b.text)
            elif isinstance(msg, ResultMessage):
                result = msg
        if result is None:
            raise LLMUnavailable("Claude Code ended without a result")
        if result.is_error:
            raise _classify(str(result.result or result.errors or result.subtype), result.api_error_status)
        usage = result.usage or {}
        return LLMResult(
            text=result.result if result.result is not None else "\n".join(texts),
            steps=steps,
            input_tokens=sum(int(usage.get(k, 0) or 0) for k in (
                "input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")),
            output_tokens=int(usage.get("output_tokens", 0)),
            cost_usd=result.total_cost_usd,
            duration_ms=result.duration_ms or int((time.monotonic() - start) * 1000),
        )
