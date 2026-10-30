"""Stage 5b: a test plan shared by a team through the registry. The local `PlanStore` stays the working copy (the
board, CSV and generation read it); this module keeps it in step with the registry: share, pull, sync, reserve,
publish. When the registry can't be reached, work is queued with temporary case ids and settled on the next sync:
a case whose slot is still unwritten is accepted, otherwise it becomes a suggested extra, so nothing paid for is lost.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from kb.errors import KbError, RegistryUnavailable
from kb.packs.model import Flow
from kb.testplan.models import CaseSlot, ChangeSet, ImpactReport, Scenario, TestCase
from kb.testplan.store import PlanStore

TEMP = "TCL-"  # a case id given offline; renumbered by the registry on sync


@dataclass
class SyncResult:
    accepted: int = 0
    extras: int = 0
    offline: bool = False


@dataclass
class PublishResult:
    accepted: list[str] = field(default_factory=list)
    extras: list[str] = field(default_factory=list)
    queued: list[str] = field(default_factory=list)


# ---------------------------------------------------------------- offline queue

def _queue_path(store, project: str, label: str, plan_id: str) -> Path:
    return Path(store.kb_home) / "outbox" / "testplans" / f"{project}@{label.replace('/', '__')}" / f"{plan_id}.json"


def _queued(store, project, label, plan_id) -> list[dict]:
    p = _queue_path(store, project, label, plan_id)
    return json.loads(p.read_text()) if p.exists() else []


def _set_queue(store, project, label, plan_id, items: list[dict]) -> None:
    p = _queue_path(store, project, label, plan_id)
    if not items:
        p.unlink(missing_ok=True)
        return
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items))


def _enqueue(store, project, label, plan_id, items: list[dict]) -> None:
    have = {i["id"]: i for i in _queued(store, project, label, plan_id)}
    have.update({i["id"]: i for i in items})
    _set_queue(store, project, label, plan_id, list(have.values()))


def pending(store, project: str, label: str, plan_id: str) -> int:
    return len(_queued(store, project, label, plan_id))


# ---------------------------------------------------------------- helpers

def _item(kind: str, iid: str, payload) -> dict:
    return {"id": iid, "kind": kind, "payload": payload.model_dump(mode="json") if hasattr(payload, "model_dump") else payload}


def _mark_written(ps: PlanStore, project, label, plan_id, ids: dict[str, str]) -> None:
    """ids: slot id -> case id now accepted by the registry."""
    def fn(slots):
        for s in slots:
            if s.id in ids:
                s.status, s.case_id, s.reserved_by, s.reserved_until, s.fail_reason = "written", ids[s.id], None, None, None
    ps.update_slots(project, label, plan_id, fn)


def new_case_ids(cfg, label: str, plan_id: str, client, n: int) -> list[str]:
    """Ids from the plan's shared counter, so two QAs never both write a TC-001. Offline: temporary ids."""
    if n <= 0:
        return []
    try:
        return client.case_ids(cfg.project, label, plan_id, n)
    except RegistryUnavailable:
        tag = uuid.uuid4().hex[:6]
        return [f"{TEMP}{tag}-{i:03d}" for i in range(1, n + 1)]


def _as_extra(ps, cfg, label, plan_id, case: TestCase, client) -> TestCase:
    """A case for a slot a teammate wrote first: kept as a suggested extra under a new id."""
    [nid] = new_case_ids(cfg, label, plan_id, client, 1)
    extra = case.model_copy(update={"id": nid, "suggested": True, "status": "suggested", "slot_id": None,
                                    "unreserved": False})
    ps.delete_case(cfg.project, label, plan_id, case.id)
    ps.save_case(cfg.project, label, plan_id, extra)
    return extra


def _publish(cfg, label, plan_id, store, client, items: list[dict]) -> tuple[list[str], list[TestCase]]:
    """Publish items; returns (accepted ids, cases that conflicted on their slot)."""
    ps = PlanStore(store)
    res = client.publish_plan_items(cfg.project, label, plan_id, items)
    by_id = {i["id"]: i for i in items}
    written = {}
    for iid in res["accepted"] + res["existing"]:
        it = by_id[iid]
        if it["kind"] == "testcase" and not it["payload"].get("suggested"):
            written[it["payload"]["slot_id"]] = iid
            c = TestCase.model_validate(it["payload"])
            ps.save_case(cfg.project, label, plan_id, c.model_copy(update={"unreserved": False}))
    _mark_written(ps, cfg.project, label, plan_id, written)
    lost = [TestCase.model_validate(by_id[c["id"]]["payload"]) for c in res["conflicts"]
            if by_id[c["id"]]["kind"] == "testcase" and not by_id[c["id"]]["payload"].get("suggested")]
    return res["accepted"], lost


