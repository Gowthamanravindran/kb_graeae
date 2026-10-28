from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone

from kb.errors import LLMUnavailable
from kb.llm.base import LLMRequest, LLMResult, ToolStep


class FakeProvider:
    name = "fake"

    def __init__(self, responses: list[str] | None = None, steps: list[ToolStep] | None = None,
                 unavailable: str | None = None, default: str | None = None):
        self.responses = list(responses or [])
        self.steps = steps or []
        self.unavailable = unavailable
        self.default = default
        self.requests: list[LLMRequest] = []

    def check(self) -> None:
        if self.unavailable:
            raise LLMUnavailable(self.unavailable)

    def run(self, req: LLMRequest) -> LLMResult:
        self.check()
        self.requests.append(req)
        if self.responses:
            text = self.responses.pop(0)
        elif self.default is not None:
            text = self.default
        else:
            raise AssertionError(f"FakeProvider ran out of responses for: {req.prompt[:80]}")
        return LLMResult(text=text, steps=list(self.steps), input_tokens=10, output_tokens=5, cost_usd=0.0, duration_ms=1)


def step(tool: str, summary: str) -> ToolStep:
    return ToolStep(tool=tool, input_summary=summary, started_at=datetime.now(timezone.utc), duration_ms=1)
