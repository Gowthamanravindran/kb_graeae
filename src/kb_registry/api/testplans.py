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
            self.s.commit()  # end the read snapshot taken before the lock, so what follows sees the latest rows
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
    # always re-read: another request may have changed them since this session last looked
    return s.query(PlanSlotRow).filter_by(project_id=project_id, branch=b, plan_id=plan_id) \
        .execution_options(populate_existing=True).all()


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


# ---------------------------------------------------------------- reservations

PRIORITIES = ("P0", "P1", "P2", "P3")


def _free(r: PlanSlotRow, user_id: int, now: datetime) -> bool:
    if r.assigned_to is not None and r.assigned_to != user_id:
        return False
    if r.status in ("planned", "failed"):
        return True
    until = _aware(r.reserved_until)
    return r.status == "reserved" and (until is None or until <= now)  # an abandoned reservation


class ReserveIn(BaseModel):
    slot_ids: list[str] | None = None
    counts: dict[str, int] | None = None
    per_flow_cap: int | None = None
    flow_ids: list[str] | None = None


class SlotIdsIn(BaseModel):
    slot_ids: list[str]


@router.post(PREFIX + "/{plan_id}/reservations")
def reserve(p: str, b: str, plan_id: str, body: ReserveIn, user: User = Depends(current_user),
            s: Session = Depends(get_session)) -> dict:
    """Reserve slots for the caller, atomically: concurrent requests never get the same slot. By ids, or by
    counts per priority in the plan's order (priority, then flow risk, then slot id)."""
    proj = require_role(s, user, p, "contributor")
    plan = _plan(s, proj.id, b, plan_id)
    risk = {f["id"]: f.get("risk", 0.0) for f in plan.impact.get("flows", [])}
    with _plan_lock(s, proj.id, b, plan_id):
        now = _now()
        rows = [r for r in _slots(s, proj.id, b, plan_id) if _free(r, user.id, now)]
        if body.flow_ids is not None:
            rows = [r for r in rows if r.data.get("flow_id") in set(body.flow_ids)]
        chosen: list[PlanSlotRow] = []
        if body.slot_ids is not None:
            want = set(body.slot_ids)
            chosen = sorted((r for r in rows if r.slot_id in want), key=lambda r: r.slot_id)
        elif body.counts:
            left = {k: max(0, int(v)) for k, v in body.counts.items()}
            per_flow: dict[str, int] = {}
            order = sorted(rows, key=lambda r: (PRIORITIES.index(r.data.get("priority", "P2")) if r.data.get("priority") in PRIORITIES else 9,
                                                -risk.get(r.data.get("flow_id"), 0.0), r.slot_id))
            for r in order:
                prio, flow = r.data.get("priority"), r.data.get("flow_id")
                if left.get(prio, 0) <= 0:
                    continue
                if body.per_flow_cap is not None and per_flow.get(flow, 0) >= body.per_flow_cap:
                    continue
                chosen.append(r)
                left[prio] -= 1
                per_flow[flow] = per_flow.get(flow, 0) + 1
        until = now + RESERVE_FOR
        for r in chosen:
            r.status, r.reserved_by, r.reserved_until = "reserved", user.id, until
        s.commit()
    return {"granted": [r.slot_id for r in chosen], "reserved_until": until.isoformat()}


@router.post(PREFIX + "/{plan_id}/reservations/renew")
def renew(p: str, b: str, plan_id: str, body: SlotIdsIn, user: User = Depends(current_user),
          s: Session = Depends(get_session)) -> dict:
    proj = require_role(s, user, p, "contributor")
    _plan(s, proj.id, b, plan_id)
    with _plan_lock(s, proj.id, b, plan_id):
        until = _now() + RESERVE_FOR
        want = set(body.slot_ids)
        done = []
        for r in _slots(s, proj.id, b, plan_id):
            if r.slot_id in want and r.status == "reserved" and r.reserved_by == user.id:
                r.reserved_until = until
                done.append(r.slot_id)
        s.commit()
    return {"renewed": sorted(done), "reserved_until": until.isoformat()}


