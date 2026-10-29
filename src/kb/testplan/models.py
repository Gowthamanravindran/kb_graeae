"""Records for test planning (spec §4). Stored as JSON in the base analysis's overlay."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

from kb.packs.model import Citation

Priority = Literal["P0", "P1", "P2", "P3"]
PRIORITIES: tuple[str, ...] = ("P0", "P1", "P2", "P3")
ScenarioType = Literal["happy_path", "branches_edges", "errors_failures", "regression", "cross_side"]
InventoryKind = Literal["branch", "validation", "permission", "error", "field", "argument", "route", "screen",
                        "job", "query", "removed", "code"]
SlotStatus = Literal["planned", "reserved", "written", "stale", "failed", "obsolete"]


def now() -> datetime:
    return datetime.now(timezone.utc)


class BranchRef(BaseModel):
    branch: str
    commit: str


class PrInfo(BaseModel):
    url: str
    title: str = ""
    body: str = ""
    issues: list[str] = Field(default_factory=list)


class ChangeSet(BaseModel):
    id: str
    kind: Literal["pr", "scenario"] = "pr"
    project: str
    title: str = ""
    base: dict[str, BranchRef]
    head: dict[str, BranchRef]  # head == base for an unchanged repo
    base_pack: str
    prs: dict[str, PrInfo] = Field(default_factory=dict)
    scenario_text: str | None = None
    answer_id: str | None = None
    created_by: str = "unknown"
    created_at: datetime = Field(default_factory=now)


class InventoryItem(BaseModel):
    id: str
    kind: InventoryKind
    change: Literal["added", "changed", "removed"]
    repo: str
    file: str
    lines: tuple[int, int]
    function: str
    summary: str
    outcomes: list[str] = Field(default_factory=list)
    text: str = ""


class ImpactFlow(BaseModel):
    id: str
    name: str
    entry_points: list[str]
    changed_functions: list[str]
    inventory_ids: list[str]
    modules: list[str] = Field(default_factory=list)
    master_flow_id: str | None = None
    risk: float = 0.0
    risk_reasons: list[str] = Field(default_factory=list)
    est_claude_calls: int = 0
    path: list[str] = Field(default_factory=list)
    crosses_repos: bool = False
    has_job: bool = False
    base_flow_id: str | None = None
    head_flow_id: str | None = None


class ImpactReport(BaseModel):
    change_set_id: str
    head_commits: dict[str, str]
    graph_delta: dict = Field(default_factory=dict)
    inventory: list[InventoryItem] = Field(default_factory=list)
    flows: list[ImpactFlow] = Field(default_factory=list)
    needs_full_reanalysis: bool = False
    full_reanalysis_reason: str = ""
    created_by: str = "unknown"
    created_at: datetime = Field(default_factory=now)


class CaseSlot(BaseModel):
    id: str
    flow_id: str
    scenario_type: ScenarioType
    inventory_ids: list[str] = Field(default_factory=list)
    outcome: str = ""
    priority: Priority = "P2"
    rule: str = ""
    status: SlotStatus = "planned"
    reserved_by: str | None = None
    reserved_until: datetime | None = None
    assigned_to: str | None = None
    case_id: str | None = None
    fail_reason: str | None = None


class Scenario(BaseModel):
    id: str
    flow_id: str
    type: ScenarioType
    title: str
    description: str = ""
    inventory_ids: list[str] = Field(default_factory=list)
    step_refs: list[int] = Field(default_factory=list)
    author: str = "unknown"


class TestCase(BaseModel):
    __test__ = False  # not a pytest class
    id: str
    slot_id: str | None = None
    suggested: bool = False
    status: Literal["written", "stale", "obsolete", "suggested", "dismissed"] = "written"
    scenario_id: str | None = None
    flow_id: str = ""
    title: str
    priority: Priority = "P2"
    preconditions: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    test_data: list[str] = Field(default_factory=list)
    expected: list[str] = Field(default_factory=list)
    code_refs: list[Citation] = Field(default_factory=list)
    author: str = "unknown"
    created_at: datetime = Field(default_factory=now)
    stale: bool = False
