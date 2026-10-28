"""Counts what an answer cost (Claude calls, tool steps, files read) while passing everything to the real tracer."""

from __future__ import annotations

from contextlib import contextmanager


class StatsTracer:
    def __init__(self, inner) -> None:
        self.inner = inner
        self.claude_calls = 0
        self.tool_steps = 0
        self.files_read = 0
        self.trace_id: str | None = None

    @contextmanager
    def trace(self, name, tags):
        with self.inner.trace(name, tags) as span:
            client = getattr(self.inner, "client", None)
            if client is not None:
                try:
                    self.trace_id = client.get_current_trace_id()
                except Exception:  # tracing must never break an answer
                    self.trace_id = None
            yield span

    def span(self, name, input=None):
        return self.inner.span(name, input)

    def record_llm(self, parent, result, name, prompt):
        if result is not None:
            self.claude_calls += 1
            self.tool_steps += len(result.steps)
            self.files_read += sum(1 for s in result.steps if s.tool == "Read")
        self.inner.record_llm(parent, result, name, prompt)

    def langgraph_callbacks(self):
        return self.inner.langgraph_callbacks()

    def score(self, name, value):
        self.inner.score(name, value)

    def flush(self):
        self.inner.flush()
