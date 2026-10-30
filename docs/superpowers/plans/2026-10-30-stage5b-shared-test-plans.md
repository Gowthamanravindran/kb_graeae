# Stage 5b — Shared Test Plans Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Several QAs work on one test plan through the registry. The impact, traces, scenarios and cases are shared once. Each QA reserves the cases they will write, so nobody writes or pays for the same case twice, and a QA lead can assign and release work.

**Architecture:**
- **Storage:** the registry stores a shared plan in four new tables: plan, slots, items and trace locks.
- **Reservations:** they run in one database transaction under a per-plan lock, so concurrent requests never overlap.
- **Client:** the desktop/CLI keeps working on its local `PlanStore` copy. A new `kb/testplan/shared.py` syncs that copy with the registry before and after each run. It reserves through the registry and publishes cases, traces and scenarios as they are written. When the registry is unreachable it falls back to local reservations and a queue.

**Tech Stack:**
- **Registry:** FastAPI, SQLAlchemy and Alembic, on SQLite and Postgres.
- **Engine:** the existing kb engine (LangGraph design graph, `kb serve`).
- **Desktop:** React with Vitest; Playwright with Electron for the whole-app tests.

**Spec:** `docs/superpowers/specs/2026-10-29-test-planning-design.md` (§7 Sharing and parallel work, §9 Screens: the plan board, §11 Stage 5b).

## Global Constraints

- **Reservation length:** reservations last **24 h**. The app renews them **every 10 minutes** while a job runs.
- **Trace reservations:** a trace reservation per (flow, side) lasts **30 minutes**, renewed the same way.
- **Roles:**
  - a **maintainer** (the QA lead) can assign flows or slots to a user and release anyone's reservation;
  - a **contributor** may reserve unassigned slots and slots assigned to them;
  - a **viewer** reads and exports.
- **Publishing a case:**
  - Accepted when the slot is reserved by the publisher, or is unreserved and unwritten.
  - If the slot is already written, it is a **conflict**. The app then re-publishes the case as a **suggested extra**, so generated work is never silently lost.
  - The first published case for a slot wins.
- **Assignment while in progress:** a lead reassigning a slot releases the old reservation. The old holder's late publish is accepted if the slot is still unwritten; otherwise it becomes a suggested extra.
- **Offline:** a QA may generate offline; the cases are marked `unreserved` and the app warns before an offline run. On sync, they are accepted if the slot is unwritten, otherwise they become suggested extras.
- **Without a registry**, everything works for one person, as today.
- **Selection order is unchanged:** slots are taken by priority, then flow risk, then slot id (`kb/testplan/slots.select`).
- **Git:** commit to `phase1` only; never push.

## Review Focus

1. **Two QAs reserve at the same moment** (threads, SQLite *and* Postgres). The grants never overlap, and the second QA gets the next free slots. → Task 2, `test_concurrent_reservations_never_overlap`.
2. **A QA's app dies mid-run** (no renewal). After 24 h their slots are free again, and their late publish is still accepted if the slot is unwritten. → Task 2 `test_expired_reservation_is_free_again`, Task 3 `test_late_publish_after_expiry_accepted_if_unwritten`.
3. **The PR gets a new commit while someone is generating.** The impact re-run re-shares slots: a reserved slot that disappeared keeps its holder's late case as obsolete, and the reservations of other slots survive. → Task 1, `test_reshare_keeps_state_and_obsoletes_removed_written`.
4. **Registry unreachable mid-run.** Cases written locally aren't lost: they are queued as `unreserved`, and on the next sync they are accepted or become extras. → Task 4, `test_offline_cases_sync_later`.
5. **A viewer, or a contributor trying lead actions.**
   - A viewer can read and export but not reserve.
   - A contributor can't assign or release others' slots, and can't reserve slots assigned to someone else.
   → Task 2, `test_roles_for_reserve_assign_release`.

---

## File map

