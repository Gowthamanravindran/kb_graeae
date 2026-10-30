"""Stage 5b: test plans shared by a team. The impact, slots, traces, scenarios and cases are stored once per plan;
each QA reserves the slots they will write. Every write to one plan runs under `_plan_lock`, so reservations and
case publishes never interleave."""

from __future__ import annotations

import threading
from collections import Counter
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from kb_registry.api.packs import _latest_base
from kb_registry.auth import current_user, get_session, require_role
from kb_registry.models import PlanItemRow, PlanSlotRow, TestPlanRow, TraceLockRow, User
from kb_registry.paths import is_safe_id

router = APIRouter()
PREFIX = "/projects/{p}/branches/{b:path}/testplans"
SLOT_FIELDS = ("flow_id", "scenario_type", "inventory_ids", "outcome", "priority", "rule")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(t: datetime | None) -> datetime | None:
    return t.replace(tzinfo=timezone.utc) if t is not None and t.tzinfo is None else t  # SQLite drops the zone


# ---------------------------------------------------------------- locking

_sqlite_locks: dict[tuple, threading.Lock] = {}
_sqlite_guard = threading.Lock()


class _PlanLock:
    """Serialises writes to one plan: a row lock on Postgres, a per-plan process lock on SQLite (the registry is
    one process there)."""

    def __init__(self, s: Session, key: tuple):
        self.s, self.key, self.lock = s, key, None

    def __enter__(self):
        if self.s.get_bind().dialect.name == "sqlite":
            with _sqlite_guard:
                self.lock = _sqlite_locks.setdefault(self.key, threading.Lock())
            self.lock.acquire()
        else:
            self.s.query(TestPlanRow).filter_by(project_id=self.key[0], branch=self.key[1], plan_id=self.key[2]) \
                .with_for_update().one_or_none()
        return self

    def __exit__(self, *exc):
        if self.lock is not None:
            self.lock.release()


def _plan_lock(s: Session, project_id: int, branch: str, plan_id: str) -> _PlanLock:
    return _PlanLock(s, (project_id, branch, plan_id))


# ---------------------------------------------------------------- helpers

def _plan(s: Session, project_id: int, b: str, plan_id: str) -> TestPlanRow:
    row = s.get(TestPlanRow, (project_id, b, plan_id))
    if row is None:
        raise HTTPException(404, f"no shared test plan {plan_id} on {b}")
    return row


def _check_id(plan_id: str) -> None:
    if not is_safe_id(plan_id):
        raise HTTPException(400, f"plan id {plan_id!r} is not allowed")


def _emails(s: Session) -> dict[int, str]:
    return {u.id: u.email for u in s.query(User)}


def _slots(s: Session, project_id: int, b: str, plan_id: str) -> list[PlanSlotRow]:
    return s.query(PlanSlotRow).filter_by(project_id=project_id, branch=b, plan_id=plan_id).all()


def _slot_view(r: PlanSlotRow, emails: dict[int, str], now: datetime) -> dict:
    until = _aware(r.reserved_until)
    live = r.status == "reserved" and until is not None and until > now
    return {**r.data, "id": r.slot_id, "status": r.status if (r.status != "reserved" or live) else "planned",
            "reserved_by": emails.get(r.reserved_by) if live else None,
            "reserved_until": until.isoformat() if live else None,
            "assigned_to": emails.get(r.assigned_to), "case_id": r.case_id}


def _view(s: Session, plan: TestPlanRow) -> dict:
    emails, now = _emails(s), _now()
    key = dict(project_id=plan.project_id, branch=plan.branch, plan_id=plan.plan_id)
    slots = sorted(_slots(s, plan.project_id, plan.branch, plan.plan_id), key=lambda r: r.slot_id)
    items = s.query(PlanItemRow).filter_by(**key).order_by(PlanItemRow.published_at).all()
    locks = [lk for lk in s.query(TraceLockRow).filter_by(**key) if _aware(lk.until) > now]
    return {"plan_id": plan.plan_id, "changeset": plan.changeset, "impact": plan.impact,
            "slots": [_slot_view(r, emails, now) for r in slots],
            "items": [{"id": i.item_id, "kind": i.kind, "payload": i.payload, "author": emails.get(i.author_id, ""),
                       "published_at": _aware(i.published_at).isoformat()} for i in items],
            "trace_locks": [{"flow_id": lk.flow_id, "side": lk.side, "holder": emails.get(lk.holder_id),
                             "until": _aware(lk.until).isoformat()} for lk in locks]}


