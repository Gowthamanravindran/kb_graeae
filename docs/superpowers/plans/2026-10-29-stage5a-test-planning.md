# Stage 5a: Test Planning (local) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give one user both test-planning features on their machine: **Test Plan for PR** (impact map, then chosen cases) and **Test Scenarios in a Branch**. This includes:
- exact case counts per priority before generation;
- generate-more;
- reconciliation when a PR gets new commits;
- CSV export, the CLI, and the desktop screens.

**Architecture:**
- Branch sets come first. Each repo gets its own branch in analysis and checkouts.
- New package `src/kb/testplan/` with two LangGraph graphs. **Impact** has no Claude calls except one naming call. **Design** fans out per flow and reuses the Ask agent's collect, trace and verify nodes against chosen checkouts.
- Records are pydantic models stored in the base analysis's overlay.
- `kb serve` exposes them as jobs and views; the desktop adds a **Test plans** view with two tabs, the Impact screen and the Plan board.

**Tech Stack:** Python 3.12, LangGraph (`Send` fan-out), tree-sitter (existing `kb.linkers.ts`), git worktrees, FastAPI, React 18 + Vitest, Playwright.

**Spec:** `docs/superpowers/specs/2026-10-29-test-planning-design.md` (§§1–6, 8–12, the 5a part of §13). The approved mocks are "KB Test Plans", version 4.

## Global Constraints

- Commit to `phase1` only; never push.
- Priorities are exactly `P0`, `P1`, `P2`, `P3`.
- Scenario types are exactly: `happy_path`, `branches_edges`, `errors_failures`, `regression` (`pr` mode only), `cross_side`.
- Inventory kinds are exactly: `branch`, `validation`, `permission`, `error`, `field`, `argument`, `route`, `screen`, `job`, `query`, `removed`. The plan adds one more, `code`, for languages without a decision extractor (Task 5 ruling).
- Slot statuses are exactly: `planned`, `reserved`, `written`, `stale`, `failed`, `obsolete`. In 5a, `reserved` means "in the running local job".
- Case ids are `TC-###` (three digits minimum, never reused within a plan).
- At most **15 slots per `cases` call**.
- Copy fixed by the spec:
  - "PR analysis needs git repos for: <names>"
  - "Analyse <base> first"
  - "Add GITHUB_TOKEN to ~/.kb/kb.env, or pick the branches yourself"
- Tab names: "Test Plan for PR" and "Test Scenarios in a Branch". The sidebar entry is "Test plans".
- CSV columns, in order: `ID, Flow, Scenario, Type, Priority, Title, Preconditions, Steps, Test data, Expected, Status, Author, Stale`.
  - The file is UTF-8 with a BOM.
  - List cells are joined as `1. …\n2. …`.
- Nothing calls Claude without the user starting generation, except `name_flows` (one call per impact run).
- The user's working copies are never switched: git reads go through worktrees under `$KB_HOME/work/<project>/worktrees/<repo>/<commit12>`.
- Existing behaviour keeps working: `kb analyze cfg --branch main`, packs keyed by branch, and all stage 1–3 tests.

## Review Focus

1. **A PR that renames or moves a file.** The changed functions must still be found, not reported as "all removed, all added". Pin it with `test_diff_follows_renames` in Task 3 (`git diff -M`).
2. **A repo in the change set whose PR branch is blank.** No diff, no worktree for a head that equals base, but flows still cross into it. Pin it with `test_blank_head_repo_contributes_no_diff_but_is_reachable` in Task 9.
3. **Generating with a cap smaller than one flow's P0 slots.** The highest-risk flow's P0 slots are taken first, and the count written equals the cap exactly. Pin it with `test_cap_takes_highest_risk_first` in Task 7.
4. **Claude returns fewer cases than the slots it was given, or cases for slot ids it wasn't given.** Missing slots go back to `planned` with reason "not returned"; unknown ids are dropped. Pin it with `test_cases_missing_and_unknown_slots` in Task 11.
5. **A new PR commit after cases were written.** Unchanged slots keep their cases, changed ones become `stale`, and gone ones become `obsolete`. Pin it with `test_reconcile_after_new_commit` in Task 9.

---

### Task 1: Branch sets for analysis

**Files:**
- Modify:
  - `src/kb/config.py`: `RepoConfig.default_branch: str | None = None`.
  - `src/kb/packs/model.py`: `Manifest.branches: dict[str, str] = {}`.
  - `src/kb/analyze.py`, `src/kb/cli.py` (`analyze`).
  - `src/kb/packs/store.py`: add `find_pack_by_branches`.
  - `src/kb/serve/app.py`: `AnalyzeIn` gains `branches: dict[str, str] | None = None` and `label: str | None = None`.
  - `src/kb/serve/views.py`: branch rows include `branches`.
  - `desktop/src/views/Library.tsx`: the analyse dialog gets per-repo inputs and a label, and rows show per-repo branches.
