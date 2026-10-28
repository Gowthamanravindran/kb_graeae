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


class RecordingSpan:
    def __init__(self, tracer, name, parent=None, kind="span", input=None):
        self.tracer, self.name, self.parent, self.kind, self.input = tracer, name, parent, kind, input
        self.output = None
        self.metadata = None
        self.children: list[RecordingSpan] = []
        tracer.all.append(self)

    def end(self, output=None, metadata=None):
        self.output, self.metadata = output, metadata

    def child(self, name, input=None, metadata=None, as_type="span", **attrs):
        c = RecordingSpan(self.tracer, name, self, as_type, input)
        c.metadata, c.attrs = metadata, attrs
        self.children.append(c)
        return c


class RecordingTracer:
    def __init__(self):
        self.all: list[RecordingSpan] = []
        self.stack: list[RecordingSpan] = []
        self.traces: list[tuple[str, dict]] = []
        self.scores: list[tuple[str, float]] = []

    @contextmanager
    def trace(self, name, tags):
        self.traces.append((name, tags))
        with self.span(name) as s:
            yield s

    @contextmanager
    def span(self, name, input=None):
        parent = self.stack[-1] if self.stack else None
        s = RecordingSpan(self, name, parent, input=input)
        if parent:
            parent.children.append(s)
        self.stack.append(s)
        try:
            yield s
        finally:
            self.stack.pop()

    def record_llm(self, parent, result, name, prompt):
        from kb.tracing import record_llm_on

        record_llm_on(parent, result, name, prompt)

    def langgraph_callbacks(self):
        return []

    def score(self, name, value):
        self.scores.append((name, value))

    def flush(self):
        pass

    def names(self):
        return [s.name for s in self.all]
