from __future__ import annotations

from typing import Literal, TypedDict

from kb.packs.model import FlowStep


class AgentState(TypedDict, total=False):
    question: str
    seed_qa_id: str | None
    kind: Literal["how", "where", "impact", "diagram"] | None
    module_ids: list[str]
    reused_flow_id: str | None
    shortlist: list[str]
    draft_steps: list[dict]
    steps: list[FlowStep]
    answer: str
    mermaid: str
    not_found: bool
    no_source: bool
    saved_qa_id: str | None
    suggestions: list[str]
    no_reuse: bool
