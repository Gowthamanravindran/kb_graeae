from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Protocol


@dataclass
class ToolStep:
    tool: str
    input_summary: str
    started_at: datetime
    duration_ms: int


@dataclass
class LLMRequest:
    prompt: str
    system: str
    cwd: Path
    extra_dirs: list[Path] = field(default_factory=list)
    allow_tools: bool = False


@dataclass
class LLMResult:
    text: str
    steps: list[ToolStep]
    input_tokens: int
    output_tokens: int
    cost_usd: float | None
    duration_ms: int


class LLMProvider(Protocol):
    name: str

    def check(self) -> None:
        """Raise LLMUnavailable(reason) if the provider cannot be used."""

    def run(self, req: LLMRequest) -> LLMResult: ...