@router.delete(PREFIX + "/{plan_id}/reservations")
def release(p: str, b: str, plan_id: str, body: SlotIdsIn, user: User = Depends(current_user),
            s: Session = Depends(get_session)) -> dict:
    """Give slots back. A contributor releases their own; a maintainer (the QA lead) anyone's."""
    from kb_registry.auth import role_of

    proj = require_role(s, user, p, "contributor")
    _plan(s, proj.id, b, plan_id)
    lead = user.is_admin or role_of(s, user, proj) == "maintainer"
    with _plan_lock(s, proj.id, b, plan_id):
        want = set(body.slot_ids)
        done = []
        for r in _slots(s, proj.id, b, plan_id):
            if r.slot_id in want and r.status == "reserved" and (lead or r.reserved_by == user.id):
                r.status, r.reserved_by, r.reserved_until = "planned", None, None
                done.append(r.slot_id)
        s.commit()
    return {"released": sorted(done)}


class AssignIn(BaseModel):
    slot_ids: list[str] | None = None
    flow_ids: list[str] | None = None
    email: str | None


@router.post(PREFIX + "/{plan_id}/assign")
def assign(p: str, b: str, plan_id: str, body: AssignIn, user: User = Depends(current_user),
           s: Session = Depends(get_session)) -> dict:
    """The QA lead assigns slots (or whole flows) to a member, or unassigns them (email null). A reservation held
    by anyone else is released, so the assignee can take the slot."""
    from kb_registry.auth import role_of

    proj = require_role(s, user, p, "maintainer")
    _plan(s, proj.id, b, plan_id)
    target = None
    if body.email is not None:
        target = s.query(User).filter_by(email=body.email).one_or_none()
        if target is None or role_of(s, target, proj) is None:
            raise HTTPException(400, f"{body.email} is not a member of {p}")
    with _plan_lock(s, proj.id, b, plan_id):
        ids, flows = set(body.slot_ids or []), set(body.flow_ids or [])
        done = []
        for r in _slots(s, proj.id, b, plan_id):
            if r.slot_id not in ids and r.data.get("flow_id") not in flows:
                continue
            r.assigned_to = target.id if target else None
            if r.status == "reserved" and r.reserved_by not in (None, r.assigned_to):
                r.status, r.reserved_by, r.reserved_until = "planned", None, None
            done.append(r.slot_id)
        s.commit()
    return {"assigned": sorted(done)}


# ---------------------------------------------------------------- trace locks

SIDES = ("base", "head")


def _lock_args(p, b, plan_id, flow_id, side, user, s):
    proj = require_role(s, user, p, "contributor")
    _plan(s, proj.id, b, plan_id)
    if side not in SIDES or not is_safe_id(flow_id):
        raise HTTPException(400, "bad flow or side")
    return proj


@router.post(PREFIX + "/{plan_id}/traces/{flow_id}/{side}/lock")
def lock_trace(p: str, b: str, plan_id: str, flow_id: str, side: str, user: User = Depends(current_user),
               s: Session = Depends(get_session)):
    """One person traces a flow at a time (30 minutes, renewed by asking again); others see who is tracing."""
    from fastapi.responses import JSONResponse

    proj = _lock_args(p, b, plan_id, flow_id, side, user, s)
    with _plan_lock(s, proj.id, b, plan_id):
        now = _now()
        row = s.get(TraceLockRow, (proj.id, b, plan_id, flow_id, side), populate_existing=True)
        if row is not None and row.holder_id != user.id and _aware(row.until) > now:
            holder = s.get(User, row.holder_id)
            return JSONResponse({"detail": f"{holder.email} is tracing this flow", "holder": holder.email,
                                 "until": _aware(row.until).isoformat()}, status_code=409)
        if row is None:
            row = TraceLockRow(project_id=proj.id, branch=b, plan_id=plan_id, flow_id=flow_id, side=side,
                               holder_id=user.id, until=now + TRACE_FOR)
            s.add(row)
        row.holder_id, row.until = user.id, now + TRACE_FOR
        s.commit()
        return {"holder": user.email, "until": _aware(row.until).isoformat()}


@router.delete(PREFIX + "/{plan_id}/traces/{flow_id}/{side}/lock")
def unlock_trace(p: str, b: str, plan_id: str, flow_id: str, side: str, user: User = Depends(current_user),
                 s: Session = Depends(get_session)) -> dict:
    proj = _lock_args(p, b, plan_id, flow_id, side, user, s)
    with _plan_lock(s, proj.id, b, plan_id):
        row = s.get(TraceLockRow, (proj.id, b, plan_id, flow_id, side), populate_existing=True)
        released = row is not None and row.holder_id == user.id
        if released:
            s.delete(row)
        s.commit()
    return {"released": released}


