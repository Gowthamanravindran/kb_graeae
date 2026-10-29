# Test planning: scenario test plans and PR impact test cases — design

Date: 2026-10-29 · Status: draft for review · Builds on: `2026-10-28-codebase-knowledge-system-design.md` (stages 1–3)

## 1. Goal

Give a manual QA team readable, code-grounded test cases, in two ways:

- **A. Scenario → test plan.** From a typed scenario ("Admin schedules a repeating task") or a saved answer, produce a test plan for that flow on one branch.
- **B. PR → test cases.** Pick a base branch (the production baseline, usually `master`) and a PR branch per repo (or paste a GitHub PR link). KB diffs them, finds every flow the change reaches, and turns the differences into scenarios and test cases, covering every changed decision point.

Success means:
- QA can see **exactly how many cases are planned per priority before any Claude call**, and generate only what they choose (for example 30 P0 now, the rest later).
- Generation work is **shared through the registry**, so several QAs split it without anyone repeating the analysis, a trace or a case.
- "All possibilities" is **measurable**: every changed decision point has a case, or is listed as skipped or failed.

### What the user decided

| Topic | Decision |
|---|---|
| Who reads the output | Manual QA team: preconditions, numbered plain-English steps, test data, expected result |
| Export | Excel / CSV, one row per case |
| How a PR is chosen | Branch names per repo (always works), **or** a GitHub PR link (adds title, description, linked issues as intent) |
| Multi-repo changes | One **change set**: a branch per repo, analysed together, so FE↔BE flows are tested end to end |
| Base per repo | Each repo has **its own base branch** (e.g. sop-web `master`, sop-backend `develop`). A repo whose PR branch is left blank is unchanged and uses its base as head |
| Control | A cheap impact map first; the user chooses flows and **how many cases per priority** |
| Priorities | P0, P1, P2, P3 |
| Splitting work | Self-serve reservations **and** lead assignment |
| Approach | Baseline pack + a cheap PR graph (no full re-analysis), deep tracing of chosen flows, a change inventory, fixed scenario types, a coverage check; full re-analysis of the PR only when a large new area appears (opt-in) |

## 2. Prerequisites and limits

- Feature B needs **git checkouts** with history for every repo in the change set. A `local_path` folder without `.git` gets a clear refusal: "PR analysis needs git repos". Feature A works on any analysed branch.
- The base branch must already have an analysis (pack). If not, KB says so and offers to analyse it.
- Slot rules count what the **code** makes testable. Business cases with no code behind them (for example a leap-year month end the code does not special-case) can only arrive as *suggested extras* (§6.4).

## 2a. Branch sets (per-repo branches)

The repos are separate, so their branches are independent. A **branch set** maps each repo to a branch, for example `{sop-web: master, sop-backend: develop}`.

- **Defaults in kb.yaml.** A new optional `default_branch` per repo (in `repos:`) fills in the base and avoids typing it each time. Without it, the base box is empty until chosen.
- **Analysis.**
  - `kb analyze` and the desktop **Analyse a branch** dialog accept a branch set: `--branch sop-web=master --branch sop-backend=develop`. A single `--branch main` still means "the same name in every repo", as today.
  - The pack is stored under a **label** (default: the branch name when all repos share it, otherwise the user names it, e.g. `production`).
  - The manifest records the branch and commit per repo. The Library shows the per-repo branches under the label.
- **Finding the base analysis.** For a change set, KB looks for a pack whose manifest has the same branch per repo, and prefers the most recent one. If none exists, KB offers to analyse that branch set first.
- **Unchanged repos.** The user leaves a repo's PR branch blank when the PR doesn't change it. The repo is still part of the change set, so cross-repo flows reach into it, but it contributes no diff.
- **PR links.** A GitHub PR belongs to one repo, so a link fills that repo's base and head from the PR. For a change spanning two repos, paste one link per repo, or pick branches.
- **Git checkouts.** Base and head of a repo are read from separate git worktrees under `$KB_HOME/work`. KB never switches the branch of the user's own working copy. A `local_path` repo must be a git repo for Feature B, and its worktrees are made from it.

This is a change to the stage-1 analysis (checkout per repo, manifest, Library display) and is part of stage 5a.

## 3. Architecture

Two LangGraph graphs with a saved **Impact Report** between them. The user's choice of flows and counts happens between the two, possibly hours later or after a restart, so the handoff is a stored record, not a suspended graph. Both graphs run as `kb serve` background jobs (stage 3 `JobRunner`), traced to Langfuse (one trace per run, one span per flow).