- Test: `tests/test_analyze.py`, `tests/test_cli.py`, `tests/serve/test_branches.py`, `desktop/src/views/Library.test.tsx`

**Interfaces:**
- Produces:
  - `BranchSet = dict[str, str]`, defined in `kb.config`.
  - `resolve_branch_set(cfg, branch_args: list[str]) -> tuple[str, BranchSet]`. A single `"main"` gives `("main", {every repo: "main"})`. `["sop-web=master", "sop-backend=develop"]` gives the label from `--label`, or raises `KbError("give --label for different branches per repo")`. Unknown repo names raise `KbError`.
  - `run_analyze(cfg, label: str, store, provider, tracer, resume=False, author=..., progress=None, branches: BranchSet | None = None)`. It checks out each repo at `branches[repo]` (default `label`), and records `Manifest.branches`.
  - `PackStore.find_pack_by_branches(project, branches: BranchSet) -> str | None`. It returns the newest label whose manifest `branches` match exactly. A manifest without `branches` matches `{repo: manifest.branch}`.
  - CLI: `kb analyze CFG --branch X [--branch repo=Y …] [--label L]`.
  - Desktop: `api.analyze(p, label, resume, branches?)`.

- [ ] **Step 1:** Write the failing tests:
  - `test_resolve_branch_set_single_and_per_repo`, covering both forms and both errors.
  - `test_analyze_records_branches_per_repo`: two fixture git repos on branches `master` and `develop`, label `production`. Expect `Manifest.branches == {"mini-fe": "master", "mini-be": "develop"}`, and the pack stored under `production`.
  - `test_find_pack_by_branches`.
  - Serve: `test_analyze_accepts_branch_map`.
  - Vitest: `analyse_dialog_per_repo_branches`. Typing `master` and `develop` plus label `production` calls `analyze("sop", "production", false, {"sop-web": "master", "sop-backend": "develop"})`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement. In the dialog, the per-repo inputs are pre-filled from `default_branch` (returned in `/api/projects` rows as `repos[].default_branch`); "Branch" stays for the single-name case.
- [ ] **Step 4:** Run the full pytest and vitest suites. Expected: pass, with stage 1–3 tests unchanged. Commit: `feat: branch sets — a branch per repo for analysis`.

### Task 2: Worktrees at exact commits

**Files:**
- Create: `src/kb/testplan/__init__.py`, `src/kb/testplan/worktrees.py`
- Test: `tests/testplan/test_worktrees.py`, `tests/testplan/fixture_repos.py`

**Interfaces:**
- Produces:
  - `git_source(repo: RepoConfig, work: Path) -> Path`, the repository to read refs from:
    - a `local_path` that is a git repo gives itself;
    - a `url` gives the managed clone (`kb.ingest.checkout` logic, fetched);
    - otherwise `None`.
  - `require_git(cfg, repos: list[str], work) -> None`, which raises `KbError("PR analysis needs git repos for: a, b")`.
  - `worktree_at(repo: RepoConfig, ref: str, work: Path) -> Checkout`. It resolves `ref` to a commit (`git rev-parse ref^{commit}`, and for url repos `origin/ref` too), then `git worktree add --detach <work>/worktrees/<repo>/<commit[:12]> <commit>`. It reuses an existing directory, applies `repo.path`, and never touches the user's working tree.
  - Test helper `make_pr_fixture(tmp: Path) -> PrFixture`, a dataclass with `cfg_path`, `web`, `be` (repo dirs), `base = {"mini-fe": "master", "mini-be": "develop"}` and `head_branch = "feature/repeat-end"`:
    - copies `tests/fixtures/mini-fe` and `mini-be` into git repos;
    - `master`/`develop` commit the fixtures, with the base backend also given the `notify` branch below;
    - the head branch applies the PR content below.

  Base `mini-be/src/module/tasks/task/resolvers.ts` resolve body:
  ```ts
  const task = await TaskModel.create(args.input);
  if (task.repeat) {
    scheduleRepeat(task);
  }
  if (task.notify) {
    notifyAssignee(task);
  }
  return task;
  ```
  (with `function notifyAssignee(task) { TaskQueue.add(TaskTopics.TASK_REPEAT, { notify: task._id }); }`)

  Head resolve body:
  ```ts
  if (!context.user.isAdmin) {
    throw new Error('Only admins can add tasks');
  }
  if (args.input.repeatEndsAt && args.input.repeatEndsAt < args.input.startsAt) {
    throw new Error('End date must be after the start date');
  }
  const task = await TaskModel.create(args.input);
  if (task.repeat === 'weekly') {
    scheduleRepeat(task, 7);
  }
  return task;
  ```
  Also on the head branch: `resolve: async ({ args, context })`, `scheduleRepeat(task, days)` using `{ delay: days * 86400000 }`, `notifyAssignee` deleted, and FE `TaskCreation.tsx` sending `variables: { input: { repeatEndsAt: null } }`.

