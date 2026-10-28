from __future__ import annotations

import json
import re
from dataclasses import replace
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from kb.errors import LLMOutputError
from kb.llm.base import LLMProvider, LLMRequest

T = TypeVar("T", bound=BaseModel)
_BLOCK = re.compile(r"```json\s*(.*?)```", re.S)


def _candidates(text: str) -> list[str]:
    """Plausible JSON spans, most likely first. Fences may also appear inside JSON strings."""
    out = [b.strip() for b in reversed(_BLOCK.findall(text))]
    start = text.find("```json")
    if start != -1 and text.rfind("```") > start:
        out.insert(0, text[start + 7:text.rfind("```")].strip())
    a, b = text.find("{"), text.rfind("}")
    if a != -1 and b > a:
        out.append(text[a:b + 1])
    return out or [text]


def ask_json(provider: LLMProvider, req: LLMRequest, model: type[T], tracer, span_name: str) -> T:
    """Ask for one JSON object matching `model`; retry once on a parse error, then LLMOutputError."""
    schema = json.dumps(model.model_json_schema())
    prompt = f"{req.prompt}\n\nReply with one JSON object in a ```json block matching this schema:\n{schema}"
    last_err = ""
    with tracer.span(span_name, input={"prompt": req.prompt[:2000]}) as span:
        for attempt in range(2):
            p = prompt if attempt == 0 else (
                f"{prompt}\n\nYour previous reply could not be parsed: {last_err}\nReply again with only the ```json block.")
            result = provider.run(replace(req, prompt=p))
            tracer.record_llm(span, result, "claude-code", p)
            for cand in _candidates(result.text):
                try:
                    out = model.model_validate_json(cand)
                    span.end(output=out.model_dump())
                    return out
                except (ValidationError, ValueError) as e:
                    last_err = str(e).splitlines()[0][:300]
        span.end(metadata={"error": last_err})
    raise LLMOutputError(f"{span_name}: reply was not valid JSON after a retry ({last_err})")