Code lives in `src/kb/testplan/`:
- `graph_impact.py`, `graph_design.py` — the two graphs.
- `nodes_impact.py`, `nodes_design.py` — their nodes.
- `diff.py` — git diff and hunks.
- `delta.py` — graph comparison.
- `inventory.py` — decision-point extraction (tree-sitter).
- `slots.py` — slot rules and priorities.
- `store.py` — persistence on the pack.
- `export.py` — CSV.

They reuse stage-1 pieces: checkout, the Graphify adapter, linkers, the pack store, the Ask agent's `collect → trace → verify`, and `ask_json`.

### 3.1 Graph 1 — Impact (no Claude except one batched naming call)

```
START → resolve_change_set → (diff ∥ head_graph) → graph_delta → inventory → reach
      → name_flows → plan_slots → rank → [needs_full_reanalysis?] → save_impact → END
```

| Node | Does | Claude |
|---|---|---|
| `resolve_change_set` | A base branch per repo and a PR branch where one is given (a blank PR branch means unchanged: head = base), or GitHub PR links (one per repo), → base and head commits per repo, read from worktrees (§2a). Finds the base pack by its per-repo branches. A PR link also yields title, body and linked issues (GitHub REST, token from `kb.env` `GITHUB_TOKEN`). Refuses without git history, or without a base pack. | no |
| `diff` | `git diff base...head` per repo → hunks (file, old and new ranges, lines). | no |
| `head_graph` | Graphify + linkers on the head checkout (the no-LLM part of `kb analyze`), cached by head commits under `$KB_HOME/work`. | no |
| `graph_delta` | Base pack graph vs head graph: added, removed and changed nodes (by id and content hash) and edges, including FE↔BE links. Maps each hunk to the innermost enclosing function or component. | no |
| `inventory` | Changed decision points in hunks and changed functions → `InventoryItem`s (§4). Uses tree-sitter on both versions of each changed function. | no |
| `reach` | From each changed node, walks callers and FE↔BE links up to entry points (routes and screens, resolvers, queue consumers, scheduled jobs). Each entry → changed-code path is an `ImpactFlow`. Links each flow to base modules and to a fresh saved base flow if one covers it (`master_flow_id`). | no |
| `name_flows` | One batched `ask_json` call: plain-English flow names from entry points, changed functions and PR intent. Without Claude, falls back to entry-point names. | 1 |
| `plan_slots` | Slot rules (§6) → `CaseSlot`s with priorities; exact counts per priority and per flow. | no |
| `rank` | Risk score and reasons per flow; Claude-call estimate per flow (§8). | no |
| `needs_full_reanalysis` | If more than 30% of changed lines fall outside every base module, or a new top-level folder appears, set the flag and offer a background `kb analyze` of the head branch. | no |
| `save_impact` | Writes the `ImpactReport`, the slots and the change set. | no |

### 3.2 Graph 2 — Test design (fan-out per flow)

```
START → plan_work → Send(flow_subgraph) × N → merge → coverage → save_plan → END
flow_subgraph: (baseline_flow ∥ head_flow) → compare → scenarios → cases → check_cases
```

**Modes.**
- `pr` fills chosen slots of an impact report.
- `more` fills the next slots by priority, for "generate next" and "fill gaps".
- `scenario` is Feature A (§3.3).

