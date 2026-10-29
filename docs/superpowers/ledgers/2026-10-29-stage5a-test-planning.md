# Stage 5a (test planning, local) — execution ledger

Final review: fresh reviewer (opus) over b5595a0..f84c126 (polish round + stage 5a): 1 Critical, 8 Important fixed in one pass; minors deferred.

```
# SDD ledger — plan: docs/superpowers/plans/2026-10-29-stage5a-test-planning.md
Pre-flight: T1 run_analyze(label, branches) ← consumed by T9 (base pack lookup via find_pack_by_branches) and T19 E2E: consistent.
Pre-flight: T2 worktree_at → T3 repo_diff(source=git_source) and T9: consistent (diff runs in git_source, not worktree).
Pre-flight: T4 GraphDelta.hunk_owner/nodes_* → T5 inventory, T6 reach(changed, removed): consistent.
Pre-flight: T5 InventoryItem ids → T7 slot ids → T9 reconciliation by id: consistent.
Pre-flight: T7 plan_slots regression uses ImpactFlow.path (T6 produces path): consistent.
Pre-flight: T8 PlanStore under base pack overlay ← spec §4 corrected to "base analysis": consistent.
Pre-flight: T11 trace_flow uses T10 trace_context hook: consistent.
Pre-flight: T12 run_design(label, plan_id) ← T15 routes, T14 CLI: consistent. T17 estimateCalls TS mirrors T7 estimate_calls.
Task 1: Ruling: resolve_branch_set takes label as a keyword (resolve_branch_set(cfg, args, label=None)) and fills repos left out of a per-repo set from default_branch, else raises "no branch for X"; identical per-repo branches need no label — cost if wrong: none.
Task 1: Ruling: available_checkouts/run_ask/citation/pull now read each repo's branch from Manifest.branches — without this, asking a "production" pack would try to check out a branch named "production" — cost if wrong: none.
Task 1: complete (commits 83d0e37..5eab338, tests: bash -c 'uv run pytest -q 2>&1 | tail -1 && cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests'' →       Tests  64 passed (64))
Task 2: Ruling: worktrees for local repos are made from KB's own --no-checkout clone under $KB_HOME/work/mirrors, not from the user's repo — `git worktree add` on their repo would write bookkeeping into their .git — cost if wrong: one local clone per repo (hard-linked).
Task 2: Ruling: stage 5a test files are named test_tp_*.py — pytest's prepend import mode needs unique basenames across tests/ — cost if wrong: none.
Task 2: complete (commits 5eab338..50d498d, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 4 passed in 2.12s)
Task 3: Ruling: repo_diff gains subpath= for a repo's path: setting (the plan said 'honours repo.path'; it needs the value passed in) — cost if wrong: none.
Task 3: complete (commits 50d498d..81430ed, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 7 passed in 2.92s)
Task 4: Ruling: Graphify gives definition lines but no extents, so graph_delta takes the base and head checkouts and computes top-level symbol spans with tree-sitter (symbol_spans); signature is graph_delta(base, head, hunks, base_checkouts, head_checkouts) — cost if wrong: nested functions map to their top-level owner.
Task 4: Ruling: the fixture graph has no call edges (Graphify --code-only emits contains/imports), so the delta test checks the removed `contains` edge for notifyAssignee instead of a call edge — cost if wrong: none.
Task 4: complete (commits 81430ed..4e29297, tests: bash -c 'uv run pytest -q 2>&1 | tail -1' → 347 passed in 47.76s)
Task 5: Ruling: inventory takes checkouts, inventory(base_checkouts, head_checkouts, delta, hunks), and reads the changed functions itself — cost if wrong: none.
Task 5: Ruling: route/screen/job/argument kinds from graph-delta node changes are deferred to Task 6's reach (they need node kinds and links); inventory covers branch/validation/permission/error/field/query/removed/code from source — cost if wrong: an added route with no decision points gets only its flow's happy-path slot.
Task 5: complete (commits 4e29297..a81c807, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 14 passed in 9.86s)
Task 6: Ruling: reach(head, base_graph, base_pack, delta, inventory) walks contains/imports/calls/routes_to/sends_graphql/handled_by/consumed_by upwards, and treats a server file as exposing its resolvers (the graphql linker attaches resolvers to files, not functions); a changed node with no entry above it becomes its own entry — cost if wrong: a few extra flows.
Task 6: Ruling: structural_items (route/job/argument/screen from added or removed graph nodes) lives in reach.py, replacing the Task 5 deferral; delta.nodes_added/removed now exclude only file nodes so routes and queue topics are seen — cost if wrong: none.
Task 6: complete (commits a81c807..23689ea, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 17 passed in 10.75s)
Task 7: Ruling: slot scenario types — permission allowed→branches_edges / denied→errors_failures; validation valid→branches_edges / invalid→errors_failures; removed→branches_edges ("old behaviour is gone or handled"), so the regression type only ever holds unchanged-path slots — cost if wrong: grouping on the board.
Task 7: Ruling: assign_priorities(slots, flows, inventory=None) and estimate_calls(..., mode) — the money heuristic reads flow.changed_functions; have_trace keys are "<flow>:base"/"<flow>:head" — cost if wrong: none.
Task 7: complete (commits 23689ea..d91d0a3, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 24 passed in 11.10s)
Task 8: Ruling: PlanStore also has find(project, plan_id) -> label, update_slots (locked read-modify-write) and delete_case; save_change_set(label, cs) takes the project from cs — cost if wrong: none.
Task 8: complete (commits d91d0a3..848882e, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 27 passed in 11.52s)
Task 9: Ruling: the base graph is rebuilt from base worktrees (cached by commit, no LLM) instead of taken from the base pack — the pack may be older than the base branch, which would attribute master's drift to the PR; the pack still supplies modules and saved flows — cost if wrong: one extra Graphify run per base commit.
Task 9: Ruling: plan ids are derived from project + per-repo base/head branch names ("pr-<hash>"), so re-running after new PR commits lands on the same plan and reconciles — cost if wrong: renaming a branch starts a new plan.
Task 9: Ruling: staleness uses a per-function source hash stored in the impact report (graph_delta.function_hashes); a written slot goes stale when a function it depends on changed between runs — cost if wrong: none.
Task 9: Ruling: Feature B requires git for every repo of the project (unchanged repos still need a worktree at their base for the PR graph) — cost if wrong: a plain-folder repo blocks PR plans even if untouched.
Task 9: Ruling: the "no base pack" message names the branch set ("Analyse mini-fe master · mini-be develop first") since no label exists yet — cost if wrong: wording.
Task 9: complete (commits 848882e..d6b17cc, tests: bash -c 'uv run pytest -q 2>&1 | tail -1' → 373 passed in 73.21s (0:01:13))
Task 10: complete (commits d6b17cc..d93002f, tests: bash -c 'uv run pytest -q tests/test_agent_nodes.py tests/test_agent_graph.py 2>&1 | tail -1' → 26 passed in 2.24s)
Task 11: Ruling: design nodes take a DesignWork bundle (flow, inventory, checkouts, provider, base/head flows, diff) rather than loose args; check_cases(cases, slots) needs no flows — cost if wrong: none.
Task 11: Ruling: compare aligns steps on overlapping citations only when the descriptions are also similar (ratio ≥ 0.4), then by text (≥ 0.6) — overlap alone paired a new admin check with "Saves the task" — cost if wrong: some changed steps show as added.
Task 11: Ruling: one scenario per (flow, type); traces made for test planning are not saved into the base pack's flows (PR-branch citations would pollute Ask's reuse) — they're kept on the plan in Task 12 — cost if wrong: none.
Task 11: complete (commits d93002f..7221b2e, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 44 passed in 28.92s)
Task 12: Ruling: each Send branch runs the per-flow steps in one node (baseline then head trace, compare, scenarios, cases, check) instead of a compiled subgraph with a parallel baseline∥head — flows already run in parallel, and saving per flow inside the node keeps "results saved as each flow finishes" simple; save_plan is folded into the flow and merge nodes — cost if wrong: base and head traces of one flow run one after the other.
Task 12: Ruling: flow traces made for a plan are cached on the plan (traces/<flow>-<side>-<commits>.json) so "generate more" never re-traces the same commits — cost if wrong: none.
Task 12: complete (commits 7221b2e..7ae8604, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 50 passed in 47.13s)
Task 13: Ruling: a typed scenario is answered through run_ask (so it reuses a saved flow when one fits, and the answer lands in Memory like any question; the learn step's suggestion call is included) — cost if wrong: one extra Claude call for suggestions per new scenario.
Task 13: Ruling: scenario plans store their flow as an ImpactReport with one flow and the answer's trace as the plan's cached head trace, so run_design handles both kinds the same way — cost if wrong: none.
Task 13: Ruling: pytest pythonpath gains tests/serve and tests/testplan so test helpers import across folders — cost if wrong: none.
Task 13: complete (commits 7ae8604..34b0fbc, tests: bash -c 'uv run pytest -q tests/testplan 2>&1 | tail -1' → 52 passed in 46.14s)
Task 14: Ruling: export_csv(plan_store, project, label, plan_id, priorities, flow_ids, statuses); `kb testplan pr` takes --pr repo=URL (a link belongs to one repo); `generate` uses "more" mode so failed slots are retried too; scenario plans take --from-answer — cost if wrong: CLI spelling.
Task 14: complete (commits 34b0fbc..8ffb125, tests: bash -c 'uv run pytest -q tests/testplan tests/test_cli.py 2>&1 | tail -1' → 56 passed in 50.47s)
Task 15: Ruling: POST testplans/pr returns {job_id} only; the plan id arrives in the job result (PR links can change the branches, so the id isn't known before the job runs) — cost if wrong: the UI waits for the job to open the plan.
Task 15: Ruling: retrying a slot regenerates exactly that slot (run_design gains only=); a stale slot's old case is kept as obsolete — cost if wrong: none.
Task 15: complete (commits 8ffb125..940b17e, tests: bash -c 'uv run pytest -q tests/serve tests/testplan 2>&1 | tail -1' → 113 passed in 75.01s (0:01:15))
Task 16: Ruling: app state gains planId / planScreen ("list" | "impact" | "board") / planJobId under the "testplans" view; the impact/plan job keeps polling across navigation like Ask — cost if wrong: none.
Task 16: Ruling: the "Analyse" button for a missing base uses the shared branch name as the label, or "production" for a per-repo set, then sends the user to the Library to watch it — cost if wrong: the user renames later.
Task 16: Ruling: CSV export goes through a new kb:save-file IPC (save dialog in main) because the page cannot download files — cost if wrong: none.
Task 16: complete (commits 940b17e..dfb3e6a, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit && npx tsc -p tsconfig.electron.json --noEmit' →       Tests  70 passed (70))
Task 17: Ruling: unticked flows really are excluded — generate gains flow_ids (route, run_design, plan_work) with test_generate_only_ticked_flows; the plan's generate signature without flows would have ignored the checkboxes — cost if wrong: none.
Task 17: Ruling: the P0 box starts at all planned P0 slots and the others at 0, matching the mock ("30 P0 now") — cost if wrong: a default.
Task 17: Ruling: the UI's estimate counts traces as needed unless the flow reuses a master flow (the plan's saved traces aren't exposed to the page) — cost if wrong: the estimate is high by one call per flow on a second run.
Task 17: complete (commits dfb3e6a..03c18b4, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  76 passed (76))
Task 18: Ruling: "Generate more" opens the same generation panel inline on the board for both kinds of plan (rather than jumping back to the Impact screen) — one place to keep going — cost if wrong: none.
Task 18: Ruling: planned slots are summarised per scenario group ("4 more planned (P0 3, P1 1)") instead of one row each, so a 100-slot plan stays readable; reserved/failed/stale slots get their own rows — cost if wrong: layout.
Task 18: complete (commits 03c18b4..1022a10, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  84 passed (84))
Task 19: Ruling: E2E CSV export uses a test-only KB_E2E_SAVE_DIR in main (no native save dialog to click), alongside the existing KB_SERVE_PROVIDER hook — cost if wrong: none (unset in real use).
Task 19: E2E 7/7 (pr_test_plan_journey, scenario_test_plan_journey added); pytest 402; vitest 84.
Task 19: complete (commits 1022a10..f84c126, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit && npx tsc -p tsconfig.electron.json --noEmit' →       Tests  84 passed (84))
Final: fixed renamed/moved files reported as all-removed — test_pure_move_is_not_removal, test_move_with_edit_reports_only_the_edit RED→GREEN
Final: fixed URL repos' base frozen at the first clone — test_url_repo_base_follows_origin RED→GREEN (managed clones resolve origin/<ref> first; impact runs fetch fresh)
Final: fixed new commits on a remote-only PR branch never reaching the mirror — test_remote_only_pr_branch_new_commit_reaches_mirror RED→GREEN (mirror fetches heads and remote-tracking refs when the commit is missing)
Final: fixed a network fetch per flow per job — test_git_source_fetches_once_per_minute RED→GREEN (one fetch per clone per minute, serialised; impact runs force one)
Final: fixed slots stuck as reserved after a failure or crash — test_failure_after_reserving_releases_slots, test_leftover_reserved_slots_are_reclaimed RED→GREEN
Final: fixed reconcile overwriting a case written concurrently — test_reconcile_does_not_lose_a_concurrent_write RED→GREEN (merge happens inside update_slots; reserved slots now stay reserved for the running design job, which reclaims leftovers itself)
Final: fixed CSV formula injection — test_csv_neutralises_formulas RED→GREEN (cells starting with = + - @ tab CR get a leading ')
Final: fixed newline injection into kb.env from Settings — test_settings_rejects_newlines RED→GREEN (400 "must be one line")
Final: fixed reach rescanning every edge per path node — test_reach_scans_edges_a_bounded_number_of_times RED→GREEN
Final: Ruling: impact and design jobs keep separate job keys — with reconcile now inside the slots lock their writes can't overwrite each other, and blocking generation while re-reading a PR isn't needed — cost if wrong: none seen.
Final: minor (deferred): a changed condition (isAdmin → isAdmin || isOwner) becomes obsolete + new slot instead of stale (inventory ids hash the condition text).
Final: minor (deferred): reconcile.touched ignores functions that had no hash in the previous run, so a newly changed function doesn't stale its flow's happy-path case.
Final: minor (deferred): classifier misses JSX permission gates ({isAdmin && <X/>}), try/catch handlers, Prisma/repository queries; nested methods map to their top-level class.
Final: minor (deferred): one failing cases batch discards the flow's earlier batches.
Final: minor (deferred): Retry changes the slot/case before knowing the job can start (a 409 leaves it reopened with no job).
Final: minor (deferred): a PR link's base branch is ignored when a default base is pre-filled; fork PRs need refs/pull/N/head.
Final: minor (deferred): head_graphs cache keyed only by commits (ignores kb.yaml ignore/linker edits); worktrees, head_build and caches are never cleaned up.
Final: minor (deferred): opening a citation uses the OS default app (shell.openPath) even for script-like files; prefer an editor or reveal in folder.
Final: minor (deferred): GET testplan detail calls list_plans (loads every plan) just for one summary.
Final: minor (deferred): re-planning a scenario drops old slots without marking them obsolete.
Final: suite after fix pass — pytest 413 passed in 130.25s (0:02:10), vitest 84/84, E2E 7/7
```