| File | Responsibility |
|---|---|
| `src/kb_registry/models.py` (modify) | `TestPlanRow`, `PlanSlotRow`, `PlanItemRow`, `TraceLockRow` |
| `src/kb_registry/migrations/versions/0003_testplans.py` (create) | the four tables |
| `src/kb_registry/api/testplans.py` (create) | share/get/list, reservations, assignment, trace locks, item publish |
| `src/kb_registry/api/projects.py` (modify) | `GET /projects/{p}/members` |
| `src/kb/sync/client.py` (modify) | client methods for the above |
| `src/kb/testplan/shared.py` (create) | share, pull/sync into `PlanStore`, reserve/renew/release, publish with conflict→extra, offline queue |
| `src/kb/testplan/graph_design.py`, `nodes_impact.py` (modify) | shared mode in a generation run; re-share after impact |
| `src/kb/testplan/models.py` (modify) | `ChangeSet.shared: bool`, `TestCase.unreserved: bool`, `CaseSlot.reserved_by_email`/`assigned_to` as emails |
| `src/kb/serve/app.py`, `src/kb/cli.py` (modify) | routes and `kb testplan share|pull|sync|assign|release` |
| `desktop/src/views/PlanBoard.tsx`, `TestPlans.tsx`, `api/*` (modify) | Share, shared list, people/progress, reserved/assigned, lead Assign/Release, offline warning |
| `desktop/e2e/app.spec.ts`, `tests/e2e_support/*` (modify) | two-user journey against a real registry |

Registry routes all live under `/api/v1/projects/{p}/branches/{b:path}/testplans`. `{b}` is the **base pack label** that holds the plan, for example `production`.

---

### Task 1: Registry storage and plan sharing

**Files:**
- Modify: `src/kb_registry/models.py`.
- Create: `src/kb_registry/migrations/versions/0003_testplans.py` and `src/kb_registry/api/testplans.py`.
- Register the new router in `src/kb_registry/api/__init__.py`.
- Test: `tests/registry/test_api_testplans.py`.

**Interfaces:**
- Produces these tables (all scoped by `project_id` and `branch`):
  - `TestPlanRow(project_id, branch, plan_id)` (primary key), with `changeset: JSON`, `impact: JSON`, `created_by → users.id`, `updated_at`.
  - `PlanSlotRow(project_id, branch, plan_id, slot_id)` (primary key), with:
    - `data: JSON`: the `CaseSlot` definition fields `flow_id, scenario_type, inventory_ids, outcome, priority, rule`;
    - `status: str`, `reserved_by: int|None`, `reserved_until: datetime|None`, `assigned_to: int|None`, `case_id: str|None`.
  - `PlanItemRow(project_id, branch, plan_id, item_id)` (primary key), with `kind ∈ {"scenario","testcase","flowtrace"}`, `payload: JSON`, `author_id`, `published_at`.
  - `TraceLockRow(project_id, branch, plan_id, flow_id, side)` (primary key), with `holder_id`, `until`.
- Produces these routes:
  - **`PUT …/testplans/{plan_id}`** (contributor). Body `{changeset: dict, impact: dict, slots: [CaseSlot dict]}`, returns the plan view. The first share creates the plan; a re-share merges (see below).
  - **`GET …/testplans`** (viewer) returns `[{plan_id, title, kind, counts_by_status, updated_at}]`.
  - **`GET …/testplans/{plan_id}`** (viewer) returns `{changeset, impact, slots: [ {…data, id, status, reserved_by: email|None, reserved_until, assigned_to: email|None, case_id} ], items: [{id, kind, payload, author, published_at}]}`.
- Merge rule on a re-share (it mirrors the local `reconcile`, which the client has already run):
  - A slot id the registry already has keeps its server-side state (status, reservation, assignment, case). Its definition `data` and `priority` are replaced. A `stale` status sent by the client wins over `written`.
  - A new slot id is inserted as `planned`.
  - A registry slot missing from the upload is set to `obsolete` if it has a case or is reserved; otherwise it is deleted.

