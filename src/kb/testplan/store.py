"""Test plans on disk: <pack>/overlay/testplans/<plan_id>/ of the base analysis (or the analysed branch)."""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from kb.errors import KbError
from kb.packs.store import PackStore, _overlay_lock, _write_json
from kb.testplan.models import CaseSlot, ChangeSet, ImpactReport, Scenario, TestCase


class PlanSummary(BaseModel):
    id: str
    kind: str
    title: str
    label: str
    base: dict[str, str]
    head: dict[str, str]
    counts_by_status: dict[str, int]
    counts_by_priority: dict[str, int]
    cases_written: int
    updated_at: datetime | None = None
    has_impact: bool = False


class PlanStore:
    def __init__(self, packs: PackStore) -> None:
        self.packs = packs

    def dir(self, project: str, label: str, plan_id: str) -> Path:
        from kb_registry.paths import is_safe_id

        if not is_safe_id(plan_id):
            raise KbError(f"unsafe plan id: {plan_id!r}")
        return self.packs.pack_dir(project, label) / "overlay" / "testplans" / plan_id

    # ---- change set / impact
    def save_change_set(self, label: str, cs: ChangeSet) -> None:
        _write_json(self.dir(cs.project, label, cs.id) / "changeset.json", cs.model_dump(mode="json"))

    def load_change_set(self, project: str, label: str, plan_id: str) -> ChangeSet:
        p = self.dir(project, label, plan_id) / "changeset.json"
        if not p.exists():
            raise KbError(f"no test plan {plan_id} on {project}@{label}")
        return ChangeSet.model_validate_json(p.read_text())

    def save_impact(self, project: str, label: str, report: ImpactReport) -> None:
        _write_json(self.dir(project, label, report.change_set_id) / "impact.json", report.model_dump(mode="json"))

    def load_impact(self, project: str, label: str, plan_id: str) -> ImpactReport | None:
        p = self.dir(project, label, plan_id) / "impact.json"
        return ImpactReport.model_validate_json(p.read_text()) if p.exists() else None

    # ---- slots
    def save_slots(self, project: str, label: str, plan_id: str, slots: list[CaseSlot]) -> None:
        p = self.dir(project, label, plan_id) / "slots.json"
        with _overlay_lock(p):
            _write_json(p, [s.model_dump(mode="json") for s in slots])

    def load_slots(self, project: str, label: str, plan_id: str) -> list[CaseSlot]:
        p = self.dir(project, label, plan_id) / "slots.json"
        return [CaseSlot.model_validate(x) for x in json.loads(p.read_text())] if p.exists() else []

    def update_slots(self, project: str, label: str, plan_id: str, fn) -> list[CaseSlot]:
        """Read-modify-write under the overlay lock; `fn(slots)` mutates in place."""
        p = self.dir(project, label, plan_id) / "slots.json"
        with _overlay_lock(p):
            slots = self.load_slots(project, label, plan_id)
            fn(slots)
            _write_json(p, [s.model_dump(mode="json") for s in slots])
        return slots

    # ---- scenarios / cases
    def save_scenario(self, project: str, label: str, plan_id: str, sc: Scenario) -> None:
        _write_json(self.dir(project, label, plan_id) / "scenarios" / f"{sc.id}.json", sc.model_dump(mode="json"))

    def scenarios(self, project: str, label: str, plan_id: str) -> list[Scenario]:
        d = self.dir(project, label, plan_id) / "scenarios"
        return [Scenario.model_validate_json(p.read_text()) for p in sorted(d.glob("*.json"))] if d.exists() else []

    def save_case(self, project: str, label: str, plan_id: str, case: TestCase) -> None:
        _write_json(self.dir(project, label, plan_id) / "cases" / f"{case.id}.json", case.model_dump(mode="json"))

    def delete_case(self, project: str, label: str, plan_id: str, case_id: str) -> None:
        (self.dir(project, label, plan_id) / "cases" / f"{case_id}.json").unlink(missing_ok=True)

    def cases(self, project: str, label: str, plan_id: str) -> list[TestCase]:
        d = self.dir(project, label, plan_id) / "cases"
        out = [TestCase.model_validate_json(p.read_text()) for p in d.glob("*.json")] if d.exists() else []
        return sorted(out, key=lambda c: (len(c.id), c.id))

    def next_case_id(self, project: str, label: str, plan_id: str) -> str:
        p = self.dir(project, label, plan_id) / "counter.json"
        with _overlay_lock(p):
            n = json.loads(p.read_text())["n"] + 1 if p.exists() else 1
            _write_json(p, {"n": n})
        return f"TC-{n:03d}"

    # ---- listing
    def find(self, project: str, plan_id: str) -> str | None:
        """Label of the pack holding this plan."""
        for d in (self.packs.kb_home / "packs").glob(f"{project}@*"):
            if (d / "overlay" / "testplans" / plan_id / "changeset.json").exists():
                return json.loads((d / "manifest.json").read_text())["branch"] if (d / "manifest.json").exists() else None
        return None

    def summary(self, project: str, label: str, plan_id: str) -> PlanSummary | None:
        d = self.dir(project, label, plan_id)
        cs_file = d / "changeset.json"
        if not cs_file.exists():
            return None
        cs = ChangeSet.model_validate_json(cs_file.read_text())
        slots = self.load_slots(project, label, cs.id)
        cases = [c for c in self.cases(project, label, cs.id) if c.status in ("written", "stale")]
        mtimes = [p.stat().st_mtime for p in d.rglob("*.json")]
        return PlanSummary(
            id=cs.id, kind=cs.kind, title=cs.title, label=label,
            base={k: v.branch for k, v in cs.base.items()},
            head={k: v.branch for k, v in cs.head.items()},
            counts_by_status=dict(Counter(s.status for s in slots)),
            counts_by_priority=dict(Counter(s.priority for s in slots)),
            cases_written=len(cases),
            updated_at=datetime.fromtimestamp(max(mtimes)) if mtimes else None,
            has_impact=(d / "impact.json").exists())

    def list_plans(self, project: str) -> list[PlanSummary]:
        out = []
        for d in sorted((self.packs.kb_home / "packs").glob(f"{project}@*")):
            mf = d / "manifest.json"
            if not mf.exists():
                continue
            label = json.loads(mf.read_text())["branch"]
            for cs_file in sorted((d / "overlay" / "testplans").glob("*/changeset.json")):
                row = self.summary(project, label, cs_file.parent.name)
                if row is not None:
                    out.append(row)
        return sorted(out, key=lambda r: r.updated_at or datetime.min, reverse=True)