# ---------------------------------------------------------------- the operations

def share(cfg, label: str, plan_id: str, store, client) -> None:
    """Publish a local plan for the team: the impact and slots, then everything already written (renumbered from
    the plan's shared counter, so later ids never collide)."""
    ps = PlanStore(store)
    cs = ps.load_change_set(cfg.project, label, plan_id)
    impact = ps.load_impact(cfg.project, label, plan_id)
    if impact is None:
        raise KbError("find what changed first: this plan has no impact yet")
    cs = cs.model_copy(update={"shared": True})
    client.share_plan(cfg.project, label, plan_id, cs.model_dump(mode="json"), impact.model_dump(mode="json"),
                      [s.model_dump(mode="json") for s in ps.load_slots(cfg.project, label, plan_id)])
    ps.save_change_set(label, cs)
    mine = [c for c in ps.cases(cfg.project, label, plan_id) if c.status in ("written", "stale", "suggested")]
    if mine:
        ids = new_case_ids(cfg, label, plan_id, client, len(mine))
        renamed = []
        rename = {}
        for c, nid in zip(mine, ids):
            ps.delete_case(cfg.project, label, plan_id, c.id)
            n = c.model_copy(update={"id": nid})
            ps.save_case(cfg.project, label, plan_id, n)
            rename[c.id] = nid
            renamed.append(n)
        ps.update_slots(cfg.project, label, plan_id,
                        lambda slots: [setattr(s, "case_id", rename.get(s.case_id, s.case_id)) for s in slots])
        publish_cases(cfg, label, plan_id, renamed, store, client)
    traces = ps.dir(cfg.project, label, plan_id) / "traces"
    publish_traces_and_scenarios(cfg, label, plan_id, store, client,
                                 {p.stem: Flow.model_validate_json(p.read_text()) for p in sorted(traces.glob("*.json"))},
                                 ps.scenarios(cfg.project, label, plan_id))
    sync(cfg, label, plan_id, store, client)


def pull(cfg, label: str, plan_id: str, store, client) -> None:
    """First download of a teammate's shared plan into this computer's copy of the analysis."""
    if not store.exists(cfg.project, label):
        raise KbError(f"Pull {label} first: a shared test plan lives in the analysis it was planned against")
    _apply(cfg, label, plan_id, store, client.plan(cfg.project, label, plan_id))


def sync(cfg, label: str, plan_id: str, store, client) -> SyncResult:
    """Send what was queued offline, then take the registry's view of slots, traces, scenarios and cases."""
    out = SyncResult()
    try:
        out = _flush(cfg, label, plan_id, store, client)
        _apply(cfg, label, plan_id, store, client.plan(cfg.project, label, plan_id))
    except RegistryUnavailable:
        out.offline = True
    return out


def _flush(cfg, label, plan_id, store, client) -> SyncResult:
    ps = PlanStore(store)
    items = _queued(store, cfg.project, label, plan_id)
    if not items:
        return SyncResult()
    temp = [i for i in items if i["id"].startswith(TEMP)]
    for it, nid in zip(temp, new_case_ids(cfg, label, plan_id, client, len(temp))):
        if nid.startswith(TEMP):
            raise RegistryUnavailable("registry unreachable")
        ps.delete_case(cfg.project, label, plan_id, it["id"])
        it["id"] = it["payload"]["id"] = nid
        ps.save_case(cfg.project, label, plan_id, TestCase.model_validate(it["payload"]))
    _set_queue(store, cfg.project, label, plan_id, items)  # renumbered, in case the publish below fails
    accepted, lost = _publish(cfg, label, plan_id, store, client, items)
    extras = []
    for c in lost:
        extra = _as_extra(ps, cfg, label, plan_id, c, client)
        extras.append(_item("testcase", extra.id, extra))
    if extras:
        client.publish_plan_items(cfg.project, label, plan_id, extras)
    _set_queue(store, cfg.project, label, plan_id, [])
    n_acc = sum(1 for i in items if i["id"] in accepted and i["kind"] == "testcase" and not i["payload"].get("suggested"))
    return SyncResult(accepted=n_acc, extras=len(extras))