- [ ] **Step 1: Write the failing tests** in `tests/registry/test_api_testplans.py`, using the existing `client`/token helpers in `tests/registry/helpers.py` and a pushed base (as `test_api_overlay.py` does):

```python
def test_share_then_get_and_list(reg):            # maintainer shares; contributor GETs; list shows it
    ...
    assert view["slots"][0]["status"] == "planned" and view["changeset"]["id"] == "pr-1"

def test_share_needs_contributor_and_a_pushed_base(reg):   # viewer → 403; no base → 404

def test_reshare_keeps_state_and_obsoletes_removed_written(reg):
    # slot A written (case TC-001), slot B reserved by u2, slot C planned; re-share with only B and a new D
    # → A obsolete (case kept), B still reserved by u2, C deleted, D planned
```

- [ ] **Step 2: Run** `uv run pytest tests/registry/test_api_testplans.py -q`. Expected: FAIL (404 on the new routes).
- [ ] **Step 3: Implement** the models, migration `0003` (`down_revision = "0002"`), and the three routes. Validate `plan_id` with `kb_registry.paths.is_safe_id`. Validate `changeset` with `kb.testplan.models.ChangeSet` and `impact` with `ImpactReport` (400 if invalid). The 404 when the branch has no base reuses `_latest_base` from `api/packs.py`.
- [ ] **Step 4: Run** `uv run pytest tests/registry -q`. Expected: all pass on SQLite and on Postgres where installed. `test_models.py` must also show the migration produces the same schema as the models.
- [ ] **Step 5: Commit** `feat(registry): shared test plans storage and share/get/list`.

### Task 2: Reservations, renewal, release and assignment

**Files:**
- Modify: `src/kb_registry/api/testplans.py` and `src/kb_registry/api/projects.py`.
- Test: `tests/registry/test_api_testplans.py`.

**Interfaces:**
- Consumes the Task 1 tables.
- Produces these routes:
  - **`POST …/{plan_id}/reservations`** (contributor). Body `{slot_ids?: [str], counts?: {"P0": n, …}, per_flow_cap?: int, flow_ids?: [str]}`, returns `{granted: [slot_id], reserved_until}`.
    - With `slot_ids`, it grants those that are free.
    - With `counts`, it takes free slots in the order of `kb.testplan.slots.select` (priority, then flow risk from `impact.flows[].risk`, then id), honouring `per_flow_cap` and `flow_ids`.
    - "Free" means: status `planned` or `failed`; no reservation, or an expired one; and assigned to nobody or to the caller.
    - Granted slots become `reserved`, with `reserved_by = caller` and `reserved_until = now + 24 h`.
  - **`POST …/{plan_id}/reservations/renew`** with `{slot_ids}` returns `{renewed: [id], reserved_until}`. It renews only the caller's own reservations.
  - **`DELETE …/{plan_id}/reservations`** with `{slot_ids}` returns `{released: [id]}`. A contributor can release only their own; a maintainer can release anyone's. Released slots go back to `planned`.
  - **`POST …/{plan_id}/assign`** (maintainer) with `{slot_ids?: [], flow_ids?: [], email: str|None}` returns `{assigned: [id]}`.
    - It sets `assigned_to`; `None` unassigns.
    - Assigning a slot reserved by someone else releases that reservation.
    - An email that isn't a project member gets a 400.
  - **`GET /projects/{p}/members`** (viewer) returns `[{email, name, role}]`.
- Atomicity: `_plan_lock(s, plan)` serializes every write to one plan.
  - On Postgres: `SELECT … FROM test_plans WHERE … FOR UPDATE`.
  - On SQLite: a process-wide `threading.Lock` per (project, branch, plan_id) held across the transaction (the registry is one process on SQLite).

- [ ] **Step 1: Write the failing tests:**

