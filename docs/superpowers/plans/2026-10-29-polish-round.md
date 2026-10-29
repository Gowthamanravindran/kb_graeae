# Polish Round Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the eight minors deferred by the stage-3 review, and add a Settings screen so Langfuse keys, the GitHub token and registry sign-in are managed in the app instead of in files.

**Architecture:** Small, independent fixes in `kb serve` (`src/kb/serve/`), the pack store, the Electron main process (`desktop/electron/`) and the renderer (`desktop/src/`). Settings read and write `$KB_HOME/kb.env` through new functions in `src/kb/envfile.py` and a new `/api/settings` route.

**Tech Stack:** Python 3.12, FastAPI, pytest; Electron 33, React 18, Vitest + Testing Library, Playwright.

**Spec:** the deferred minors listed in `docs/superpowers/ledgers/2026-10-28-stage3-desktop.md` ("Final: minor (deferred)" lines), and the Settings request agreed on 2026-10-29. There is no separate spec; the decisions below are the design.

## Global Constraints

- Commit to branch `phase1` only; never push.
- `kb serve` stays 127.0.0.1-only, behind `TokenHostMiddleware`; the page never sees the engine token.
- `kb.env` keeps comments and unknown lines when written, and is written with mode `0600`.
- Secrets are never returned to the page in full: the API returns `{set: bool, last4: str | null, source: "file" | "environment" | null}`.
- Settings managed by the screen (exact keys): `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST`, `GITHUB_TOKEN`. Of these, `LANGFUSE_HOST` is not secret, so it is returned in full.
- The registry URL stays in each project's `kb.yaml`. The Settings screen shows it, signs in and out, and opens the file; it does not rewrite `kb.yaml`.
- Tests: `uv run pytest -q` and `cd desktop && npx vitest run && npx tsc -p tsconfig.json --noEmit && npx tsc -p tsconfig.electron.json --noEmit`; E2E with `xvfb-run -a npx playwright test` in `desktop/`.

## Review Focus

1. **A key is set both in the shell environment and in kb.env.** The screen shows the environment value wins ("set in your environment") and saving the file does not pretend to change it. Covered by `test_settings_reports_environment_source` in Task 9.
2. **Saving one setting.** It must not drop comments, other keys or unrelated lines in kb.env. Covered by `test_write_env_file_preserves_other_lines` in Task 9.
3. **Opening a citation path that the engine did not return** (a compromised page). Main must refuse. Covered by `open_citation_refuses_paths_the_engine_did_not_give` in Task 1.
4. **A long analysis running.** A new question must still start, not wait an hour. Covered by `test_ask_runs_while_two_analyses_block` in Task 5.
5. **Two answers finishing at the same moment.** Neither answer's suggestions may be lost. Covered by `test_add_suggestions_concurrent_keeps_all` in Task 8.

---

### Task 1: Open citations through the engine, not raw paths

**Files:**
- Create: `desktop/electron/citations.ts`, `desktop/electron/citations.test.ts`
- Modify: `desktop/electron/main.ts`, `desktop/electron/preload.ts`, `desktop/src/global.d.ts`, `desktop/src/views/Ask.tsx`

**Interfaces:**
- Produces:
  - `openCitation(engine: {port: number; token: string} | null, c: {project, branch, repo, file, line}, deps: {fetch: typeof fetch; openPath(p: string): Promise<string>; openExternal(u: string): Promise<void>; editor?: string}) => Promise<{ok: boolean; error?: string}>`.
  - `editorUrl(path: string, line: number): string`.
  - IPC `kb:open-citation`, and `window.kb.openCitation(c)`. This replaces `openFile`.

- [ ] **Step 1:** Write the failing tests in `desktop/electron/citations.test.ts`:
  - `open_citation_asks_the_engine_then_opens`: the fake fetch is called with `http://127.0.0.1:4000/api/projects/sop/branches/feature%2Fx/citation?repo=web&file=src%2Fa.ts&line=7` and header `X-KB-Token: t`, and it returns `{path: "/w/src/a.ts", line: 7}`. Assert `openPath` was called with `"/w/src/a.ts"`.
  - `open_citation_refuses_paths_the_engine_did_not_give`: the engine answers 404. Assert the result is `{ok: false, error: "file not found in your checkout of web"}` and `openPath` was not called.
  - `editor_url_handles_windows_paths`: `editorUrl("C:\\w\\a.ts", 3) === "vscode://file/C:/w/a.ts:3"` and `editorUrl("/w/a.ts", 3) === "vscode://file/w/a.ts:3"`.