- [ ] **Step 1:** Write the failing tests:
  - `test_worktree_at_does_not_touch_working_copy`: the user's repo is on `master` with an uncommitted edit; after `worktree_at(..., "feature/repeat-end")`, the repo is still on `master`, the edit is still there, and the worktree has the head file.
  - `test_require_git_lists_plain_folders`.
  - `test_worktree_reused_for_same_commit`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement.
- [ ] **Step 4:** Run them. Expected: PASS. Commit: `feat(testplan): worktrees at exact commits`.

### Task 3: Diff hunks

**Files:**
- Create: `src/kb/testplan/diff.py`
- Test: `tests/testplan/test_diff.py`

**Interfaces:**
- Produces:
  - `@dataclass Hunk(repo: str, file: str, old_file: str, old_start: int, old_len: int, new_start: int, new_len: int, added: list[str], removed: list[str])`.
  - `repo_diff(source: Path, repo: str, base: str, head: str, ignore: list[str]) -> list[Hunk]`. It runs `git diff -M -U0 --no-color base...head`, honours `repo.path` by filtering on that prefix and making paths relative to it, and skips files matching `cfg.ignore`.

- [ ] **Step 1:** Write the failing tests:
  - `test_repo_diff_fixture`: the backend gives hunks only in `resolvers.ts`. `added` includes `"  if (!context.user.isAdmin) {"`, and `removed` includes `"  if (task.notify) {"`.
  - `test_diff_follows_renames`: an extra commit renames `model.ts` to `task-model.ts` with one changed line, giving one hunk with `old_file="src/module/tasks/task/model.ts"`.
- [ ] **Step 2:** Run. Expected: FAIL. **Step 3:** Implement the parser for `@@ -a,b +c,d @@` headers and `rename from`/`rename to` lines. **Step 4:** PASS. Commit: `feat(testplan): git diff hunks`.

### Task 4: Head graph and graph delta

**Files:**
- Modify: `src/kb/analyze.py`, extracting `build_graph(cfg, checkouts: dict[str, Checkout], work: Path, tracer=None) -> GraphBuild`. `run_analyze` calls it; its behaviour is unchanged.
- Create: `src/kb/testplan/delta.py`
- Test: `tests/testplan/test_delta.py`, `tests/test_analyze.py` (still green)

**Interfaces:**
- Produces:
  - `@dataclass GraphBuild(graphs: dict[str, CodeGraph], merged: CodeGraph, links: list[CodeEdge], link_nodes: list[CodeNode])`.
  - `head_graph(cfg, checkouts, work: Path) -> CodeGraph`, cached as `work/head_graphs/<commits_key>.json`.
  - `@dataclass GraphDelta(nodes_added: list[str], nodes_removed: list[str], nodes_changed: list[str], edges_added: list[tuple[str, str, str]], edges_removed: list[tuple[str, str, str]], hunk_owner: dict[int, str])`. `hunk_owner` maps each hunk index to the innermost function/component node id on the side where its lines exist.
  - `graph_delta(base: CodeGraph, head: CodeGraph, hunks: list[Hunk]) -> GraphDelta`. A node counts as changed when a hunk overlaps its `line..end_line` (new side for head nodes, old side for base nodes).

- [ ] **Step 1:** Write the failing test `test_graph_delta_fixture`:
  - `nodes_changed` contains the backend `AddTask`/resolve function node and `scheduleRepeat`;
  - `nodes_removed` contains `notifyAssignee`;
  - the FE `TaskCreation` node is changed;
  - `edges_removed` contains the `notifyAssignee` call edge.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS, with `tests/test_analyze.py` green. Commit: `feat(testplan): head graph and graph delta`.

### Task 5: Change inventory

**Files:**
- Create: `src/kb/testplan/models.py`, which holds every record in spec §4 as pydantic models, plus `ImpactReport.inventory`; `src/kb/testplan/inventory.py`.
- Test: `tests/testplan/test_inventory.py`

**Interfaces:**
- Produces:
  - `InventoryItem(id, kind, change, repo, file, lines: tuple[int, int], function, summary, outcomes: list[str])`.
  - `extract_decisions(source: str, path: str, start: int, end: int) -> list[Decision]`, for `.ts/.tsx/.js/.jsx` via `kb.linkers.ts`.
  - `Decision(kind, text, outcomes, line)`.
  - `inventory(base_files, head_files, delta: GraphDelta, hunks) -> list[InventoryItem]`.
