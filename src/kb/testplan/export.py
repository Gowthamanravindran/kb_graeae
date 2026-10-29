"""CSV export of a test plan for Excel: one row per case, UTF-8 with a BOM, list cells numbered."""

from __future__ import annotations

import csv
import io

from kb.testplan.models import PRIORITIES
from kb.testplan.store import PlanStore

COLUMNS = ["ID", "Flow", "Scenario", "Type", "Priority", "Title", "Preconditions", "Steps", "Test data", "Expected",
           "Status", "Author", "Stale"]
TYPE_LABEL = {"happy_path": "Happy path", "branches_edges": "Branches and edges", "errors_failures": "Errors and failures",
              "regression": "Regression", "cross_side": "Cross-side effects"}


def _safe(cell: str) -> str:
    """Excel runs a cell starting with = + - @ (or a tab/CR) as a formula: text from Claude or a PR must not."""
    return "'" + cell if cell and cell[0] in "=+-@\t\r" else cell


def _numbered(items: list[str]) -> str:
    return "\n".join(f"{i}. {x}" for i, x in enumerate(items, 1))


def export_csv(ps: PlanStore, project: str, label: str, plan_id: str, priorities: list[str] | None = None,
               flow_ids: list[str] | None = None, statuses: list[str] | None = None) -> bytes:
    impact = ps.load_impact(project, label, plan_id)
    flows = {f.id: f.name for f in (impact.flows if impact else [])}
    scen = {s.id: s for s in ps.scenarios(project, label, plan_id)}
    slots = {s.id: s for s in ps.load_slots(project, label, plan_id)}
    rows = []
    for c in ps.cases(project, label, plan_id):
        if c.suggested or c.status not in ("written", "stale"):
            continue
        status = "stale" if c.stale or c.status == "stale" else "written"
        if priorities and c.priority not in priorities:
            continue
        if flow_ids and c.flow_id not in flow_ids:
            continue
        if statuses and status not in statuses:
            continue
        sc = scen.get(c.scenario_id or "")
        typ = sc.type if sc else (slots[c.slot_id].scenario_type if c.slot_id in slots else "")
        rows.append([c.id, flows.get(c.flow_id, ""), sc.title if sc else "", TYPE_LABEL.get(typ, typ), c.priority, c.title,
                     _numbered(c.preconditions), _numbered(c.steps), _numbered(c.test_data), _numbered(c.expected),
                     status.capitalize(), c.author, "Yes" if status == "stale" else "No"])
    rows.sort(key=lambda r: (PRIORITIES.index(r[4]) if r[4] in PRIORITIES else 9, r[1], len(r[0]), r[0]))
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow(COLUMNS)
    w.writerows([[_safe(str(c)) for c in r] for r in rows])
    return ("﻿" + buf.getvalue()).encode("utf-8")