```python
def test_reserve_by_counts_follows_priority_then_risk(reg): ...
def test_concurrent_reservations_never_overlap(reg):   # 8 threads × counts {"P0": 3} on a 20-slot plan:
    # union of grants has no duplicates; total granted == min(24, free P0 slots)
def test_second_qa_gets_the_next_free_slots(reg): ...
def test_expired_reservation_is_free_again(reg, monkeypatch):   # patch the module clock forward 24h+1s
def test_renew_only_own(reg): ...
def test_roles_for_reserve_assign_release(reg):
    # viewer reserve → 403; contributor assign → 403; contributor release other's → only own released;
    # contributor reserving a slot assigned to someone else → not granted; maintainer release any → released
def test_assign_releases_someone_elses_reservation(reg): ...
def test_members_list(reg): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/registry/test_api_testplans.py -q`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the routes. Read the time through one module function, `_now()`, so tests can move the clock.
- [ ] **Step 4: Run** `uv run pytest tests/registry -q`. Expected: all pass (Postgres included where available).
- [ ] **Step 5: Commit** `feat(registry): atomic slot reservations, renewal, release and lead assignment`.

### Task 3: Trace locks and publishing items

**Files:**
- Modify: `src/kb_registry/api/testplans.py`.
- Test: `tests/registry/test_api_testplans.py`.

**Interfaces:**
- Produces these routes:
  - **`POST …/{plan_id}/traces/{flow_id}/{side}/lock`** (contributor), where `side ∈ {"base","head"}`.
    - Returns 200 `{holder: email, until}` when granted or renewed by the same holder.
    - Returns 409 `{detail, holder: email, until}` while someone else holds it.
    - The lock lasts 30 minutes.
  - **`DELETE …/{plan_id}/traces/{flow_id}/{side}/lock`**, which releases the caller's own lock.
  - **`POST …/{plan_id}/items`** (contributor) with `{items: [{id, kind, payload}]}`, at most `MAX_ITEMS`, each at most `MAX_ITEM_BYTES` (both from `api/packs.py`). Returns `{accepted: [id], existing: [id], conflicts: [{id, reason}]}`.
    - **`scenario` and `flowtrace`:** the first publish wins. The same id with the same content is `existing`; different content is a conflict.
    - **`testcase`:** the payload is validated as `kb.testplan.models.TestCase`, and `payload.slot_id` must exist.
      - Accepted if the slot is reserved by the caller, or is not reserved by anyone else and has no case. The slot becomes `written`, with `case_id = id`, and its reservation is cleared.
      - If the slot already has a case, it is a conflict: `"already written"`.
      - A `TestCase` with `suggested: true` (an extra) is always accepted and leaves the slot alone.
      - An `unreserved: true` case follows the same rules (accepted only if the slot is unwritten).
- `GET …/{plan_id}` (Task 1) includes these items, and its slot view gains `trace_locks: [{flow_id, side, holder, until}]`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_trace_lock_first_wins_then_expires(reg, monkeypatch): ...
def test_publish_case_for_own_reservation(reg): ...
def test_publish_case_conflicts_when_slot_written(reg): ...
def test_late_publish_after_expiry_accepted_if_unwritten(reg, monkeypatch): ...
def test_publish_suggested_extra_always_accepted(reg): ...
def test_scenario_and_flowtrace_first_wins(reg): ...
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** the routes, taking `_plan_lock` around each testcase publish.
- [ ] **Step 4: Run** `uv run pytest tests/registry -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(registry): trace locks and shared plan items with first-write-wins cases`.

### Task 4: Client — share, pull, reserve, publish, offline queue

**Files:**
- Modify: `src/kb/sync/client.py` and `src/kb/testplan/models.py`.
- Create: `src/kb/testplan/shared.py`.
- Test: `tests/testplan/test_tp_shared.py`, using a real registry `TestClient` the way `tests/sync` does, through `RegistryClient(..., http=TestClient(reg.app))`.

