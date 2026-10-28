"""Tracing behind a small interface: Langfuse (self-hosted) or nothing.

A trace per `kb analyze` / `kb ask`; a span per step / LangGraph node; each Claude Code
call is a generation whose child "tool" observations are Claude's own steps (Read, Grep, ...).
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Protocol

try:
    from langfuse import Langfuse, propagate_attributes
except ImportError:  # pragma: no cover
    Langfuse = None  # type: ignore[assignment]
    propagate_attributes = None  # type: ignore[assignment]

from kb.config import ProjectConfig


class Span(Protocol):
    def end(self, output: Any = None, metadata: dict | None = None) -> None: ...

    def child(self, name: str, input: Any = None, metadata: dict | None = None, as_type: str = "span",
              **attrs: Any) -> "Span": ...


class Tracer(Protocol):
    def trace(self, name: str, tags: dict[str, str]): ...

    def span(self, name: str, input: Any = None): ...

    def record_llm(self, parent: Span | None, result, name: str, prompt: str) -> None: ...

    def langgraph_callbacks(self) -> list: ...

    def score(self, name: str, value: float) -> None: ...

    def flush(self) -> None: ...


def record_llm_on(parent: Span | None, result, name: str, prompt: str) -> None:
    """One generation under `parent`, with a child observation per Claude tool step."""
    if parent is None or result is None:
        return
    gen = parent.child(
        name, input=prompt, as_type="generation", output=result.text, model=name,
        usage_details={"input": result.input_tokens, "output": result.output_tokens},
        cost_details={"total": result.cost_usd} if result.cost_usd is not None else None,
        metadata={"duration_ms": result.duration_ms},
    )
    for s in result.steps:
        gen.child(f"{s.tool} {s.input_summary}", as_type="tool",
                  metadata={"started_at": s.started_at.isoformat(), "duration_ms": s.duration_ms}).end()
    gen.end(output=result.text)


class _NoopSpan:
    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        pass

    def child(self, name: str, input: Any = None, metadata: dict | None = None, as_type: str = "span", **attrs):
        return self


class NoopTracer:
    @contextmanager
    def trace(self, name: str, tags: dict[str, str]) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    @contextmanager
    def span(self, name: str, input: Any = None) -> Iterator[_NoopSpan]:
        yield _NoopSpan()

    def record_llm(self, parent, result, name: str, prompt: str) -> None:
        pass

    def langgraph_callbacks(self) -> list:
        return []

    def score(self, name: str, value: float) -> None:
        pass

    def flush(self) -> None:
        pass


class _LangfuseSpan:
    def __init__(self, obs) -> None:
        self._obs = obs

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        if output is not None or metadata is not None:
            self._obs.update(output=output, metadata=metadata)
        self._obs.end()

    def child(self, name: str, input: Any = None, metadata: dict | None = None, as_type: str = "span", **attrs):
        attrs = {k: v for k, v in attrs.items() if v is not None}
        return _LangfuseSpan(self._obs.start_observation(name=name, as_type=as_type, input=input, metadata=metadata, **attrs))

    def update(self, **kw) -> None:
        self._obs.update(**kw)


class _CurrentSpan(_LangfuseSpan):
    """A span opened as the current OTel context; ended by its context manager."""

    def end(self, output: Any = None, metadata: dict | None = None) -> None:
        if output is not None or metadata is not None:
            self._obs.update(output=output, metadata=metadata)


class LangfuseTracer:
    def __init__(self, host: str) -> None:
        self.client = Langfuse(public_key=os.environ["LANGFUSE_PUBLIC_KEY"],
                               secret_key=os.environ["LANGFUSE_SECRET_KEY"], host=host)

    @contextmanager
    def trace(self, name: str, tags: dict[str, str]):
        tag_list = [f"{k}:{v}" for k, v in tags.items()]
        with propagate_attributes(trace_name=name, tags=tag_list, metadata=dict(tags)):
            with self.client.start_as_current_observation(name=name, as_type="agent") as obs:
                yield _CurrentSpan(obs)

    @contextmanager
    def span(self, name: str, input: Any = None):
        with self.client.start_as_current_observation(name=name, as_type="span", input=input) as obs:
            yield _CurrentSpan(obs)

    def record_llm(self, parent, result, name: str, prompt: str) -> None:
        record_llm_on(parent, result, name, prompt)

    def langgraph_callbacks(self) -> list:
        from langfuse.langchain import CallbackHandler

        return [CallbackHandler()]

    def score(self, name: str, value: float) -> None:
        self.client.score_current_trace(name=name, value=value)

    def flush(self) -> None:
        self.client.flush()


def make_tracer(cfg: ProjectConfig) -> Tracer:
    host = cfg.langfuse or os.environ.get("LANGFUSE_HOST")
    if host and os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY") and Langfuse:
        return LangfuseTracer(host)
    return NoopTracer()
