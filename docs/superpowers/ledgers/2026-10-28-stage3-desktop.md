# Stage 3 (desktop) — execution ledger

Final review: fresh reviewer (opus) over f6d09ca..9c33069; no Critical, 6 Important fixed in one pass, 8 minors deferred.

```
# SDD ledger — plan: docs/superpowers/plans/2026-10-28-stage3-desktop.md
Spec read. Branch phase1 from f6d09ca. Commit only, never push.
Pre-flight:
- T1 /api/login/sso needs T2 jobs: plan says 501 until T2 — implement SSO route in T2 once jobs exist.
- T3 consumes T2 JobRunner + stage-2 client_for/pull/push + stage-1 run_analyze (adds progress param): consistent.
- T4 memory_view consumes stage-2 Outbox: consistent.
- T5 consumes stage-1 available_checkouts: consistent.
- T8 client mirrors T1–T5 routes; T10 needs `sso` flag in /registry response (added by plan edit to T10; implement in T1's route).
- T9 E2E needs T1 serve + T7 renderer build: consistent.
- T15 needs registry from stage 2 + all views: consistent.
Pre-flight Ruling: the registry status route returns "sso" from the start (T1) and /api/login/sso is implemented in T2 when jobs exist — cost if wrong: none.
Task 1: Ruling: Host check validates the hostname (127.0.0.1 / localhost) but not the port — the port is what DNS rebinding cannot fake anyway and the app may sit behind any port — cost if wrong: none.
Task 1: Ruling: test helpers live in tests/serve/serve_helpers.py (a `conftest` import would collide with tests/registry/conftest on the shared pythonpath); Workspace refuses two configs with the same project name; ServeSettings gains store()/client()/provider()/claude_status() helpers — cost if wrong: none.
Task 1: complete (commits f6d09ca..a1ad9c9, tests: uv run pytest -q → 274 passed in 40.70s)
Task 2: Ruling: /api/login/sso implemented here (needs jobs); HTTPException details that are dicts are returned as the body (409 carries job_id) — cost if wrong: none.
Task 2: complete (commits a1ad9c9..2f1bb63, tests: uv run pytest -q → 281 passed in 37.42s)
Task 3: Ruling: request models live at module level (FastAPI cannot resolve locally defined classes under postponed annotations — bodies became query params); branch rows response is {"rows", "registry_error"} as planned plus a per-row "job_id" for in-progress analyses; map_modules gains on_module(i, n) for progress; ask requires an existing pack up front (400 instead of a failed job) — cost if wrong: none.
Task 3: complete (commits 2f1bb63..0790b95, tests: uv run pytest -q → 288 passed in 38.27s)
Task 4: Ruling: publish/sync without a registry in kb.yaml → 400 'add registry:'; memory 'private' count includes queued items (they are still yours until accepted); feedback uses Langfuse env config (LANGFUSE_HOST etc.) since it is not tied to a project — cost if wrong: none.
Task 4: complete (commits 0790b95..184779b, tests: uv run pytest -q → 296 passed in 38.07s)
Task 5: complete (commits 184779b..8b55cf6, tests: uv run pytest -q → 302 passed in 39.06s)
Task 5: Ruling: the escape tests passed vacuously before the route existed (404 from routing); they exercise the real checks now — added the positive test first as the real RED — cost if wrong: none.
Task 6: Ruling: graphifyy is now a Python dependency; graphify_command() adds a `python -m graphify` fallback, so stage-1 test_missing_graphify_binary now also hides the module (intended change). Built binary: dist/kb-serve 497 MB one-folder, ready line in 0.75 s, `--run-graphify extract` works on the fixture — size is mostly graphify's grammars + langchain stack — cost if wrong: a large download; trimming deferred.
Task 6: Ruling: `uv add graphifyy` resolved 0.8.38 (fails on any doc file even with --code-only) because the project pinned tree-sitter>=0.26 while graphifyy 0.9.97 needs <0.26 — relaxed to tree-sitter>=0.25 (linkers pass on 0.25.2) and pinned graphifyy>=0.9.97 — cost if wrong: none observed; full suite green.
Task 6: Ruling: the frozen entry also behaves like `python -m MOD` / `python -c CODE` (graphify 0.9.97 re-launches sys.executable for workers) and calls multiprocessing.freeze_support() — found by running the packaged binary — cost if wrong: none.
Task 6: complete (commits 8b55cf6..1ec770a, tests: uv run pytest -q → 307 passed in 40.54s)
Task 7: complete (commits 1ec770a..b042f67, tests: npm --prefix desktop test → npm notice)
Task 8: Ruling: useJob(api, jobId, intervalMs) takes the client explicitly (plan: useJob(jobId, intervalMs) with an implicit client) — keeps hooks testable without a global — cost if wrong: none.
Task 8: complete (commits b042f67..d9e136f, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E 'Tests'' →       Tests  6 passed (6))
Task 9: Ruling: the renderer is served BY kb serve (--ui-dir, same origin) and Electron's main process injects X-KB-Token on every request to 127.0.0.1:<port> via session.webRequest.onBeforeSendHeaders; the preload therefore exposes NO token and baseUrl "" (plan said preload exposes baseUrl+token) — a file:// page fetching http://127.0.0.1 with a custom header needs CORS preflight, which the Global Constraints forbid; this also keeps the secret out of page JavaScript — cost if wrong: none; the engine-error screen loads from the bundled files instead.
Task 9: Ruling: preload also exposes engineStatus/retryEngine/openLogs (the error screen needs them); E2E launches Electron with --no-sandbox because the workspace runs as root (renderer webPreferences.sandbox stays on); E2E fake Claude lives in tests/e2e_support (KB_SERVE_PROVIDER hook). The quit test first failed on its own pgrep (shell matched the pattern, then `--` parsing) — fixed in the test; engine processes are gone 0.5 s after quit — cost if wrong: none.
Task 9: complete (commits d9e136f..57e351f, tests: bash -c 'cd desktop && xvfb-run -a npx playwright test 2>&1 | tail -2 && cd .. && uv run pytest -q tests/serve 2>&1 | tail -1' → 35 passed in 6.66s)
Task 10: Ruling: ProjectSwitcher is a click-open menu (projects + 'Add a project…') — the mock showed only the card; StatusFooter fetches /registry per project for the green dot — cost if wrong: none.
Task 10: complete (commits 57e351f..0234ea3, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  10 passed (10))
Task 11: complete (commits 0234ea3..6d0cfe5, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  15 passed (15))
Task 11: Ruling: in-progress rows show the live progress bar and step inline instead of a "View progress" button — the row already is the progress view — cost if wrong: one button.
Task 12: Ruling: the Mermaid failure test uses a plain flag rather than a rejecting vi.fn — vitest's spy tracking reports a rejected promise as unhandled even when the component catches it — cost if wrong: none (test-only).
Task 12: complete (commits 6d0cfe5..25132dd, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  21 passed (21))
Task 13: Ruling: /api/status now also returns langfuse.host (from LANGFUSE_HOST/LANGFUSE_BASE_URL) — the "Open trace in Langfuse" link needs a host and the plan's status payload had none — cost if wrong: one extra field.
Task 13: Ruling: the answer card has no commit line — the ask payload carries no commits and the Library already shows them per branch — cost if wrong: add commits to ask_payload later.
Task 13: Ruling: a question prefilled from the Map ("Ask about X") is placed in the input, not run automatically — keeps Claude usage under the user's control, matching the Suggested-next wording — cost if wrong: one extra click.
Task 13: complete (commits 25132dd..b0edce8, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit && cd .. && uv run pytest -q tests/serve 2>&1 | tail -1' → 36 passed in 5.86s)
Task 14: Ruling: Re-trace and Run from Memory navigate to Ask showing the running job — one place to watch answers form — cost if wrong: stay on Memory with a progress line instead.
Task 14: complete (commits b0edce8..d0beb61, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  36 passed (36))
Task 15: Ruling: the sidebar footer gets a "Sign in to registry" link when the registry is reachable but you are not signed in — the journey needs a way into the Login dialog besides a failed push — cost if wrong: one link.
Task 15: Ruling: electron-builder also ships dist → resources/ui (the renderer kb serve serves), sets publish: null and `--publish never` — no update feed exists; without it the AppImage build errors — cost if wrong: config lines.
Task 15: Ruling: main.ts uses kb-serve.exe on Windows — the PyInstaller binary has that name there — cost if wrong: none.
Task 15: finding: the first AppImage smoke failed because dist/kb-serve was built before --ui-dir existed; rebuilt the engine and the smoke passed. docs/desktop.md says to always build the engine first.
Task 15: complete (commits d0beb61..9c33069, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  37 passed (37))
Final: fixed concurrent writes to a branch pack (analyse/pull exclusive; ask/push/views shared, 409 otherwise) — test_writer_blocks_readers_on_same_branch, test_reader_blocks_writer_on_same_branch RED→GREEN, serve suite 38/38
Final: fixed blank window from a non-URL registry (config validates http(s); UI hostOf; ErrorBoundary) — test_registry_must_be_http_url, 'a registry value that is not a URL does not crash the shell' RED→GREEN, vitest 39/39
Final: fixed Ask carrying over to another branch — 'changing branch or project drops the previous Ask seed' RED→GREEN, vitest 40/40
Final: fixed endless spinner when the engine dies after start — engine_dying_later_shows_error_screen (E2E) RED→GREEN, 'useJob gives up when the engine stops answering' RED→GREEN; found and fixed a second bug while doing so: the token-header callback used `engine!` and threw once the engine stopped, so every request hung. E2E 5/5, vitest 43/43
Final: fixed Claude Code not found when the app starts from Finder/Dock — electron/env.test.ts 'macOS app started from Finder still finds claude' RED→GREEN
Final: fixed orphaned engine when Electron crashes — test_serve_exits_when_parent_closes_stdin RED→GREEN (engine exits on stdin EOF; os._exit so running jobs stop too)
Final: Ruling: when the engine stops after start, main notifies the open page over IPC instead of navigating to the file:// error page — keeps one code path for the error screen and avoids a cross-origin navigation — cost if wrong: none visible.
Final: minor (deferred): main's kb:open-file trusts any absolute existing file; the checkout check lives in /citation only (and vscode:// URL breaks on Windows paths).
Final: minor (deferred): will-navigate / window-open use a string-prefix origin check; compare URL origins and handle will-redirect.
Final: minor (deferred): links inside answers do nothing on click; route them to openExternal.
Final: minor (deferred): Claude status is only re-read at start and after login; re-check on window focus.
Final: minor (deferred): JobRunner has 2 workers, so long analyses queue asks behind them; separate pool or show queue position.
Final: minor (deferred): /api/feedback lets Langfuse network errors through as a bare 500.
Final: minor (deferred): sharing from Memory when signed out shows the error but doesn't open the Login dialog.
Final: minor (deferred): add_suggestions read-modify-writes suggestions.json without a lock.
Final: Ruling: pytest testpaths = tests — the AppImage build output under desktop/release bundles thousands of third-party tests that a bare pytest collected — cost if wrong: none.
```