| Node | Does | Claude |
|---|---|---|
| `plan_work` | Loads the report and the **reserved** slots (§7); groups them by flow; skips a flow whose traces and scenarios already exist on the registry or locally. | no |
| `baseline_flow` | Base version of the flow: reuses `master_flow_id` if fresh; otherwise Ask's collect → trace → verify on the base checkout. Shared once traced. | 0–1 |
| `head_flow` | The same journey on the head checkout. The prompt includes the diff hunks, the **full** changed functions and the flow's inventory items, and requires a step for each item reached. Verify checks every citation against head code. Shared once traced. | 1 |
| `compare` | Aligns base and head steps (by code location, then text similarity) → added, removed, changed and unchanged steps, and changed conditions. | no |
| `scenarios` | `Scenario`s for the flow's slots, by the five types (§6.2), each naming its inventory items and step references. Shared and reused per (flow, type). | 1 per flow |
| `cases` | Writes **exactly one `TestCase` per given slot**: title, preconditions, numbered steps, test data, expected result. It may also return *suggested extras*, kept apart from slots. Batches of up to 15 slots per call. | 1 per batch |
| `check_cases` | Rejects cases whose code references or inventory ids do not exist in either flow, or with no steps or no expected result. A rejected slot goes back to `planned`, with the reason recorded. | no |
| `merge` | Collects per-flow results; merges duplicate extras; assigns stable case ids (`TC-###`, never reused). | no |
| `coverage` | For every inventory item: covered, skipped (by the user's choice), failed or uncovered; counts per priority, status and person. | no |
| `save_plan` | Writes scenarios, cases and slot states; publishes them when the change set is shared (§7). | no |

A failure inside one flow's subgraph marks that flow's slots `failed` with the reason, and the other flows still finish. Each flow's results are saved as soon as that flow finishes, so a rerun resumes.

### 3.3 Feature A — scenario mode

The input is a `ScenarioRequest`: scenario text, or an answer id, and the branch.
- `plan_work` finds the flow as Ask does (module match, reuse of a saved flow, or a trace).
- Then `inventory` and `plan_slots` run **on the functions that flow cites**, instead of a diff.
- The user sees the planned counts per priority and sets them before generation.
- `baseline_flow` and `compare` are skipped, and `regression` is not used as a scenario type.
- Everything else is identical.

## 4. Data model

Stored in the pack overlay of the **head** branch (`testplans/<change_set_id>/…` JSON), and published to the registry as overlay items of new kinds.

- **ChangeSet** — `id`, `project`, `base {repo: {branch, commit}}`, `head {repo: {branch, commit}}` (head = base for an untouched repo), `base_pack` (label of the matching analysis), `prs {repo: {url, title, body, issues}}`, `created_by`, `created_at`.
- **ImpactReport** — one per head commit set. Fields:
  - `change_set_id`, `head_commits`;
  - `graph_delta {nodes_added, nodes_removed, nodes_changed, edges_added, edges_removed}`;
  - `inventory [InventoryItem]`, `flows [ImpactFlow]`;
  - `needs_full_reanalysis {flag, reason}`, `created_by`, `created_at`.
- **InventoryItem** — fields:
  - `id`: stable; a hash of repo, file, enclosing function and normalised condition or field text;
  - `kind` ∈ {branch, validation, permission, error, field, argument, route, screen, job, query, removed};
  - `change` ∈ {added, changed, removed}, `repo`, `file`, `lines`, `function`, `summary` (plain English);
  - `outcomes`: for branches, the branch outcomes; for validations, the rules.
- **ImpactFlow** — `id`, `name`, `entry_points`, `changed_functions`, `inventory_ids`, `modules`, `master_flow_id?`, `risk {score, reasons}`, `est_claude_calls`, `trace {base_flow_id?, head_flow_id?}`.
- **CaseSlot** — fields:
  - `id`: stable; `flow_id + scenario_type + inventory_id + outcome`;
  - `flow_id`, `scenario_type`, `inventory_ids`, `priority` (P0–P3), `rule`;
  - `status` ∈ {planned, reserved, written, stale, failed, obsolete};
  - `reserved_by?`, `reserved_until?`, `assigned_to?`, `case_id?`, `fail_reason?`.
- **Scenario** — `id`, `flow_id`, `type`, `title`, `description`, `inventory_ids`, `step_refs`, `author`.
- **TestCase** — `id` (`TC-###`), `slot_id?`, `suggested` (bool), `scenario_id`, `title`, `priority`, `preconditions [str]`, `steps [str]`, `test_data [str]`, `expected [str]`, `code_refs [Citation]` (hidden from QA), `author`, `created_at`, `stale`.
- **TestPlan** — not stored; a view over one change set's slots, scenarios and cases. CSV export comes from it.
- **ScenarioRequest** (Feature A) — `id`, `project`, `branch`, `text?`, `answer_id?`, `created_by`. It plays the role of the ChangeSet: no base, no graph delta.

## 5. Updates to a PR

A new head commit re-runs Graph 1 (it is cheap) and reconciles by stable ids:
- A slot whose inventory item is unchanged keeps its case.
- A slot whose item or code references changed becomes `stale`, keeping its case; **Regenerate** is offered.
- A slot that no longer exists becomes `obsolete`; its case is kept, read-only.
- New items get new `planned` slots.
- Traces of affected flows are marked stale and are re-traced only when someone generates for that flow.

## 6. Slots and priorities

### 6.1 Slot rules (deterministic)

| Inventory item | Slots |
|---|---|
| branch (`if`/`switch`/ternary) | one per outcome |
| validation | one valid + one invalid per rule |
| permission / role check | allowed + denied |
| error / error response | one |
| field, argument, route, screen, job, query (added or removed) | one |
| removed behaviour | one ("old behaviour is gone or handled") |
| per flow | one changed happy path (`pr`) or happy path (`scenario`) |
| per flow, unchanged steps (`pr` only) | regression slots, one per group of contiguous unchanged steps (at most 3 per flow) |
| flow crosses repos or a queue/scheduled job | one cross-side slot |

### 6.2 Scenario types

1. Happy path (changed, in `pr` mode).
2. Branches and edges.
3. Errors and failures.
4. Regression (`pr` only).
5. Cross-side effects.

Every slot has exactly one type.

### 6.3 Priority

The base priority by slot:
- **P0:** removed behaviour, permission, and the changed happy path on a flow whose risk is in the top third.
- **P1:** other changed happy paths and branches.
- **P2:** validation, error and edge cases.
- **P3:** regression and cross-side cases.

A flow whose risk reasons include money or data deletion (heuristic: the changed code touches delete or payment-named functions or tables) lifts its P1 slots to P0.

Counts per priority are shown before generation. The user sets a number per priority and, optionally, a per-flow cap. Slots are taken by priority, then flow risk, then slot id, so a cap always means "the most important N".

### 6.4 Suggested extras

`cases` may return cases with no slot. They are stored with `suggested: true` in a separate tray and count toward no cap. **Accept** turns one into a case with a new slot (`rule: "suggested"`); **Dismiss** removes it.

## 7. Sharing and parallel work (registry extension)

**Shared once per change set, reused by everyone:**
- the ImpactReport and slots;
- flow traces (base and head);
- scenarios;
- cases.

Each QA pays only for the cases they generate, with their own Claude.

**Registry additions:**
- **Overlay kinds:** `impact`, `scenario`, `testcase`, `flowtrace`.
- **Reservations:** `POST /projects/{p}/changesets/{id}/reservations` with `{slot_ids | {priority: n, flow_id?}}`.
  - Slots are reserved atomically in one database transaction, so concurrent requests never get the same slot.
  - The response lists the slots granted.
  - Reservations last 24 h, and the app renews them every 10 minutes while a job runs.
  - A **trace reservation** per flow lasts 30 minutes, renewed the same way.
- **Assignment:** a maintainer (the QA lead) can assign flows or slots to a user, and release anyone's reservation. A contributor may reserve unassigned slots and slots assigned to them. A viewer reads and exports.
- **Publishing a case:**
  - Accepted when the slot is reserved by the publisher, or unreserved and unwritten.
  - If the slot is already written, the registry answers 409. The app then re-publishes the case as a suggested extra on that slot, so generated work is never silently lost.
  - The first published case for a slot wins.

| Situation | Outcome |
|---|---|
| Two QAs, different slots | No conflict; the plan fills from both |
| Two QAs ask for the same slots | Atomic reservation; the second is given the next free slots |
| Both need an untraced flow | The first reserves the trace; others see "<name> is tracing <flow>" and can wait or pick other flows |
| A job outlives its reservation | Renewal while running; expiry only frees abandoned slots |
| Lead reassigns a slot in progress | The old reservation is released. The old holder's late publish is accepted if the slot is still unwritten; otherwise it becomes a suggested extra |
| New PR commit mid-generation | The case is published, then reconciled by §5 (stale or obsolete if its code changed) |
| Offline QA | May generate; cases are marked `unreserved`. On sync, accepted if the slot is unwritten, otherwise they become suggested extras. The app warns before an offline run |

**Without a registry** (local only), everything works for one person; reservations are local.

## 8. Cost and control

The Claude-call estimate per flow is: `(base trace needed ? 1 : 0) + (head trace needed ? 1 : 0) + (scenarios needed ? 1 : 0) + ceil(slots / 15)`. Reused shared traces and scenarios count zero.

The impact screen shows "Will generate X of Y · about N Claude calls". Nothing calls Claude without the user pressing **Reserve & generate**, except the one `name_flows` call in Graph 1.

## 9. Screens (desktop)

There is a new sidebar view, **Test plans**. Clickable mocks will be supplied with this spec for approval.

1. **Test plans list**, with two tabs:
   - **Test Plan for PR** (Feature B): the PR plans with their per-repo changes, progress and people, and the **New PR test plan** form.
   - **Test Scenarios in a Branch** (Feature A): the scenario plans with their analysed branch, progress and people, and the **New scenario test plan** form (scenario text plus an analysed branch set).
   - Both lists show status ("Impact ready", "62 of 115 written") and the people involved.
   - **New PR test plan**: a table with one row per repo: **Base** and **PR branch** side by side, the base pre-filled from `default_branch`; leaving a PR branch blank means that repo is unchanged. A GitHub link per repo fills that row in.
   - **New scenario test plan**: free text, or started from **Make test plan** in Ask or Memory.
2. **Impact.**
   - **Header:** the PR, commits and summary ("14 functions changed in 2 repos, 9 flows affected"), plus the full re-analysis banner when flagged.
   - **Ranked flow list:**
     - each row has a checkbox, the flow name, risk chips and the entry point;
     - planned counts per priority, and "Reuses master flow" or "Needs master trace";
     - expanding a row shows its inventory items in plain English.
   - **Generation panel:** a number box per priority, an optional per-flow cap, the live total and estimate, and **Reserve & generate**.
3. **Plan board.**
   - Progress per priority and per person.
   - Cases grouped by flow, then scenario. Each case expands to preconditions, steps, data and expected result.
   - Slot states inline (planned, reserved by X until …, written, stale with **Regenerate**, failed with **Retry**, obsolete).
   - **Generate more** (the same number boxes).
   - For leads: **Assign** and **Release**.
   - The suggested-extras tray, and a coverage strip ("3 items without a case: 2 skipped by choice, 1 failed").
   - **Export CSV**, which follows the active filters.
4. **Elsewhere.** **Make test plan** on answers in Ask and Memory; a test-plan count per branch in the Library.

**CSV columns:** ID, Flow, Scenario, Type, Priority, Title, Preconditions, Steps, Test data, Expected, Status, Author, Stale. Lists are joined with numbered new lines. The file is UTF-8 with a BOM, so Excel opens it correctly.

The CLI gets matching commands:
- `kb testplan pr`
- `kb testplan scenario`
- `kb testplan generate --p0 30 --p1 0`
- `kb testplan export`
- `kb testplan status`

## 10. Errors

| Situation | Behaviour |
|---|---|
| No git history | "PR analysis needs git repos for: sop-web" — before any work |
| No base pack | "Analyse <base> first" with an **Analyse** button |
| GitHub link without a token, or a private repo | "Add GITHUB_TOKEN to ~/.kb/kb.env, or pick the branches yourself" |
| Claude unavailable | Graph 1 still completes, with code-name flow names; generation is disabled with the existing banner |
| One flow fails | Its slots are marked `failed` with the reason and **Retry**; the others finish |
| Registry unreachable | Local generation with an `unreserved` warning; publishes are queued in the existing outbox |
| Branch being analysed or pulled | The existing 409 "being updated" rule applies |

## 11. Testing and acceptance

- **Fixture:** a small FE and BE repo pair with real git history. `master`, plus a PR branch that:
  - adds a branch, a validation and a permission check;
  - removes one behaviour;
  - changes a GraphQL argument used by a screen.
- **Branch sets:** a different base per repo; an untouched repo; base pack lookup by per-repo branches; worktrees never touch the user's checkout; single-name `--branch` keeps working.
- **Graph 1 golden:** the exact expected inventory items, flows (including the screen reached only through the changed resolver), slots and counts per priority.
- **Graph 2 with a scripted fake:**
  - exactly one case per slot;
  - caps and per-priority numbers are respected;
  - `more` continues by priority;
  - check_cases rejects ungrounded cases;
  - one failing flow doesn't stop the others;
  - scenario mode skips regression.
- **PR update:** stale and obsolete reconciliation by stable id.
- **Registry:**
  - concurrent reservations never overlap (a threaded test);
  - expiry and renewal;
  - trace reservation;
  - assignment and release rights by role;
  - first-publish-wins, with the loser kept as a suggested extra;
  - offline publishes on sync.
- **CSV:** columns, ordering, filters, and that the file opens correctly as UTF-8.
- **E2E (Playwright):** pick branches → impact → generate 5 P0 → a second profile generates 5 P1 on the shared plan → the board shows both → the export matches the board.
- **Acceptance on the Mac:** a real PR on the SOP repos with git history and real Claude, reviewed by the user.

## 12. Out of scope (now)

- Generating automated test code.
- Pushing to test-management tools (TestRail, Xray).
- Word/PDF and Markdown exports.
- Commenting on PRs.
- Running test cases or recording results.

## 13. Delivery

This is too large for one plan, so it is split into two stages, each with its own plan:
- **Stage 5a — local:**
  - Feature A, plus Feature B for one user;
  - both graphs, inventory, slots, priorities and caps, generate-more, stale and obsolete reconciliation;
  - the three screens without sharing, CSV, and the CLI.
- **Stage 5b — shared:**
  - the registry kinds;
  - reservations, renewals and trace reservations;
  - lead assignment;
  - publish conflicts and extras, offline sync;
  - the board's people and progress view;
  - the two-user E2E.

The polish round agreed on 2026-10-29 (deferred minors and a Settings screen) runs before stage 5a.
