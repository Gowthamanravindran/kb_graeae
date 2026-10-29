"""Case slots: the cases that *will* be written, planned from the inventory without Claude, so the number
per priority is exact before anything is generated (spec §6)."""

from __future__ import annotations

import hashlib
import math
import re
from typing import Literal

from kb.testplan.models import PRIORITIES, CaseSlot, ImpactFlow, InventoryItem

MONEY = re.compile(r"delete|remove|destroy|payment|refund|invoice|charge", re.I)
MAX_REGRESSION = 3
CASES_PER_CALL = 15

# kind -> list of (outcome or None for "use item outcomes", scenario type, base priority, rule)
_RULES = {
    "permission": [("allowed", "branches_edges", "P0"), ("denied", "errors_failures", "P0")],
    "validation": [("valid", "branches_edges", "P2"), ("invalid", "errors_failures", "P2")],
    "error": [("error", "errors_failures", "P2")],
    "removed": [("gone", "branches_edges", "P0")],
}


def _sid(flow_id: str, typ: str, inv: str | None, outcome: str) -> str:
    return hashlib.sha1(f"{flow_id}|{typ}|{inv or '-'}|{outcome}".encode()).hexdigest()[:12]


def risk(flow: ImpactFlow, inventory: list[InventoryItem]) -> tuple[float, list[str]]:
    items = [i for i in inventory if i.id in set(flow.inventory_ids)]
    removed = any(i.kind == "removed" or i.change == "removed" for i in items)
    perm = any(i.kind == "permission" for i in items)
    terms = [(3, removed, "removed behaviour"), (3, perm, "permission change"), (2, flow.crosses_repos, "crosses repos"),
             (2, flow.has_job, "background job"), (1, flow.master_flow_id is None, "no master flow")]
    score = sum(w for w, on, _ in terms if on) + 0.5 * len(items)
    return score, [r for _, on, r in terms if on]


def _unchanged_runs(flow: ImpactFlow) -> list[list[str]]:
    changed = set(flow.changed_functions)
    changed_files = {c.split("#", 1)[0] for c in changed}
    runs, cur = [], []
    for p in flow.path:
        is_changed = p in changed or p in changed_files or p.split("#", 1)[0] in changed_files
        if is_changed:
            if cur:
                runs.append(cur)
            cur = []
        else:
            cur.append(p)
    if cur:
        runs.append(cur)
    return runs


def plan_slots(flows: list[ImpactFlow], inventory: list[InventoryItem], mode: Literal["pr", "scenario"]) -> list[CaseSlot]:
    by_id = {i.id: i for i in inventory}
    slots: list[CaseSlot] = []

    def add(flow, typ, prio, rule, inv=None, outcome=""):
        slots.append(CaseSlot(id=_sid(flow.id, typ, inv, outcome or rule), flow_id=flow.id, scenario_type=typ,
                              inventory_ids=[inv] if inv else [], outcome=outcome, priority=prio, rule=rule))

    for f in flows:
        add(f, "happy_path", "P1", "happy path")
        for iid in f.inventory_ids:
            it = by_id.get(iid)
            if it is None:
                continue
            if it.kind in _RULES:
                for outcome, typ, prio in _RULES[it.kind]:
                    add(f, typ, prio, it.kind, iid, outcome)
            elif it.kind == "branch":
                for outcome in it.outcomes or ["true", "false"]:
                    add(f, "branches_edges", "P1", "branch", iid, outcome)
            else:  # field, argument, route, screen, job, query, code
                add(f, "branches_edges", "P1", it.kind, iid, it.change)
        if mode == "pr":
            for run in _unchanged_runs(f)[:MAX_REGRESSION]:
                add(f, "regression", "P3", "regression", None, run[0])
        if f.crosses_repos or f.has_job:
            add(f, "cross_side", "P3", "cross side")
    return slots


def assign_priorities(slots: list[CaseSlot], flows: list[ImpactFlow], inventory: list[InventoryItem] | None = None) -> None:
    """Spec §6.3: the happy path is P0 on the riskiest third of flows; money/deletion flows lift P1 to P0."""
    ranked = sorted(flows, key=lambda f: (-f.risk, f.id))
    top = {f.id for f in ranked[:math.ceil(len(ranked) / 3)]} if ranked else set()
    money = {f.id for f in flows if any(MONEY.search(c) for c in f.changed_functions)}
    for s in slots:
        if s.rule == "happy path":
            s.priority = "P0" if s.flow_id in top else "P1"
        if s.priority == "P1" and s.flow_id in money:
            s.priority = "P0"


def select(slots: list[CaseSlot], flows: list[ImpactFlow], counts: dict[str, int], per_flow_cap: int | None,
           statuses: tuple[str, ...] = ("planned",)) -> list[CaseSlot]:
    risk_of = {f.id: f.risk for f in flows}
    order = sorted((s for s in slots if s.status in statuses),
                   key=lambda s: (PRIORITIES.index(s.priority), -risk_of.get(s.flow_id, 0.0), s.id))
    left = {p: max(0, int(counts.get(p, 0))) for p in PRIORITIES}
    per_flow: dict[str, int] = {}
    chosen = []
    for s in order:
        if left[s.priority] <= 0:
            continue
        if per_flow_cap is not None and per_flow.get(s.flow_id, 0) >= per_flow_cap:
            continue
        chosen.append(s)
        left[s.priority] -= 1
        per_flow[s.flow_id] = per_flow.get(s.flow_id, 0) + 1
    return chosen


def estimate_calls(selected: list[CaseSlot], flows: list[ImpactFlow], have_trace: set[str],
                   have_scenarios: set[tuple[str, str]], mode: str = "pr") -> int:
    """Spec §8: per flow, base trace (pr, no fresh master flow) + head trace + scenarios + ceil(slots/15)."""
    by_flow: dict[str, list[CaseSlot]] = {}
    for s in selected:
        by_flow.setdefault(s.flow_id, []).append(s)
    fl = {f.id: f for f in flows}
    total = 0
    for fid, ss in by_flow.items():
        f = fl.get(fid)
        if mode == "pr" and f is not None and f.master_flow_id is None and f"{fid}:base" not in have_trace:
            total += 1
        if f"{fid}:head" not in have_trace:
            total += 1
        if any((fid, s.scenario_type) not in have_scenarios for s in ss):
            total += 1
        total += math.ceil(len(ss) / CASES_PER_CALL)
    return total
