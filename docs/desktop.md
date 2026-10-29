# KB Desktop

The desktop app is an Electron window around the same engine the CLI uses. On start it launches
`kb serve` (the "engine") on a random local port with a one-time token. The engine serves both
the API and the app's pages. The window only talks to `127.0.0.1`, and every request carries the token.

```
Electron main ──spawns──▶ kb serve --port 0 --kb-home … --ui-dir …   (prints KB_SERVE_READY {"port": N})
     │                         │
     └─ window loads http://127.0.0.1:N/  (token added to every request by the main process)
```

## Develop

Use **Node 22 LTS** (`desktop/.nvmrc`). On Node 26, Electron's installer stops partway through unzipping, and `npm run dev` then fails with "Electron failed to install correctly".

```bash
uv sync                          # engine + dev deps (repo root)
npm --prefix desktop install
npm --prefix desktop run dev     # Vite + Electron; the engine runs as `uv run kb serve`
```

The app keeps its project list in `$KB_HOME/desktop.json` (default `~/.kb`).

### Adding a project

Choose **Add a project** (on the empty Library, or **Add a project…** in the project menu).
1. **Name the project and list its repos.** For each repo, paste a GitHub link or choose the folder of a clone you have. A link is cloned with your own git sign-in, so a private repo works if `git clone <link>` works in Terminal.
2. **Read repos.** KB reads each repo's `package.json` and suggests:
   - a name;
   - a role (frontend or backend);
   - the production branch, chosen from the repo's branches;
   - for a monorepo, the package to use (for example `packages/sop-web`);
   - how the repos connect: GraphQL from web to backend (with the backend style), React Router screens and Bull queues.

   Change anything that's off.
3. **Create project.** KB writes `~/.kb/projects/<name>/kb.yaml` and opens the project. You can still edit that file by hand.

**Use an existing kb.yaml…** in the same dialog adds a project from a `kb.yaml` you already have.

### Tests

| What | Command |
|---|---|
| Engine API | `uv run pytest tests/serve` |
| Renderer (Vitest + Testing Library) | `npm --prefix desktop test` |
| Whole app (Playwright driving Electron) | `npm --prefix desktop run build && npm --prefix desktop run e2e` (Linux without a display: `xvfb-run -a npm --prefix desktop run e2e`) |

The end-to-end tests use a scripted fake instead of Claude. Set `KB_SERVE_PROVIDER=module:function`
to make `kb serve` build its LLM provider from that function. The tests set it to
`e2e_fakes:make_provider` from `tests/e2e_support`. This is a testing hook only; leave it unset in real use.
`full_journey` also starts a real `kb-registry` on SQLite and saves screenshots of each view to
`desktop/e2e/screenshots/` (git-ignored).

## Build the app

Always build the engine first. The app bundles whatever is in `dist/kb-serve`, so a stale
engine ships stale behaviour.

```bash
bash packaging/build-sidecar.sh          # PyInstaller one-folder engine → dist/kb-serve (includes Graphify)
npm --prefix desktop run dist            # app for the current OS → desktop/release/
```

| OS | Output | Notes |
|---|---|---|
| macOS | `KB Desktop-<v>-arm64.dmg`, `…-x64.dmg` | build on a Mac, once per architecture you need: the engine is native to the machine that built it |
| Windows | NSIS installer | build on Windows |
| Linux | `KB Desktop-<v>.AppImage` | `npm --prefix desktop run dist -- --linux AppImage` |

The builds are **unsigned** for now.

- **macOS, first launch:** right-click the app in Applications → **Open** → **Open**. After that it opens normally. If macOS says the app "is damaged", run `xattr -dr com.apple.quarantine "/Applications/KB Desktop.app"`.
- **Windows:** SmartScreen → **More info** → **Run anyway**.

Claude Code must be installed and signed in on the machine (`claude` once) to ask new questions.
Without it the app still opens, browses maps and reads saved answers, and the Ask view explains what is missing.
Langfuse is optional. Enter the keys in **Settings** (they are saved to `~/.kb/kb.env`), or put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` in that file yourself
(see `docs/langfuse-local.md`). That gives "Open trace in Langfuse" and thumbs feedback, even when the app is opened from Finder.

If the engine fails to start, the app shows **KB engine didn't start** with the last lines of the log,
a **Retry** button, and **Open logs** (`$KB_HOME/logs/`).

## Mac acceptance checklist (to do on your Mac)

1. In the repo: `uv sync && npm --prefix desktop install`.
2. `bash packaging/build-sidecar.sh && npm --prefix desktop run dist`, and check that `desktop/release/` has a `.dmg`.
3. Install the dmg, then first-launch with right-click → Open. Expect the Library with the empty-state message.
4. Add a project: **Add a project** → name it `sop`, paste the links of `sop-fe-master` and `sop-backend-main` (or choose your clones) → **Read repos**. Check that the web repo uses `packages/sop-web`, the GraphQL link uses `graphql-compose`, and the production branches are right → **Create project**.
5. **Analyse a branch** → `main`. Watch the progress steps. The row ends as "Private · local only".
6. **Open** → the Map shows the SOP modules in plain English. Toggle **Code names**, pick **Tasks**, then **Ask about Tasks**. Switch to **Graph**: each repo is a column. Lines show GraphQL calls from web to backend and the jobs queued for workers. Pick a line to list the operations with their file and line on both sides.
7. Ask "How does scheduling work in Tasks?" with real Claude Code. Expect UI flow and Backend flow with citations, a diagram, and "n of m steps verified". Click a citation: it opens in your editor. A citation to a script or a file with no extension is shown in Finder rather than opened, so nothing runs.
8. With the registry from `docs/registry-acceptance.md` Run 2:
   - sign in from the sidebar (**Sign in to registry**);
   - **Push to registry**, then **Share to sop@main** on the answer;
   - Memory shows the answer as Published.
9. Second profile: quit the app, then start it with another home, for example
   `KB_HOME=~/.kb-second open -a "KB Desktop"`.
   - Sign in as the viewer, **Pull** `main`, and ask the same question.
   - Expect "Reused a saved answer — no Claude call".
10. Quit the app, then confirm no engine is left running: `pgrep -f kb-serve` prints nothing.