- Rules, decided here; the spec leaves the classifier open:
  - `if`/`switch`/ternary gives a `branch`. Outcomes are `["true", "false"]` for an `if` or ternary, and the case labels plus `"default"` for a `switch`.
  - An `if` whose block directly throws or returns an error, and whose condition matches `/\b(role|roles|permission|isAdmin|can[A-Z]\w*|authori[sz]e|guard)\b/`, gives a `permission` with outcomes `["allowed", "denied"]`.
  - Otherwise, an `if` whose block directly throws and whose condition reads `args.`/`input.`/`req.body`/`values.`, or calls `validate`/`isValid`, gives a `validation` with outcomes `["valid", "invalid"]`. A throw inside a permission or validation block is part of that item, not a separate `error`.
  - Any other `throw`, `new …Error(`, or `res.status(4xx|5xx)` gives an `error`.
  - Graph-delta nodes give the remaining kinds:
    - added or removed node kind `route` → `route`;
    - a component reachable from a route → `screen`;
    - a `queue_topic`/`handled_by` change → `job`;
    - a `resolver`/`graphql_op` argument change → `argument`;
    - a changed object key in a `variables:` literal → `field`;
    - `find`/`create`/`update`/`aggregate` calls on a model → `query`.
  - Base and head decisions of a function are aligned **within the same kind** by best text similarity (`difflib` ratio ≥ 0.6 on whitespace-collapsed text). An aligned pair with different text → `change: changed`. An unaligned head decision → `added`; an unaligned base decision → `removed`.
  - **Ruling (spec gap):** files in languages with no extractor give one `code` item per changed function (outcomes `["changed"]`).
  - `id = sha1(f"{repo}|{file}|{function}|{kind}|{normalised text}")[:12]`.

- [ ] **Step 1:** Write the failing test `test_inventory_fixture`. The kinds multiset is exactly `{permission:1, validation:1, branch:1, removed:1, field:1}`:
  - the branch has `change == "changed"` and outcomes `["true", "false"]`;
  - the permission summary mentions `isAdmin`;
  - the removed item's summary mentions `notify`.

  Also write `test_inventory_ids_stable_across_whitespace` and `test_unknown_language_gives_code_item`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): change inventory`.

### Task 6: Reach — impact flows

**Files:**
- Create: `src/kb/testplan/reach.py`
- Test: `tests/testplan/test_reach.py`

**Interfaces:**
- Produces: `reach(head: CodeGraph, base_pack, changed: list[str], removed: list[str], inventory) -> list[ImpactFlow]`. It walks callers (incoming `calls`/`contains`/`renders` edges) and linker edges backwards (`handled_by`, `sends_graphql`, `routes_to`, `consumed_by`, `enqueues`), across repos, until **top entry points**:
  - nodes of kind `route`;
  - `queue_topic` consumers;
  - resolvers with no incoming `handled_by` from any `graphql_op` used by a component.

  There is one `ImpactFlow` per top entry point. Its fields:
  - `changed_functions`: the changed or removed nodes it reaches;
  - `inventory_ids`: the items in those functions;
  - `modules`: base modules owning its nodes;
  - `master_flow_id`: the newest saved base `Flow` whose cited files include one of the flow's changed functions' files, else `None`;
  - `path`: an ordered list of node ids from the entry to the deepest changed node (used by slot planning for regression).
- Removed nodes are looked up in the base graph.

- [ ] **Step 1:** Write the failing test `test_reach_fixture`: exactly one flow, entry `route` `/tasks/create-task`, whose `changed_functions` include both the FE component and the backend resolver (the FE↔BE link was crossed), and all 5 inventory ids. Also `test_resolver_without_ui_is_own_entry`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): reach changed code to entry points`.

### Task 7: Slots, priorities, risk and selection

**Files:**
- Create: `src/kb/testplan/slots.py`
- Test: `tests/testplan/test_slots.py`

**Interfaces:**
- Produces:
  - `plan_slots(flows, inventory, mode: Literal["pr", "scenario"]) -> list[CaseSlot]`. The rules are spec §6.1. Regression slots come from contiguous runs of **unchanged** nodes on `flow.path`: one slot per run, at most 3, and `pr` mode only. A cross-side slot is added when the path crosses repos or contains a `queue_topic`.
  - `risk(flow, inventory) -> tuple[float, list[str]]`. The score is `3·removed + 3·permission + 2·(crosses repos) + 2·(queue) + 1·(no master flow) + 0.5·(inventory items)`. Each term that applies contributes its reason text: "removed behaviour", "permission change", "crosses repos", "background job", "no master flow".
  - `assign_priorities(slots, flows)`. It applies spec §6.3:
    - "top third" is the flows ranked by risk, using `ceil(n/3)`;
    - the money/deletion heuristic matches changed function or file names against `/delete|remove|destroy|payment|refund|invoice|charge/i`.
  - `select(slots, flows, counts: dict[str, int], per_flow_cap: int | None) -> list[CaseSlot]`. It takes `planned` slots ordered by `(priority, -flow risk, slot id)`, up to `counts[p]` per priority and `per_flow_cap` per flow.
  - `estimate_calls(selected, flows, have_trace: set[str], have_scenarios: set[tuple[str, str]]) -> int`, using spec §8.
  - Slot id: `sha1(f"{flow_id}|{type}|{inventory_id or '-'}|{outcome}")[:12]`.