# ---------------------------------------------------------------- publishing items

class PlanItemIn(BaseModel):
    id: str
    kind: str
    payload: dict


class PlanItemsIn(BaseModel):
    items: list[PlanItemIn]


def _validate_plan_item(it: PlanItemIn) -> dict:
    from kb.packs.model import Flow
    from kb.testplan.models import Scenario, TestCase

    if not is_safe_id(it.id):
        raise HTTPException(400, f"item id {it.id!r} is not allowed (letters, digits, . _ : - only)")
    model = {"testcase": TestCase, "scenario": Scenario, "flowtrace": Flow}.get(it.kind)
    if model is None:
        raise HTTPException(400, f"item {it.id}: unknown kind {it.kind}")
    try:
        obj = model.model_validate(it.payload)
    except ValidationError as e:
        raise HTTPException(400, f"item {it.id}: invalid {it.kind}: {e.errors()[0]['msg']}") from e
    if it.kind != "flowtrace" and obj.id != it.id:
        raise HTTPException(400, f"item {it.id}: payload id is {obj.id}")
    return obj.model_dump(mode="json")


@router.post(PREFIX + "/{plan_id}/items")
def publish_items(p: str, b: str, plan_id: str, body: PlanItemsIn, user: User = Depends(current_user),
                  s: Session = Depends(get_session)) -> dict:
    """Traces and scenarios: the first publish wins. A case is accepted when its slot is reserved by the publisher,
    or reserved by nobody (or abandoned) and unwritten; then the slot is written. A case for a slot someone else
    wrote or holds is a conflict, which the app turns into a suggested extra. Extras are always accepted."""
    import json as _json

    from kb_registry.api.packs import MAX_ITEM_BYTES, MAX_ITEMS, _sha

    proj = require_role(s, user, p, "contributor")
    _plan(s, proj.id, b, plan_id)
    if len(body.items) > MAX_ITEMS:
        raise HTTPException(413, f"at most {MAX_ITEMS} items per request")
    prepared = []
    for it in body.items:
        if len(_json.dumps(it.payload)) > MAX_ITEM_BYTES:
            raise HTTPException(413, f"item {it.id} is larger than {MAX_ITEM_BYTES // 1024} KB")
        prepared.append((it, _validate_plan_item(it)))
    accepted, existing, conflicts = [], [], []
    with _plan_lock(s, proj.id, b, plan_id):
        now = _now()
        emails = _emails(s)
        slots = {r.slot_id: r for r in _slots(s, proj.id, b, plan_id)}
        for it, data in prepared:
            if it.kind == "testcase" and not data.get("suggested") and data.get("slot_id") not in slots:
                raise HTTPException(400, f"item {it.id}: no slot {data.get('slot_id')} in this plan")
        for it, data in prepared:
            row = s.get(PlanItemRow, (proj.id, b, plan_id, it.id), populate_existing=True)
            if row is not None:
                if _sha(row.payload) == _sha(data):
                    existing.append(it.id)
                else:
                    conflicts.append({"id": it.id, "reason": "already published with different content"})
                continue
            if it.kind == "testcase" and not data.get("suggested"):
                sl = slots[data["slot_id"]]
                until = _aware(sl.reserved_until)
                held = sl.status == "reserved" and until is not None and until > now and sl.reserved_by != user.id
                if sl.case_id or sl.status in ("written", "stale", "obsolete"):
                    conflicts.append({"id": it.id, "reason": "already written"})
                    continue
                if held:
                    conflicts.append({"id": it.id, "reason": f"reserved by {emails.get(sl.reserved_by, 'someone')}"})
                    continue
                sl.status, sl.case_id, sl.reserved_by, sl.reserved_until = "written", it.id, None, None
            s.add(PlanItemRow(project_id=proj.id, branch=b, plan_id=plan_id, item_id=it.id, kind=it.kind, payload=data,
                              author_id=user.id, published_at=now))
            accepted.append(it.id)
        s.commit()
    return {"accepted": accepted, "existing": existing, "conflicts": conflicts}