**Interfaces:**
- Consumes the Task 1–3 routes.
- Model additions:
  - `ChangeSet.shared: bool = False`.
  - `TestCase.unreserved: bool = False`.
  - `CaseSlot.reserved_by` and `CaseSlot.assigned_to` now hold an email when the plan is shared (they are strings already).
- `RegistryClient` gains these methods, each returning the JSON:
  - `share_plan(p, b, plan_id, changeset, impact, slots)`, `plans(p, b)`, `plan(p, b, plan_id)`;
  - `reserve(p, b, plan_id, *, slot_ids=None, counts=None, per_flow_cap=None, flow_ids=None)`;
  - `renew(p, b, plan_id, slot_ids)`, `release(p, b, plan_id, slot_ids)`;
  - `assign(p, b, plan_id, *, slot_ids=None, flow_ids=None, email)`;
  - `lock_trace(p, b, plan_id, flow_id, side)` (raises `TraceBusy(holder, until)` on 409), `unlock_trace(...)`;
  - `publish_plan_items(p, b, plan_id, items)`, `members(p)`.
- `kb/testplan/shared.py`:
  - `share(cfg, label, plan_id, store, client) -> None`: uploads the local changeset, impact and slots, sets `ChangeSet.shared = True` locally, then calls `sync`.
  - `sync(cfg, label, plan_id, store, client) -> SyncResult(accepted: int, extras: int)`:
    1. Flushes the plan's offline queue (`$KB_HOME/outbox/testplans/<project>@<label>/<plan_id>.json`), turning conflicting cases into suggested extras with new ids.
    2. Pulls the plan view and overwrites the local slot states, changeset and impact.
    3. Writes every pulled scenario, testcase and flowtrace into the local `PlanStore`. Flowtraces go to `traces/<flow>-<side>-<commits>.json`, the path `graph_design._trace_path` uses.
  - `pull(cfg, label, plan_id, store, client) -> None`: the first download into a pack that has none. It requires the base pack `label` locally, and raises `KbError("Pull <label> first")` otherwise.
  - `reserve(...) -> list[str]`: granted ids.
  - `publish_cases(cfg, label, plan_id, cases, store, client) -> PublishResult(accepted: list[str], extras: list[str])`.
    - A conflicting case is re-published as `suggested=True, status="suggested", slot_id=None` with a new id from `PlanStore.next_case_id`.
    - On `RegistryUnavailable` the cases are queued with `unreserved=True`.
  - `publish_traces_and_scenarios(...)`: the same queue behaviour.

- [ ] **Step 1: Write the failing tests:**

```python
def test_share_then_second_user_pulls_the_plan(two_users): ...
def test_publish_conflict_becomes_suggested_extra(two_users): ...
def test_offline_cases_sync_later(two_users, monkeypatch):   # registry raises RegistryUnavailable on publish
    # → queued with unreserved=True; next sync: unwritten slot accepted, written slot → extra
def test_pull_requires_the_base_pack(two_users): ...
```

- [ ] **Step 2: Run** `uv run pytest tests/testplan/test_tp_shared.py -q`. Expected: FAIL (no module).
- [ ] **Step 3: Implement** the client methods and `shared.py`.
- [ ] **Step 4: Run** `uv run pytest tests/testplan tests/sync -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(testplan): share, pull and sync test plans through the registry`.

### Task 5: Shared mode in generation and impact re-runs

**Files:**
- Modify: `src/kb/testplan/graph_design.py` and `src/kb/testplan/nodes_impact.py`.
- Test: `tests/testplan/test_tp_shared.py`.