- [ ] **Step 2:** Run `cd desktop && npx vitest run electron/citations.test.ts`. Expected: FAIL, because the module cannot be resolved.
- [ ] **Step 3:** Implement `citations.ts`. Branch segments are encoded per segment, as in `src/api/client.ts`. `editor === "vscode"` uses `openExternal(editorUrl(...))`.
- [ ] **Step 4:** Wire it up. In `main.ts`, `ipcMain.handle("kb:open-citation", …)` calls `openCitation(engine, c, {fetch, openPath: shell.openPath, openExternal: shell.openExternal, editor: process.env.KB_EDITOR})`. Delete the `kb:open-file` handler and `openFile` from the preload and `global.d.ts`. In `Ask.tsx`, `open()` calls `window.kb?.openCitation({project, branch, repo, file, line})` and shows `error` when present. Update `Ask.test.tsx`'s citation test to assert `openCitation` was called with `{project: "sop", branch: "main", repo: "sop-web", file: "src/tasks/Schedule.tsx", line: 12}`.
- [ ] **Step 5:** Run vitest and both `tsc` projects. Expected: all pass. Commit: `fix(desktop): open citations via the engine only`.

### Task 2: Exact-origin navigation checks

**Files:**
- Modify: `desktop/electron/security.ts`
- Create: `desktop/electron/security.test.ts`