def _apply(cfg, label, plan_id, store, view: dict) -> None:
    ps = PlanStore(store)
    cs = ChangeSet.model_validate(view["changeset"]).model_copy(update={"shared": True})
    ps.save_change_set(label, cs)
    ps.save_impact(cfg.project, label, ImpactReport.model_validate(view["impact"]))
    local = {c.id: c for c in ps.cases(cfg.project, label, plan_id)}
    queued = {i["id"] for i in _queued(store, cfg.project, label, plan_id)}
    mine = {s.id: s for s in ps.load_slots(cfg.project, label, plan_id)}
    slots = []
    for raw in view["slots"]:
        s = CaseSlot.model_validate(raw)
        if s.status == "planned" and any(c.slot_id == s.id and c.id in queued for c in local.values()):
            s.status = "written"  # written here offline, not sent yet
        elif s.status == "planned" and s.id in mine and mine[s.id].status == "failed":
            s.status, s.fail_reason = "failed", mine[s.id].fail_reason  # free for the team, but say why it failed here
        slots.append(s)
    ps.save_slots(cfg.project, label, plan_id, slots)
    traces = ps.dir(cfg.project, label, plan_id) / "traces"
    for it in view["items"]:
        if it["kind"] == "scenario":
            ps.save_scenario(cfg.project, label, plan_id, Scenario.model_validate(it["payload"]))
        elif it["kind"] == "testcase":
            ps.save_case(cfg.project, label, plan_id, TestCase.model_validate(it["payload"]))
        elif it["kind"] == "flowtrace":
            traces.mkdir(parents=True, exist_ok=True)
            (traces / f"{it['id']}.json").write_text(json.dumps(it["payload"]))


def reserve(cfg, label: str, plan_id: str, store, client, *, slot_ids=None, counts=None, per_flow_cap=None,
            flow_ids=None) -> list[str]:
    r = client.reserve(cfg.project, label, plan_id, slot_ids=slot_ids, counts=counts, per_flow_cap=per_flow_cap,
                       flow_ids=flow_ids)
    granted = set(r["granted"])

    def fn(slots):
        for s in slots:
            if s.id in granted:
                s.status, s.fail_reason = "reserved", None
    PlanStore(store).update_slots(cfg.project, label, plan_id, fn)
    return r["granted"]


def publish_cases(cfg, label: str, plan_id: str, cases: list[TestCase], store, client) -> PublishResult:
    """Publish written cases. A case whose slot a teammate wrote first becomes a suggested extra; if the registry
    can't be reached the cases are kept locally, marked unreserved, and queued for the next sync."""
    ps = PlanStore(store)
    out = PublishResult()
    online = [c for c in cases if not c.id.startswith(TEMP)]
    offline = [c for c in cases if c.id.startswith(TEMP)]
    if online:
        try:
            out.accepted, lost = _publish(cfg, label, plan_id, store, client,
                                          [_item("testcase", c.id, c) for c in online])
            for c in lost:
                extra = _as_extra(ps, cfg, label, plan_id, c, client)
                client.publish_plan_items(cfg.project, label, plan_id, [_item("testcase", extra.id, extra)])
                out.extras.append(extra.id)
        except RegistryUnavailable:
            offline = online + offline
    if offline:
        marked = [c.model_copy(update={"unreserved": True}) for c in offline]
        for c in marked:
            ps.save_case(cfg.project, label, plan_id, c)
        _enqueue(store, cfg.project, label, plan_id, [_item("testcase", c.id, c) for c in marked])
        out.queued = [c.id for c in marked]
    return out


def publish_traces_and_scenarios(cfg, label: str, plan_id: str, store, client, traces: dict[str, Flow],
                                 scenarios: list[Scenario]) -> None:
    """Traces (keyed by their file name, `<flow>-<side>-<commits>`) and scenarios: the first publish wins."""
    items = [_item("flowtrace", k, f) for k, f in traces.items()] + [_item("scenario", s.id, s) for s in scenarios]
    if not items:
        return
    try:
        client.publish_plan_items(cfg.project, label, plan_id, items)
    except RegistryUnavailable:
        _enqueue(store, cfg.project, label, plan_id, items)