- [ ] **Step 1:** Write the failing tests:
  - `test_fixture_slot_counts`. Non-regression slots per priority are exactly: P0 = 4 (permission ×2, removed ×1, happy path ×1, since the single flow is in the top third), P1 = 3 (branch ×2, field ×1), P2 = 2 (validation ×2), P3 = 1 (cross-side). Regression slots are 1–3, all P3.
  - `test_cap_takes_highest_risk_first`: two flows with P0 slots 4 and 3 and risks 9 and 2, `counts={"P0": 5}`. Expect all 4 from the high-risk flow plus 1, and exactly 5 total.
  - `test_per_flow_cap`.
  - `test_estimate_calls_counts_reuse`.
  - `test_slot_ids_stable`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): case slots, priorities and selection`.

### Task 8: Test plan store

**Files:**
- Create: `src/kb/testplan/store.py`
- Test: `tests/testplan/test_store.py`

**Interfaces:**
- Produces: `PlanStore(pack_store: PackStore)` with:
  - `dir(project, label, plan_id) -> Path`, which is `<pack>/overlay/testplans/<plan_id>/`. `plan_id` is checked with the existing `is_safe_id`.
  - `save_change_set` / `load_change_set`.
  - `save_impact(report)` / `load_impact(...)`.
  - `save_slots(list)` / `load_slots(...)`, as a single `slots.json`, under the stage-polish overlay lock.
  - `save_scenario` / `scenarios(...)`.
  - `save_case` / `cases(...)`.
  - `next_case_id(...) -> str`, backed by a counter file.
  - `list_plans(project) -> list[PlanSummary]`, scanning every pack of the project.
  - `PlanSummary(id, kind: "pr" | "scenario", title, label, base, head, counts_by_status, counts_by_priority, updated_at)`.

- [ ] **Step 1:** Write the failing tests: `test_round_trip_all_records`, `test_next_case_id_never_reused` (also after a case is deleted), and `test_unsafe_plan_id_refused`, which uses `tmp_path` paths only.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): plan store`.

### Task 9: Graph 1 — Impact

**Files:**
- Create: `src/kb/testplan/graph_impact.py`, `src/kb/testplan/nodes_impact.py`, `src/kb/testplan/github.py`
- Test: `tests/testplan/test_graph_impact.py`

**Interfaces:**
- Consumes: Tasks 1–8.
- Produces:
  - `ChangeSetRequest(project, base: BranchSet, head: dict[str, str | None], pr_links: dict[str, str])`. A `None` or empty head means unchanged.
  - `run_impact(cfg, req, store, provider, tracer, progress=None) -> ImpactReport`. It runs the graph `resolve_change_set → (diff ∥ head_graph) → graph_delta → inventory → reach → name_flows → plan_slots → rank → full_reanalysis_check → save_impact`, built with `StateGraph` like `kb.agent.graph`.
  - `pr_info(url: str) -> PrInfo(title, body, issues, repo_full_name, base, head)` via GitHub REST `GET /repos/{o}/{r}/pulls/{n}`, with a `GITHUB_TOKEN` bearer token when set. It raises `KbError` with the spec copy when the token is missing for a 404/401.
  - `name_flows` makes one `ask_json` call with model `FlowNames(names: dict[str, str])`. When the LLM is unavailable, the names are the entry point labels.
  - `full_reanalysis_check`: set the flag when more than 30% of changed lines fall in files that belong to no base module member, or when a new top-level folder appears.
  - Reconciliation: when the change set already has slots and cases, re-planned slots with the same id keep `written` (and `case_id`). Slots whose inventory item is unchanged but whose flow path functions changed become `stale`. Old slot ids that no longer exist become `obsolete`; their cases are kept.