**Interfaces:**
- Produces: `isAppUrl(url: string, origin: string | null): boolean`, which is true only when `new URL(url).origin === origin`, and false on parse errors. `lockDown` uses it for `will-navigate`, `will-redirect` and `setWindowOpenHandler`. External http(s) URLs (not the app's origin) still go to `shell.openExternal`.

- [ ] **Step 1:** Write the failing test `is_app_url_compares_whole_origins`:
  - `isAppUrl("http://127.0.0.1:40001/", "http://127.0.0.1:4000") === false`;
  - `isAppUrl("http://127.0.0.1:4000/x", "http://127.0.0.1:4000") === true`;
  - `isAppUrl("not a url", "http://127.0.0.1:4000") === false`;
  - `isAppUrl("http://127.0.0.1:4000/", null) === false`.
- [ ] **Step 2:** Run it. Expected: FAIL (no export).
- [ ] **Step 3:** Implement and use it in `lockDown`; add `win.webContents.on("will-redirect", …)` with the same rule as `will-navigate`.
- [ ] **Step 4:** Run vitest, `tsc -p tsconfig.electron.json`, and the Playwright E2E. Expected: all pass. Commit: `fix(desktop): compare navigation origins exactly`.

### Task 3: Links inside answers open in the browser

**Files:**
- Create: `desktop/src/components/Markdown.tsx`, `desktop/src/components/Markdown.test.tsx`
- Modify: `desktop/src/views/Ask.tsx`, `desktop/src/views/MapView.tsx`

**Interfaces:**
- Produces: `Markdown({children: string, mermaid?: boolean})`. It wraps `react-markdown`, renders `a` as a button-styled link whose click calls `window.kb?.openExternal(href)` for http(s) hrefs only, and, when `mermaid` is set, renders ```` ```mermaid ```` blocks with `Mermaid`. This is the MapView behaviour moved here.

- [ ] **Step 1:** Write the failing tests:
  - `link_opens_externally`: render `[docs](https://example.com/a)`, click "docs", and assert `window.kb.openExternal` was called with `"https://example.com/a"`.
  - `non_http_link_does_nothing`: `[x](javascript:alert(1))` is clicked; `openExternal` is not called.
- [ ] **Step 2:** Run it. Expected: FAIL (module missing).
- [ ] **Step 3:** Implement, and replace both `ReactMarkdown` uses with `Markdown`.
- [ ] **Step 4:** Run vitest (the MapView and Ask tests still pass). Commit: `fix(desktop): links in answers open in the browser`.

### Task 4: Claude status re-checked on focus

**Files:**
- Modify: `src/kb/serve/app.py` (the `claude_status` cache and the `/api/status` route), `desktop/src/state.tsx`
- Test: `tests/serve/test_security.py`, `desktop/src/state.test.tsx`

**Interfaces:**
- Produces:
  - `GET /api/status?fresh=1` bypasses the 60-second cache.
  - `api.status(fresh?: boolean)`.
  - `AppProvider` calls `refresh(true)` on `window` `focus`, at most once every 10 seconds.

- [ ] **Step 1:** Write the failing tests:
  - `test_status_fresh_bypasses_cache`: the provider is unavailable on the first call and available on the second. Assert `/api/status` is still unavailable, then `/api/status?fresh=1` is available.
  - Vitest `focus_rechecks_status`: dispatch `focus` on `window`; `api.status` is called with `true`.
- [ ] **Step 2:** Run them. Expected: both FAIL.
- [ ] **Step 3:** Implement `ServeSettings.claude_status(fresh: bool = False)`, the `fresh` query parameter, and the focus listener.
- [ ] **Step 4:** Run pytest `tests/serve` and vitest. Expected: pass. Commit: `fix: re-check Claude Code when the window regains focus`.

### Task 5: Questions never queue behind analyses

**Files:**
- Modify: `src/kb/serve/jobs.py`
- Test: `tests/serve/test_jobs.py`

**Interfaces:**
- Produces: `JobRunner(max_workers: int = 2, log_dir=None, ask_workers: int = 2)`. Jobs of kind `"ask"` run on a separate pool, so analyse, pull and push jobs share `max_workers` while questions have their own.
- Also: `Job.queue_position: int | None`, included in `to_dict()`. It is the number of jobs ahead in the same pool while the job is `queued`.

- [ ] **Step 1:** Write the failing tests:
  - `test_ask_runs_while_two_analyses_block`: submit two `analyze` jobs waiting on a gate, then one `ask` job returning 1. The ask job is `done` within 5 seconds while both analyses are still running.
  - `test_queued_job_reports_position`: with both general workers busy, a third `pull` job has `queue_position == 1`.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement with two `ThreadPoolExecutor`s. The queue position is computed from `created_at` order among `queued` jobs of the same pool.
- [ ] **Step 4:** Show it in the UI: `desktop/src/views/Ask.tsx` `Running` shows `Waiting for N earlier jobs` when `job.state === "queued" && job.queue_position`. `desktop/src/api/types.ts` gets `queue_position?: number | null`.
- [ ] **Step 5:** Run pytest and vitest. Commit: `fix(serve): separate pool for questions; show queue position`.

### Task 6: Feedback reports Langfuse errors clearly

**Files:**
- Modify: `src/kb/serve/app.py` (`/api/feedback`)
- Test: `tests/serve/test_map_memory.py`

- [ ] **Step 1:** Write the failing test `test_feedback_langfuse_down_is_502`. With keys set and `langfuse.Langfuse` monkeypatched so `create_score` raises `ConnectionError("refused")`, the response is 502 with detail `"Langfuse is unreachable: refused"`, and `serve.log` gets the traceback.
- [ ] **Step 2:** Run it. Expected: FAIL (500).
- [ ] **Step 3:** Catch `Exception` around the Langfuse calls, log through `jobs.log.exception`, and raise `HTTPException(502, f"Langfuse is unreachable: {e}")`.
- [ ] **Step 4:** Run pytest. Commit: `fix(serve): feedback returns a clear 502 when Langfuse is down`.

### Task 7: Sharing from Memory while signed out opens sign-in

**Files:**
- Modify: `desktop/src/views/Memory.tsx`, `desktop/src/views/Ask.tsx` (Share)
- Test: `desktop/src/views/Memory.test.tsx`, `desktop/src/views/Ask.test.tsx`

- [ ] **Step 1:** Write the failing tests:
  - `share_signed_out_opens_login`: `publish` rejects with `new ApiError(401, "not signed in")`; after clicking "Share 2 selected", a dialog titled "Sign in to the registry" is visible.
  - The same test for Ask's "Share to sop@main".
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Render `LoginDialog` from both views when an `ApiError` with status 401 is caught, the same way `Library.tsx` does.
- [ ] **Step 4:** Run vitest. Commit: `fix(desktop): sharing while signed out opens sign-in`.

### Task 8: Suggestions survive concurrent saves

**Files:**
- Modify: `src/kb/packs/store.py`
- Test: `tests/test_packs.py`

**Interfaces:**
- Produces: module-level `_overlay_locks: dict[Path, threading.Lock]` and `_overlay_lock(path: Path) -> threading.Lock`. `add_suggestions` and `save_module_note` hold the lock for their read-modify-write, and `_write_json` writes to a temp file and then `os.replace`s it.

- [ ] **Step 1:** Write the failing test `test_add_suggestions_concurrent_keeps_all`. 20 threads each add one distinct suggestion to the same pack; afterwards, `load(...).suggestions` has 20 items.
- [ ] **Step 2:** Run it, 5 times in a loop, so the failure is observed. Expected: FAIL in at least one run.
- [ ] **Step 3:** Implement the lock and atomic write.
- [ ] **Step 4:** Run `uv run pytest -q tests/test_packs.py` 5 times. Expected: pass every time. Commit: `fix(packs): lock overlay read-modify-write`.

### Task 9: Settings screen

**Files:**
- Modify: `src/kb/envfile.py`, `src/kb/sync/credentials.py`, `src/kb/serve/app.py`, `desktop/src/api/client.ts`, `desktop/src/api/types.ts`, `desktop/src/state.tsx` (view `"settings"`), `desktop/src/components/Sidebar.tsx`, `desktop/src/App.tsx`, `docs/langfuse-local.md`, `docs/desktop.md`
- Create: `desktop/src/views/Settings.tsx`, `desktop/src/views/Settings.test.tsx`, `tests/serve/test_settings.py`
- Test: `tests/test_envfile.py`

**Interfaces:**
- Produces:
  - `write_env_file(path: Path, updates: dict[str, str | None]) -> None`. `None` removes the key. Comments and other lines are kept, new keys are appended, and the file is chmod `0600`.
  - `env_sources(path: Path, keys: list[str]) -> dict[str, "file" | "environment" | None]`. This needs the keys the file applied at load: `load_env_file` records them in a module-level `_applied: set[str]`.
  - `GET /api/settings`, returning `{env_path: str, values: {KEY: {set, last4, source, value?}}}`. `value` is present only for `LANGFUSE_HOST`.
  - `PUT /api/settings` with `{values: {KEY: str | null}}`. It refuses unknown keys with 400 `"unknown setting KEY"`, writes the file, sets or unsets `os.environ` for keys whose source is not `environment`, and returns the same shape as GET.
  - `api.settings()`, `api.saveSettings(values)`.
  - View `"settings"` in `View`, and a sidebar entry "Settings" below the others.

- [ ] **Step 1:** Write the failing Python tests:
  - `test_write_env_file_preserves_other_lines`: a file with `# note\nFOO=1\nLANGFUSE_HOST=http://a\n` gets `{LANGFUSE_HOST: "http://b", GITHUB_TOKEN: "ghp_x"}`. The result is exactly `# note\nFOO=1\nLANGFUSE_HOST=http://b\nGITHUB_TOKEN=ghp_x\n`, with mode `0o600`.
  - `test_write_env_file_removes_key`: `{FOO: None}` removes the `FOO` line.
  - `test_settings_masks_secrets`: after PUT `LANGFUSE_SECRET_KEY=sk-lf-abcd1234`, GET returns `{set: true, last4: "1234", source: "file"}` with no `value` key.
  - `test_settings_reports_environment_source`: with `GITHUB_TOKEN` in the process environment before the app starts, GET shows `source: "environment"`. A PUT of a new value writes the file but leaves `os.environ["GITHUB_TOKEN"]` unchanged.
  - `test_settings_rejects_unknown_key`: 400.
  - `test_settings_applies_langfuse_to_status`: PUT all three Langfuse keys; `/api/status` then shows `langfuse.configured: true` with the host.
- [ ] **Step 2:** Run them. Expected: FAIL.
- [ ] **Step 3:** Implement `envfile` and the routes. The path is `settings.kb_home / "kb.env"`.
- [ ] **Step 4:** Write the failing vitest tests in `Settings.test.tsx`:
  - `shows_masked_and_saves`: the secret shows `••••1234`; typing a new public key and clicking "Save" calls `saveSettings({LANGFUSE_PUBLIC_KEY: "pk-lf-new"})`, and the text "Saved. New questions use these settings." appears.
  - `environment_value_is_read_only`: with `source: "environment"`, the input is disabled and the helper text reads "Set in your environment; that value wins over this file."
  - `registry_section_lists_projects`: each project shows its registry URL (or "No registry in kb.yaml"), "Sign in" or "Signed in as me@corp · Sign out", and "Open kb.yaml". The last calls `window.kb.openExternal("file://" + config_path)`.
- [ ] **Step 5:** Implement `Settings.tsx` in three sections: "Langfuse" (public key, secret key, host, plus a "Test connection" button that calls `api.status(true)` and shows configured or not), "GitHub" (token, with the helper "Used to read pull request titles and descriptions."), and "Registries" (per project). Sign-out is `api.logout(registry)` → a new `POST /api/logout {registry}` → a new `forget_token(kb_home: Path, registry_url: str) -> bool` in `src/kb/sync/credentials.py`, which removes the entry for `norm_url(registry_url)`. Add the test `test_logout_forgets_token`: after login, logout makes `/api/projects/{p}/registry` report `logged_in: false`.
- [ ] **Step 6:** Run all tests and the E2E. Update `docs/langfuse-local.md` and `docs/desktop.md` to say keys can be entered in Settings. Commit: `feat(desktop): Settings screen for Langfuse, GitHub and registries`.

### Task 10: Final checks

- [ ] Run the full suites (pytest, vitest, both tsc, Playwright). Then build the AppImage and run the smoke from `docs/desktop.md`: `npm --prefix desktop run dist -- --linux AppImage`, start it under `xvfb-run` with `--no-sandbox`, and check that the Library and Settings render.
- [ ] Ledger: write `docs/superpowers/ledgers/2026-10-29-polish-round.md` with the rulings and the test evidence. Commit: `docs: polish round ledger`.
