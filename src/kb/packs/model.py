"""Pack contents. Nothing here may hold copied source code (spec 3.4)."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

Visibility = Literal["private", "published"]


def commits_key(commits: dict[str, str]) -> str:
    """Identity of a set of repo commits (same value on client and registry)."""
    import hashlib

    return hashlib.sha256(repr(sorted(commits.items())).encode()).hexdigest()[:12]


class Manifest(BaseModel):
    project: str
    branch: str
    commits: dict[str, str]
    version: int = 1
    parent: int | None = None
    author: str
    created_at: datetime
    status: Literal["incomplete", "complete"]
    stats: dict[str, int] = Field(default_factory=dict)
    failed_modules: list[str] = Field(default_factory=list)
    synced: bool = False  # True once this base came from / went to a registry
    branches: dict[str, str] = Field(default_factory=dict)  # repo -> branch (a branch set; empty = `branch` everywhere)


class Module(BaseModel):
    id: str
    name: str
    summary: str
    members: list[str]
    entry_points: list[str]


class Citation(BaseModel):
    repo: str
    file: str
    line_start: int
    line_end: int
    content_hash: str


class FlowStep(BaseModel):
    side: Literal["ui", "backend"]
    text: str
    citation: Citation | None = None
    verified: bool = False


class Flow(BaseModel):
    id: str
    question: str
    module_ids: list[str]
    steps: list[FlowStep]
    mermaid: str
    created_at: datetime
    visibility: Visibility = "private"


class QAItem(BaseModel):
    id: str
    question: str
    answer: str
    flow_id: str | None = None
    citations: list[Citation] = Field(default_factory=list)
    author: str
    created_at: datetime
    visibility: Visibility = "private"
    stale: bool = False


class Suggestion(BaseModel):
    id: str
    question: str
    source_qa_id: str