- [ ] **Step 1:** Write the failing tests with a fake provider:
  - `test_impact_fixture_end_to_end`: the report has 1 flow, 5 inventory items and the Task 7 counts, and is saved.
  - `test_impact_refuses_without_base_pack`: "Analyse production first".
  - `test_impact_refuses_plain_folders`.
  - `test_blank_head_repo_contributes_no_diff_but_is_reachable`: head only for the backend; FE hunks are empty; the flow's entry is still the FE route.
  - `test_reconcile_after_new_commit`: write cases for 3 slots, add a commit changing only the validation message, and re-run. The validation slots become `stale`, the others stay `written`. A commit deleting the permission check makes the permission slots `obsolete`.
  - `test_name_flows_falls_back_without_claude`.
  - `test_pr_link_fills_branches` (with a monkeypatched HTTP response).
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): impact graph`.

### Task 10: Ask nodes accept chosen checkouts and extra trace context

**Files:**
- Modify: `src/kb/agent/nodes.py` (`trace`: when `deps.extra.get("trace_context")` is set, append it to the prompt before the closing instruction).
- Test: `tests/test_agent_nodes.py`

**Interfaces:**
- Produces: `deps.extra["trace_context"]: str`, honoured by `trace`. Nothing else changes.
- [ ] **Step 1:** Write the failing test `test_trace_includes_extra_context`: the recording provider's prompt contains `"CHANGED CODE"` when it is set.
- [ ] **Step 2–4:** FAIL → implement → PASS (the full agent suite stays green). Commit: `feat(agent): extra trace context hook`.

### Task 11: Graph 2 — per-flow design nodes

**Files:**
- Create: `src/kb/testplan/nodes_design.py`
- Test: `tests/testplan/test_nodes_design.py`

**Interfaces:**
- Consumes: `kb.agent.nodes.collect/trace/verify` (with `AgentDeps(checkouts=…)`), plus Tasks 5–8.
- Produces:
  - `trace_flow(flow, checkouts, base_pack, provider, tracer, context: str) -> Flow`. It builds `AgentDeps` with the given checkouts, sets the question to `"How does: <flow.name>"`, sets `module_ids = flow.modules`, and runs collect → trace → verify. The result is saved like a normal flow.
  - `baseline_flow(work) -> Flow` reuses `master_flow_id` if it isn't stale, else calls `trace_flow` on base worktrees. `head_flow(work) -> Flow` calls `trace_flow` on head worktrees with context `CHANGED CODE` + hunks + full changed functions + `"Include a step for each of these decision points: <summaries>"`.
  - `compare(base: Flow | None, head: Flow) -> FlowDiff(added, removed, changed, unchanged: list[int])`, aligning by `(repo, file)` plus overlapping lines, then `difflib` ratio ≥ 0.6 on text.
  - `write_scenarios(work, slots) -> list[Scenario]`: one `ask_json` call returning `ScenariosOut(scenarios: [{type, title, description, inventory_ids, step_refs}])`. Types missing for given slots are filled with a generic title. It reuses existing scenarios per `(flow, type)`.
  - `write_cases(work, scenarios, slots) -> tuple[list[TestCase], list[TestCase]]` (written, extras): `ask_json` per batch of ≤15 slots, returning `CasesOut(cases: [{slot_id, title, preconditions, steps, test_data, expected}], extras: [...])`.
  - `check_cases(cases, slots, base, head) -> tuple[list[TestCase], dict[str, str]]` (kept, rejected slot id → reason). A case is rejected when it has empty `steps` or empty `expected`, or when its `slot_id` isn't among the given slots.

- [ ] **Step 1:** Write the failing tests with a scripted fake:
  - `test_compare_marks_added_removed_unchanged`.
  - `test_baseline_reuses_saved_flow` (no Claude call).
  - `test_head_prompt_lists_decision_points`.
  - `test_cases_one_per_slot`.
  - `test_cases_missing_and_unknown_slots`: given 3 slots, the fake returns 2 of them plus one unknown id. The result is 2 cases, the third slot rejected with reason "not returned", and the unknown dropped.
  - `test_check_cases_rejects_empty_expected`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): design nodes per flow`.

### Task 12: Graph 2 — fan-out, modes, coverage

**Files:**
- Create: `src/kb/testplan/graph_design.py`
- Test: `tests/testplan/test_graph_design.py`

**Interfaces:**
- Produces:
  - `run_design(cfg, plan_id, label, store, provider, tracer, mode: Literal["pr", "more", "scenario"], counts: dict[str, int], per_flow_cap: int | None, progress=None) -> DesignResult(written: int, failed: int, extras: int, coverage: Coverage)`.
  - Graph: `plan_work → Send("flow", work) per flow → merge → coverage → save_plan`. The `flow` node is a compiled subgraph: `(baseline_flow ∥ head_flow) → compare → scenarios → cases → check_cases`. In scenario mode it is `head_flow → scenarios → cases → check_cases`.
  - Selected slots are marked `reserved` before fan-out.
  - Each flow's results are saved when that flow finishes.
  - An exception inside a flow marks that flow's reserved slots `failed` with `fail_reason=str(e)`, and the others finish.
  - `merge` assigns case ids via `next_case_id`, and merges extras whose titles match after lowercasing.
  - `Coverage(items: {inventory_id: "covered" | "skipped" | "failed" | "uncovered"}, by_priority, by_status)`.
  - `more` mode is `pr` mode restricted to `planned` and `failed` slots.
  - Progress: `"Tracing <flow> on the PR branch (i/n)"`, `"Writing cases for <flow> (i/n)"`.

- [ ] **Step 1:** Write the failing tests:
  - `test_design_generates_exact_counts`: `counts={"P0": 2}` on the fixture writes exactly 2 cases, both P0. The rest stay `planned`.
  - `test_more_continues_by_priority`: after that, `more` with `{"P0": 2, "P1": 3}` writes the remaining P0s and then P1s.
  - `test_one_flow_failing_does_not_stop_others`, using a two-flow fixture variant.
  - `test_coverage_counts_skipped_vs_failed`.
  - `test_langfuse_span_per_flow`, using `RecordingTracer`.
- [ ] **Step 2:** FAIL. **Step 3:** Implement. **Step 4:** PASS. Commit: `feat(testplan): design graph with fan-out`.