**Interfaces:**
- Consumes `shared.py` (Task 4).
- `run_design(..., client: RegistryClient | None = None)`. With `ChangeSet.shared` and a client, a run works like this:
  - **`plan_work`:** calls `shared.sync` first. It then reserves through `client.reserve` (with counts, cap and flow ids, or `slot_ids` for `only`) instead of choosing slots locally, and the granted slots become `ctx.chosen`. If the registry is unreachable, it falls back to local `select`, adds the warning `"Offline: these cases are not reserved and may duplicate a teammate's"` to the result, and marks the cases `unreserved`.
  - **Renewal:** a `threading.Timer`-based renewer calls `client.renew` every 600 s for `ctx.reserved`, plus the trace locks held, until the run ends.
  - **`flow_node`:** before a trace, it uses a pulled flowtrace if one exists. Otherwise it takes `lock_trace`. On `TraceBusy`, the flow fails its slots with the reason `"<holder> is tracing <flow name>"` and releases those slots' reservations; the user can retry later. After tracing it publishes the flowtrace, then the scenarios, then publishes the cases with `publish_cases`. Accepted cases are `written`, and conflicts become extras locally.
  - **The end of a run** (success or failure) releases unused reservations through `client.release`, then runs `shared.sync`.
- `DesignResult` gains `extras_from_conflicts: int` and `warnings: list[str]`.
- `nodes_impact.save_impact`: after `reconcile`, if `ChangeSet.shared` and a client is available, it calls `client.share_plan` (the Task 1 merge) and then `shared.sync`.

- [ ] **Step 1: Write the failing tests:**

```python
def test_two_users_split_generation_without_overlap(two_users):
    # user A generates {"P0": 2}, user B generates {"P0": 2} concurrently (threads, DesignFake)
    # → 4 distinct written slots, both see 4 cases after sync, no extras
def test_busy_trace_fails_only_that_flow(two_users): ...
def test_reservations_renewed_while_running(two_users, monkeypatch):  # shrink the interval to 0.05 s
def test_impact_rerun_reshares_slots(two_users): ...
def test_offline_run_warns_and_marks_unreserved(two_users, monkeypatch): ...
```

- [ ] **Step 2: Run** the tests. Expected: FAIL.
- [ ] **Step 3: Implement** shared mode. Local-only plans (`shared` false, or no registry) must behave exactly as today; the existing `test_tp_graph_design.py` stays green unchanged.
- [ ] **Step 4: Run** `uv run pytest tests/testplan -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(testplan): reserve, renew and publish while generating a shared plan`.

### Task 6: Engine routes and CLI

**Files:**
- Modify: `src/kb/serve/app.py` and `src/kb/cli.py`.
- Test: `tests/serve/test_testplans_shared.py` and `tests/test_cli.py`.

**Interfaces** (engine routes, all under `/api/projects/{name}/testplans`):
- **`POST /{plan_id}/share`** returns a `job_id`. It needs a registry and a pushed base; the error says `"Push <label> to the registry first"`.
- **`GET /shared`** returns `[{plan_id, label, title, kind, counts_by_status, local: bool}]` for every branch pulled locally.
- **`POST /shared/{label}/{plan_id}/pull`** returns a `job_id`.
- **`POST /{plan_id}/sync`** returns a `job_id`.
- **`POST /{plan_id}/assign`** with `{slot_ids?, flow_ids?, email|null}`, and **`POST /{plan_id}/release`** with `{slot_ids}`: both are synchronous and proxy to the registry. A 403 from the registry is passed through.
- **`GET /{plan_id}/people`** returns `{members: [...], me: email, role, progress: [{email, written, reserved}]}`.
- The existing `GET /{plan_id}` adds `shared: bool`, `warnings`, and `trace_locks`.
- `_design_job` passes `client` when the plan is shared.
- **CLI:** `kb testplan share|pull|sync <plan-id>`, `kb testplan assign <plan-id> --flow F|--slot S --to EMAIL`, and `kb testplan release <plan-id> --slot S`.