# ---------------------------------------------------------------- share / get / list

class ShareIn(BaseModel):
    changeset: dict
    impact: dict
    slots: list[dict]


@router.put(PREFIX + "/{plan_id}")
def share(p: str, b: str, plan_id: str, body: ShareIn, user: User = Depends(current_user),
          s: Session = Depends(get_session)) -> dict:
    """Create or update a shared plan. Re-sharing (after a new PR commit) keeps each known slot's state, adds new
    slots, and turns slots that disappeared into `obsolete` when someone wrote or holds them (otherwise drops them)."""
    from kb.testplan.models import CaseSlot, ChangeSet, ImpactReport

    proj = require_role(s, user, p, "contributor")
    _check_id(plan_id)
    if _latest_base(s, proj.id, b) is None:
        raise HTTPException(404, f"no analysed base for {p}@{b} — push the analysis first")
    try:
        cs = ChangeSet.model_validate(body.changeset)
        ImpactReport.model_validate(body.impact)
        incoming = [CaseSlot.model_validate(x) for x in body.slots]
    except ValidationError as e:
        raise HTTPException(400, f"invalid test plan: {e.errors()[0]['msg']}") from e
    if cs.id != plan_id:
        raise HTTPException(400, f"changeset id is {cs.id}, not {plan_id}")
    plan = s.get(TestPlanRow, (proj.id, b, plan_id))
    if plan is None:
        plan = TestPlanRow(project_id=proj.id, branch=b, plan_id=plan_id, changeset=body.changeset,
                           impact=body.impact, created_by=user.id, updated_at=_now())
        s.add(plan)
        s.flush()
    with _plan_lock(s, proj.id, b, plan_id):
        plan.changeset, plan.impact, plan.updated_at = body.changeset, body.impact, _now()
        have = {r.slot_id: r for r in _slots(s, proj.id, b, plan_id)}
        for sl in incoming:
            data = sl.model_dump(mode="json", include=set(SLOT_FIELDS))
            row = have.pop(sl.id, None)
            if row is None:
                s.add(PlanSlotRow(project_id=proj.id, branch=b, plan_id=plan_id, slot_id=sl.id, data=data,
                                  status="stale" if sl.status == "stale" else "planned", case_id=sl.case_id))
                continue
            row.data = data
            if sl.status == "stale" and row.status in ("written", "stale"):
                row.status = "stale"  # the client saw this case's code change
        for row in have.values():  # slots whose decision point is gone
            if row.case_id or row.status in ("reserved", "written", "stale", "obsolete"):
                row.status = "obsolete"
                row.reserved_by = row.reserved_until = None
            else:
                s.delete(row)
        s.commit()
    return _view(s, plan)


@router.get(PREFIX)
def plans(p: str, b: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> list[dict]:
    proj = require_role(s, user, p, "viewer")
    out = []
    now = _now()
    for plan in s.query(TestPlanRow).filter_by(project_id=proj.id, branch=b).order_by(TestPlanRow.updated_at.desc()):
        emails = {}
        counts = Counter(_slot_view(r, emails, now)["status"] for r in _slots(s, proj.id, b, plan.plan_id))
        out.append({"plan_id": plan.plan_id, "title": plan.changeset.get("title", ""), "kind": plan.changeset.get("kind", ""),
                    "counts_by_status": dict(counts), "updated_at": _aware(plan.updated_at).isoformat()})
    return out


@router.get(PREFIX + "/{plan_id}")
def plan_view(p: str, b: str, plan_id: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict:
    proj = require_role(s, user, p, "viewer")
    _check_id(plan_id)
    return _view(s, _plan(s, proj.id, b, plan_id))


RESERVE_FOR = timedelta(hours=24)
TRACE_FOR = timedelta(minutes=30)