### Task 13: Scenario mode (Feature A)

**Files:**
- Create: `src/kb/testplan/scenario.py`
- Test: `tests/testplan/test_scenario.py`

**Interfaces:**
- Produces:
  - `plan_scenario(cfg, label, store, provider, tracer, text: str | None, answer_id: str | None) -> ScenarioPlan(plan_id, flow, slots, counts)`. It finds or traces the flow as `run_ask` does: a reused flow costs 0 calls, otherwise 1. It then builds a pseudo `ImpactFlow` from the cited functions, runs `extract_decisions` on the full text of each cited function as `added` items, and calls `plan_slots(..., mode="scenario")`.
  - Generation is `run_design(mode="scenario")`.

- [ ] **Step 1:** Write the failing tests:
  - `test_scenario_plan_counts_before_generation`: the fixture's base flow gives slots with no `regression` type, and the counts are returned without any `cases` call.
  - `test_scenario_from_answer_reuses_flow` (zero trace calls).
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(testplan): scenario test plans`.

### Task 14: CSV export and CLI

**Files:**
- Create: `src/kb/testplan/export.py`
- Modify: `src/kb/cli.py` (a `testplan` sub-app)
- Test: `tests/testplan/test_export.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `export_csv(plan, filters: {priorities?, flow_ids?, statuses?}) -> bytes`, with the columns and format in Global Constraints. Only `written` and `stale` cases are exported; extras are excluded unless accepted.
  - The CLI:
    - `kb testplan pr CFG --base repo=branch… --head repo=branch… [--pr URL…]`
    - `kb testplan scenario CFG --branch LABEL "text"`
    - `kb testplan generate CFG PLAN_ID --p0 N --p1 N --p2 N --p3 N [--per-flow N]`
    - `kb testplan status CFG PLAN_ID`
    - `kb testplan export CFG PLAN_ID -o file.csv [--priority P0 …]`
- [ ] **Step 1:** Write the failing tests:
  - `test_csv_columns_bom_and_lists`: the bytes start with `\xef\xbb\xbf`; the header is exact; steps render as `"1. Open Tasks\n2. Save"`.
  - `test_csv_filters`.
  - `test_cli_pr_then_generate_then_export`, with the fake provider via `KB_SERVE_PROVIDER`-style injection, as used in the stage-1 CLI tests.
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(testplan): CSV export and CLI`.

### Task 15: kb serve routes

**Files:**
- Modify: `src/kb/serve/app.py`
- Create: `tests/serve/test_testplans.py`

**Interfaces:**
- Produces:
  - `GET /api/projects/{p}/testplans?kind=pr|scenario` returns `[PlanSummary]`.
  - `POST /api/projects/{p}/testplans/pr` takes `{base, head, pr_links}` and returns `{job_id, plan_id}`.
  - `POST /api/projects/{p}/testplans/scenario` takes `{label, text?, answer_id?}` and returns `{job_id, plan_id}`.
  - `GET …/testplans/{id}` returns `{summary, change_set, impact?, slots, scenarios, cases, extras, coverage}`.
  - `POST …/testplans/{id}/generate` takes `{counts, per_flow_cap?}` and returns `{job_id}`.
  - `POST …/testplans/{id}/extras/{case_id}/accept|dismiss`.
  - `POST …/testplans/{id}/slots/{slot_id}/retry` → job.
  - `GET …/testplans/{id}/export.csv?priority=&flow=&status=` returns `text/csv` with `Content-Disposition: attachment; filename="<project>-<plan>-test-cases.csv"`.
- Job keys: `testplan:<project>:<plan_id>`, so one job per plan at a time (409 otherwise). The scope is the base label's branch scope, shared (not exclusive).
- [ ] **Step 1:** Write failing tests for each route on the fixture with the fake provider, plus `test_generate_409_while_running` and `test_export_headers`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(serve): test plan routes`.

### Task 16: Desktop — Test plans list with two tabs

**Files:**
- Create: `desktop/src/views/TestPlans.tsx`, `desktop/src/views/TestPlans.test.tsx`
- Modify: `desktop/src/api/client.ts`, `desktop/src/api/types.ts`, `desktop/src/state.tsx` (view `"testplans"`, `testplanId`), `desktop/src/components/Sidebar.tsx`, `desktop/src/App.tsx`

**Interfaces:**
- Produces: client methods `testplans(p, kind)`, `newPrPlan(p, body)`, `newScenarioPlan(p, body)`, `testplan(p, id)`, `generate(p, id, counts, cap?)`, `acceptExtra`, `dismissExtra`, `retrySlot`, and `exportUrl(p, id, filters)`. The renderer saves the CSV with `window.kb.saveFile(defaultName, bytes)`, a new IPC that calls `dialog.showSaveDialog` and writes the file.
- The UI follows the mock:
  - the tablist "Test Plan for PR" / "Test Scenarios in a Branch", with counts;
  - the PR form is a per-repo table (Base pre-filled from `default_branch`, PR branch placeholder "No changes", PR link). It shows the line "Base analysis found: <label> (…)", or "Analyse <base> first" with an **Analyse** button;
  - the scenario form takes scenario text plus an analysed-branch select;
  - the lists show progress bars.
