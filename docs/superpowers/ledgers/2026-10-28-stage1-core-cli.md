# SDD ledger — plan: docs/superpowers/plans/2026-10-28-stage1-core-cli.md
Spec: docs/superpowers/specs/2026-10-28-codebase-knowledge-system-design.md (read)
Branch: stage1-core-cli from master 16ddc52
Pre-flight:
- T9 consumes T3 CodeGraph/CodeEdge: consistent.
- T11 ask_json consumes T10 LLMProvider/LLMRequest/LLMResult: consistent (ask_json moved to T11 during plan self-review).
- T12 consumes T9 Module, T10 provider, T11 ask_json/Tracer: consistent.
- T13 consumes T4 checkout_repo, T5 run_graphify/parse_graphify_json, T6-8 get_linker, T9 PackStore/build_index, T12 cluster_graph/map_modules/write_system_overview: consistent.
- T14/T15 consume T9 Pack/search/hash_span/Flow/QAItem/Suggestion, T11 ask_json: consistent. Note: FTS5 bm25 is negative-lower-better; T15 "0.8x best score" uses score = -bm25 (T9 search returns positive higher-better score).
- T16 consumes T4 checkout re-resolution, T14 build_agent: consistent.
Task 1: complete (commits 16ddc52..a58a003, tests: uv run pytest -q → 1 passed in 0.02s)
Task 2: complete (commits a58a003..f36fa86, tests: uv run pytest -q → 9 passed in 0.06s)
Task 3: complete (commits f36fa86..7954be6, tests: uv run pytest -q → 14 passed in 0.18s)
Task 4: complete (commits 7954be6..9d86892, tests: uv run pytest -q → 20 passed in 0.65s)
Task 5: Ruling: graphify flags — use `extract . --code-only --no-cluster --out <dir>` (cwd=repo root), not `--no-viz` (not an extract flag) — `--out` keeps graphify-out/ out of the user's repo (spec: read-only over repos); --no-cluster because the mapper clusters itself — cost if wrong: one flag change.
Task 5: Ruling: parse_graphify_json gains `ignore: list[str] = ()` — plan requires ignore globs to be excluded from the parsed graph but the signature lacked it — cost if wrong: none (optional param).
Task 5: Ruling: fixtures mini-fe/mini-be created in Task 5 (plan lists them under Task 7) because Task 5 needs a sample to capture — cost if wrong: none.
Task 5: complete (commits 9d86892..0562fba, tests: uv run pytest -q → 27 passed in 0.58s)
Task 6: Ruling: .ts files parse with the plain TypeScript grammar, .tsx/.js/.jsx with TSX (plan said TSX for all) — TSX misparses `<T>x` casts in .ts — cost if wrong: none, helpers are grammar-agnostic.
Task 6: Ruling: get_linker/unknown-type test deferred to Task 8, when all three built-in linker modules exist — cost if wrong: none.
Task 6: Ruling: LinkContext gains `ignore: tuple[str, ...] = ()` so linkers honour kb.yaml ignore globs — cost if wrong: none.
Task 6: complete (commits 0562fba..da647ad, tests: uv run pytest -q → 34 passed in 0.80s)
Task 7: Ruling: linkers add their own nodes (graphql_op, resolver, and file nodes if missing) to ctx.graph and return edges — plan's Linker.run returns only edges but edges need endpoints — cost if wrong: none.
Task 7: Ruling: op ids "<repo>:<file>#op:<name>", resolver ids "<repo>:<file>#resolver:<field>@<line>"; `${...}` interpolations stripped before parsing gql — cost if wrong: id format change only.
Task 7: complete (commits da647ad..f58753a, tests: uv run pytest -q → 39 passed in 0.88s)
Task 8: Ruling: queue_topic node id '<repo>:queue_topic:<KEY>', route node id '<repo>:route:<path>'; dynamic consumer edge is consumer-file -> unresolved:queue:<ident> — cost if wrong: id format only.
Task 8: complete (commits f58753a..eece4c6, tests: uv run pytest -q → 46 passed in 0.83s)
Task 9: Ruling: write_base gains `link_nodes: list[CodeNode] = ()`; links.json = {"nodes": [...linker-added nodes], "edges": [...]} — linker edges point at nodes (ops, resolvers, topics, routes) that no repo graph holds — cost if wrong: file format tweak.
Task 9: Ruling: added PackStore.write_modules / exists and search(kinds=...) filter; "is_stale" returns False when the repo has no checkout (unknown ≠ stale) — cost if wrong: stale items without source shown as fresh.
Task 9: complete (commits eece4c6..d78cf95, tests: uv run pytest -q → 55 passed in 1.15s)
Task 10: Ruling: ClaudeCodeProvider.check() runs only `claude --version` (no paid 1-turn query); sign-in failures surface as LLMUnavailable("not signed in …") from the first run() — callers must let LLMUnavailable propagate, not treat it as a per-item failure — cost if wrong: analyze reaches step 4 before discovering a missing login (steps 1–3 already saved, so nothing lost).
Task 10: Ruling: read-only enforced with both `tools` (available) and `allowed_tools` (auto-approved) = Read/Grep/Glob, or [] — SDK distinguishes the two — cost if wrong: none. Real smoke test against the container's claude CLI passed (Read step captured).
Task 10: complete (commits d78cf95..8d648f6, tests: uv run pytest -q → 60 passed in 1.58s)
Task 11: Ruling: langfuse resolved to v5 (plan said v3): trace tags via `propagate_attributes(trace_name, tags, metadata)`, spans via `start_as_current_observation`, Claude call = `generation` observation with child `tool` observations — v5 API — cost if wrong: tracing adapter only. Tool-step timing goes in metadata (v5 start_observation takes no start time). Smoke-tested against an unreachable host: no crash.
Task 11: Ruling: Span.child takes `as_type` + extra attrs (output/model/usage/cost) so record_llm can create generations — cost if wrong: none.
Task 11: complete (commits 8d648f6..d840769, tests: uv run pytest -q → 69 passed in 1.84s)
Task 12: Ruling: cluster group graph ignores inside-folder edges (self-loops) — with them Louvain kept a frontend folder and the backend folder it calls apart, defeating the plan's stated intent — cost if wrong: cluster granularity; re-tune on SOP in Task 17.
Task 12: Ruling: write_system_overview gains `cwd: Path | None` (LLMRequest needs a cwd); appends a generated Mermaid flowchart when Claude's reply lacks one; entry points fall back to the top-ranked candidate when none valid — cost if wrong: none.
Task 12: complete (commits d840769..8736871, tests: uv run pytest -q → 78 passed in 1.98s)
Task 13: Ruling: tests run real graphify on the mini fixtures (plan said monkeypatched) — more honest, ~1s each; skipped if graphify missing — cost if wrong: slower suite.
Task 13: Ruling: analyze also re-checks overlay Q&A citations after writing the new base and sets `stale` (spec 3.3: carry overlay forward, mark changed items stale); module checkpoints keyed by a hash of the commit set so --resume never reuses names from other commits; `unparsed` = JS/TS source files graphify produced no file node for — cost if wrong: stats/staleness timing only.
Task 13: complete (commits 8736871..4d3aacf, tests: uv run pytest -q → 85 passed in 7.07s)
Task 14: Ruling: AgentState gains no_source; AgentDeps gains context (glossary for prompts) and extra — needed by Task 15/16 — cost if wrong: none.
Task 14: complete (commits 4d3aacf..7193fc0, tests: uv run pytest -q → 88 passed in 7.97s)
Task 15: Ruling: reuse additionally requires question-term overlap (Jaccard ≥ 0.6) and is skipped for follow-ups (seed) — FTS score alone would reuse a different question in the same module (e.g. "who is notified" vs "how scheduling works") — cost if wrong: an extra Claude call instead of a reuse.
Task 15: Ruling: learn makes ONE ask_json (suggestions + module notes) instead of one per module — fewer Claude calls; failure there still saves the Q&A — cost if wrong: none.
Task 15: Ruling: collect also hops symbol -> its file node (linker-made nodes have no contains edge) and seeds from search hits within the modules; verify tolerates absolute paths and a wrong repo name when the file exists in another checkout; trace test fixed to put the queue topic in its shortlist (gaps are reported for shortlisted nodes only) — cost if wrong: shortlist breadth.
Task 15: complete (commits 7193fc0..797dbf7, tests: uv run pytest -q → 101 passed in 8.28s)
Task 16: Ruling: a local_path counts as a git repo only if it is the work-tree top (`--show-toplevel` == path); a copy inside another repo is a plain folder — the old check made subfolders of any repo fail on branch mismatch (found via fixtures inside kb-system) — test_folder_inside_another_git_repo_is_plain_folder RED→GREEN — cost if wrong: a user pointing at a monorepo subfolder loses branch checking (they should use `path:`).
Task 16: Ruling: run_ask never pre-checks Claude: not-found and reused answers work without a Claude login (browse-only users); repos without a checkout are skipped with a warning; warns when current code differs from the analysed commits — cost if wrong: none.
Task 16: complete (commits 797dbf7..1b87f7a, tests: uv run pytest -q → 111 passed in 8.60s)
Task 17: Ruling: acceptance runs in this cloud workspace on a source-only copy of the SOP repos (tar written to sop-backend-main/_to_delete/kb-transfer.tgz on the user's Mac, then staged) instead of on the Mac — the Mac-side shell is a Linux VM without Docker, and the workspace already has a signed-in claude CLI and graphify — cost if wrong: none for analyze/ask; user should delete the _to_delete folder.
Task 17: Ruling: Langfuse server could NOT be verified: Docker Hub images (clickhouse, postgres, redis) are blocked by this workspace's network allowlist (403); Langfuse client code was smoke-tested against an unreachable host only. Step 5 of Task 17 deferred to the user's Mac (docs/langfuse-local.md) — cost if wrong: tracing bugs surface on first real run.
Task 17: Ruling: clustering retuned on SOP structure: Louvain resolution 4 (param, default RESOLUTION=4.0), max_clusters 60 — at resolution 1 Tasks merged into Launcher and Training/Forms/Nexus formed a 6.9k-node blob; at 4 Tasks is its own cluster (web tasks + TasksDashboard + backend module/tasks). Toy-graph merge test pinned to resolution=1.0 — cost if wrong: module granularity.
Task 17: Ruling: golden scoring lives in src/kb/golden.py (testable), scripts/golden.py is a thin CLI; per-entry min_recall (default 0.5; scheduling 1.0) added beside min_verified_ratio (default 0.6) — plan named only verified ratio thresholds — cost if wrong: threshold tweaks.
Final review: fresh reviewer (opus) — "Ready to merge: with fixes". 2 Critical, 8 Important, 14 Minor.
Final: re-graded Minor -> Important: Claude Code loading the user's repo settings (setting_sources) — hooks/CLAUDE.md from the analysed repo (SOP has .claude/) would run during kb calls; dotfiles in the local content hash — Finder's .DS_Store changes the commit id on every Mac.
Final: fixed C1 bull-queue copies inline-handler source into links.json — test_inline_handler_and_concurrency_never_copy_source RED→GREEN
Final: fixed C2 graphify rationale (comment/docstring) nodes stored as names — test_sanitized_graph_contains_no_source_text (+comment markers) RED→GREEN
Final: fixed I3 verify accepted ../ escapes; lstrip broke dotfiles — test_verify_rejects_paths_escaping_the_checkout_and_keeps_dotfiles RED→GREEN
Final: fixed I4 every Claude error reported as "not signed in": now unavailable / transient (retry 2s, 8s) / per-call LLMCallError (module marked failed) — test_max_turns_is_a_per_call_failure_not_unavailable, test_transient_error_retried_with_backoff, test_persistent_transient_error_becomes_call_error, test_per_call_failure_marks_module_failed RED→GREEN
Final: fixed I5 kb ask KeyError when a context_docs repo has no checkout — test_context_doc_repo_without_checkout_does_not_crash RED→GREEN
Final: fixed I6 kb ask read url repos at branch tip; now checkout_at(pack commit), offline-safe — test_checkout_at_pins_commit_without_network RED→GREEN
Final: fixed I7 learn failure hid the saved answer and skipped indexing — test_learn_failure_still_returns_answer_and_indexes RED→GREEN
Final: fixed I8 golden reruns reused cached flows and wrote to the user's overlay; dataset expected_output now the expected files — test_run_golden_is_fresh_and_isolated RED→GREEN
Final: fixed I9 incomplete pack answered "couldn't find code": index built after write_base, not-found mentions --resume, collect works without modules — test_analyze_without_llm_saves_structure_and_marks_incomplete (+index), test_incomplete_pack_not_found_mentions_resume, test_collect_without_modules_uses_node_hits RED→GREEN
Final: fixed I10 Claude prose could carry code into the overlay: prompt rule + strip_code on steps/answer/suggestions/notes — test_code_is_stripped_from_saved_prose, test_trace_prompt_forbids_quoting_code RED→GREEN
Final: fixed (found by I10's test) ask_json cut replies at ``` inside JSON strings — test_ask_json_survives_fences_inside_strings RED→GREEN
Final: fixed (re-graded) setting_sources=[] — test_settings_from_user_repo_not_loaded RED→GREEN; dotfiles skipped in content hash — test_dotfiles_do_not_change_local_commit RED→GREEN
Final: fixed (minor folded into I4 test) corrupt checkpoint redone on --resume — test_corrupt_checkpoint_is_redone RED→GREEN
Final: suite 132/132 green.
Final: minor (deferred): bull producers with non-literal topics dropped silently; topics matched across different queues by key alone
Final: minor (deferred): sends_graphql uses bare-word variable matching over all frontend files (can cross-link same-named vars, comments)
Final: minor (deferred): SDL discovery ignores `ignore` globs
Final: minor (deferred): linker config missing from/to/in passes validation then KeyError at link time; unknown linker type found only after steps 1–2
Final: minor (deferred): git not installed -> FileNotFoundError traceback (_author, checkout)
Final: minor (deferred): failed analyze never flushes its Langfuse trace
Final: minor (deferred): re-analysing while logged out empties modules.json/system.md until --resume
Final: minor (deferred): agent ignores question kind in trace; seeded follow-up passes Q&A but not its citations/diagram; verify checks source not graph
Final: minor (deferred): reused flows show ✓ without re-check when checkouts are missing
Final: minor (deferred): golden runs on url repos re-clone into the scratch home
Final: Ruling: _to_delete/kb-transfer.tgz written into the user's sop-backend-main folder for the acceptance run — reviewer flagged it against read-only; it is outside any analysis of the user's own copy only if deleted — user must delete it; it also changes that folder's local commit id — cost if wrong: one-time drift warning.
Task 17: Ruling: golden run 1 = 8/10; every miss was the frontend .graphql.ts op file (Claude cited components/transforms instead). Trace prompt now asks to cite the graphql_op definition for the request step (test_trace_prompt_asks_to_cite_graphql_operation_for_requests RED→GREEN); chapter golden entry gains sop-backend:src/module/sop/resolvers.ts (Claude correctly found addSop there; my grep had missed it) — cost if wrong: prompt length.
Task 17: SOP acceptance — analyze complete (60 modules, 1898 links resolved / 207 unresolved, 6706 files); kb ask 'How does scheduling work in Tasks?' 12/12 steps verified, cites TaskCreation flow, dataTransform, resolvers.ts:191-289, queue/task/index.ts, util.ts, main.ts:240-425 (found the TASK_REPEAT handling the linker could not); golden 10/10 PASS, verified 1.00 on all. Langfuse server check (step 5) deferred to the user's Mac.
Task 17: complete (commits 1b87f7a..37fff21, tests: uv run pytest -q → 133 passed in 8.99s)