- [ ] **Step 1: Write the failing tests** for each route, using a registry `TestClient` wired into the serve settings the way `tests/serve/test_branches.py::registry_serve` does. Include `test_share_without_pushed_base_says_push_first`.
- [ ] **Step 2: Run** them. Expected: FAIL.
- [ ] **Step 3: Implement** the routes and CLI.
- [ ] **Step 4: Run** `uv run pytest tests/serve tests/test_cli.py -q`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(serve): share, pull, sync, assign and release test plans`.

### Task 7: Desktop — sharing, people and lead controls

**Files:**
- Modify: `desktop/src/api/client.ts`, `types.ts`, `views/PlanBoard.tsx` and `views/TestPlans.tsx`.
- Test: `desktop/src/views/PlanBoard.test.tsx` and `TestPlans.test.tsx`.

**Behaviour (spec §9.3):**
- **Plan board header:**
  - **Share with team** (when a registry is set and the plan isn't shared). Once shared, a "Shared on <registry host>" badge and **Sync**.
  - The people/progress strip: per person, written and reserved counts, plus "You".
- **Slot rows:**
  - "Reserved by <name> until <time>", "Assigned to <name>".
  - For a maintainer: an **Assign** menu (members plus "Unassign") on flows and slots, and **Release** on reserved slots.
  - A trace lock shows "<name> is tracing <flow>".
- **Generate panel:**
  - On a shared plan the button reads **Reserve & generate**.
  - When the last sync failed, it shows the offline warning `"Offline: these cases are not reserved and may duplicate a teammate's"` before starting.
- **Results:** after a run, "N cases became suggested extras because a teammate wrote them first" when `extras_from_conflicts > 0`.
- **Test plans list:** a "Shared with the team" section lists registry plans that aren't local, each with **Open**, which pulls the plan and then opens it.

- [ ] **Step 1: Write the failing tests:**

```tsx
test("share_button_shares_and_shows_badge", ...)
test("people_strip_shows_written_and_reserved_per_person", ...)
test("lead_can_assign_a_flow_and_release_a_slot", ...)   // menu → api.assign called with {flow_ids:[..], email}
test("contributor_sees_no_lead_controls", ...)
test("reserve_and_generate_on_shared_plan_with_offline_warning", ...)
test("shared_plans_list_opens_by_pulling", ...)
```

- [ ] **Step 2: Run** `npm --prefix desktop test`. Expected: the new tests FAIL.
- [ ] **Step 3: Implement** the UI.
- [ ] **Step 4: Run** `npm --prefix desktop test && npx --prefix desktop tsc --noEmit -p desktop`. Expected: all pass.
- [ ] **Step 5: Commit** `feat(desktop): share test plans, people progress and lead assignment`.

### Task 8: Two-user whole-app journey and docs

**Files:**
- Modify: `desktop/e2e/app.spec.ts`, `tests/e2e_support/make_pr_home.py` (add a registry-backed variant) and `docs/test-planning.md`.

**The journey** (spec §10), against a real `kb-registry` started by `startRegistry`:
1. Profile A:
   - push `production`, find what changed, **Share with team**;
   - generate 5 P0.
2. Profile B (a second `KB_HOME`, signed in as a contributor):
   - pull `production`, open the shared plan;
   - generate 5 P1.
3. Both boards show 10 cases. The people strip shows A: 5 and B: 5. CSV export from B has 10 rows plus the header.

- [ ] **Step 1: Write** the `shared_test_plan_two_users` e2e test.
- [ ] **Step 2: Run** `npm --prefix desktop run build && xvfb-run -a npm --prefix desktop run e2e`. Expected: the new test passes, and all others still pass.
- [ ] **Step 3: Update** `docs/test-planning.md` with a "Working as a team" section covering share, reserve, assignment, offline and conflicts, and replace the "stage 5b" note.
- [ ] **Step 4: Run** the full suites:
  - `uv run pytest -q`;
  - `npm --prefix desktop test`;
  - the e2e suite.

  Expected: all pass.
- [ ] **Step 5: Commit** `test(e2e): two QAs share one test plan; docs: working as a team`.