- [ ] **Step 1:** Write the failing tests:
  - `tabs_switch_lists`.
  - `pr_form_sends_branch_sets`: a blank PR branch for the backend sends `head: {"sop-web": "feature/x", "sop-backend": null}`.
  - `missing_base_analysis_offers_analyse`.
  - `scenario_form_starts_plan`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(desktop): test plans list`.

### Task 17: Desktop — Impact screen

**Files:**
- Create: `desktop/src/views/Impact.tsx`, `desktop/src/views/Impact.test.tsx`

- The UI follows the mock:
  - a summary strip (functions changed, repos, decision points, flows, cases planned);
  - the full re-analysis banner, whose button calls `analyze` with the head branch set and label `<plan_id>-head`;
  - the ranked flow list with checkboxes, risk chips, the "Reuses master flow"/"Needs master trace" chip, and expandable inventory;
  - the generation panel with number inputs per priority (max = planned), a per-flow cap, and a live "Will generate X of Y · about N Claude calls". The estimate comes from `slots` + `impact.flows[].est_claude_calls` recomputed client-side with the Task 7 formula, ported to TypeScript as `estimateCalls`, with a vitest test that matches the Python test's values;
  - the button "Generate" (5a has no reservations; the label becomes "Reserve & generate" in 5b).
  - When `status.claude.available` is false, Generate is disabled and the existing Claude banner text is shown (spec §10). The impact itself still loads.
- [ ] **Step 1:** Write the failing tests:
  - `counts_update_live`.
  - `generate_disabled_without_claude`.
  - `unticking_flow_reduces_planned`.
  - `generate_calls_api_with_counts`: `generate("sop", "cs1", {"P0": 30, "P1": 0, "P2": 0, "P3": 0}, null)`.
  - `estimate_matches_python_fixture`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(desktop): impact screen`.

### Task 18: Desktop — Plan board, "Make test plan", Library count

**Files:**
- Create: `desktop/src/views/PlanBoard.tsx`, `desktop/src/views/PlanBoard.test.tsx`
- Modify: `desktop/src/views/Ask.tsx`, `desktop/src/views/Memory.tsx` (a **Make test plan** button calling `newScenarioPlan({label, answer_id})`), `desktop/src/views/Library.tsx` (test plan count per branch), `src/kb/serve/views.py` (`testplans` count in branch rows)

- The board follows the mock without the people strip:
  - per-priority progress;
  - a coverage strip;
  - cases grouped by flow, then scenario type, with expandable details;
  - slot states (planned, generating, written, stale with **Regenerate**, failed with **Retry**, obsolete);
  - the extras tray with Accept/Dismiss;
  - a priority filter;
  - **Export CSV** (follows filters, saved through `window.kb.saveFile`);
  - **Generate more**, which opens the Impact generation panel, or for scenario plans an inline panel with the same inputs.
  - A running job shows its step and resumes polling after navigation (as in Ask), using `testplanJobId` in app state.
- [ ] **Step 1:** Write the failing tests:
  - `board_groups_and_expands`.
  - `filter_applies_to_export`: export is called with `{priority: ["P0"]}`.
  - `stale_regenerate_calls_retry`.
  - `make_test_plan_from_answer`.
  - `library_shows_plan_count`.
- [ ] **Step 2–4:** FAIL → implement → PASS. Commit: `feat(desktop): plan board and entry points`.

### Task 19: End-to-end and docs

**Files:**
- Modify: `desktop/e2e/app.spec.ts`, `desktop/e2e/fixtures.ts` (a `makePrHome()` that builds the Task 2 fixture repos and analyses `production` through the fake provider), `tests/e2e_support/e2e_fakes.py` (scripted replies for `name_flows`, scenarios and cases)
- Create: `docs/test-planning.md`, covering both tabs, branch sets, `default_branch`, `GITHUB_TOKEN`, caps, generate more, CSV, the CLI, and the Mac acceptance steps from spec §11.

- [ ] **Step 1:** Write the E2E `pr_test_plan_journey`:
  - Test plans → PR tab → fill bases `master`/`develop` and head `feature/repeat-end` for both repos → "Find what changed";
  - the impact screen shows 1 flow and 5 decision points;
  - set P0 to 2 → Generate → the board shows 2 written P0 cases → Export CSV writes a file with 3 lines (header + 2);
  - Generate more P1 → 3 more cases.

  Also write `scenario_test_plan_journey`.
- [ ] **Step 2:** Run under `xvfb-run -a`. Expected: FAIL until wired, then PASS.
- [ ] **Step 3:** Run all suites, then the AppImage smoke. Write the ledger. Commit: `test(desktop): test planning journeys; docs`.
