# Polish round — execution ledger

AppImage smoke after the round: Library and Settings render (SMOKE OK).

```
# SDD ledger — plan: docs/superpowers/plans/2026-10-29-polish-round.md
Pre-flight: tasks 1-8 independent; Task 9 consumes envfile (exists) and credentials (forget_token new in T9); Task 7 LoginDialog exists; no conflicts
Task 1: Ruling: citation URL encodes the branch per segment (feature/x, not feature%2Fx as the plan's example wrote) — matches the renderer client and the {branch:path} route — cost if wrong: none.
Task 1: complete (commits b5595a0..bef23a0, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit && npx tsc -p tsconfig.electron.json --noEmit' →       Tests  49 passed (49))
Task 2: complete (commits bef23a0..cf0445e, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.electron.json --noEmit' →       Tests  50 passed (50))
Task 3: complete (commits cf0445e..64cf175, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  52 passed (52))
Task 4: complete (commits 64cf175..57a6af5, tests: bash -c 'uv run pytest -q tests/serve 2>&1 | tail -1 && cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests'' →       Tests  53 passed (53))
Task 5: Ruling: queue_position is the 1-based place in line among queued jobs of the same pool (the plan's test expects 1 for the first waiting job), and the Ask text reads 'Queued behind other questions (position N)' instead of 'Waiting for N earlier jobs' — the count of running jobs ahead isn't meaningful to show — cost if wrong: wording.
Task 5: complete (commits 57a6af5..524cb0f, tests: bash -c 'uv run pytest -q tests/serve 2>&1 | tail -1 && cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests'' →       Tests  54 passed (54))
Task 6: complete (commits 524cb0f..3f33687, tests: bash -c 'uv run pytest -q tests/serve 2>&1 | tail -1' → 43 passed in 7.23s)
Task 7: complete (commits 3f33687..17c19f0, tests: bash -c 'cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit' →       Tests  56 passed (56))
Task 8: Ruling: save_module_note writes one file without reading first, so it needs no lock; instead _write_json uses a per-thread temp name (the shared .tmp name could collide between threads) — cost if wrong: none.
Task 8: complete (commits 17c19f0..e0b0eb7, tests: bash -c 'uv run pytest -q 2>&1 | tail -1' → 323 passed in 36.92s)
Task 9: Ruling: "Open kb.yaml" became "Show kb.yaml" through a new IPC kb:reveal-config(project): main asks the engine for the project's config_path and calls shell.showItemInFolder — the plan's openExternal("file://…") is refused by main (http(s) only), and taking a path from the page would undo Task 1 — cost if wrong: one IPC.
Task 9: Ruling: env_source tracks keys that came from kb.env or Settings in envfile._applied; anything else in os.environ is "environment" and Settings never overwrites it — cost if wrong: none.
Task 9: complete (commits e0b0eb7..a31685d, tests: bash -c 'uv run pytest -q 2>&1 | tail -1 && cd desktop && npx vitest run 2>&1 | grep -E '^ +Tests' && npx tsc -p tsconfig.json --noEmit && npx tsc -p tsconfig.electron.json --noEmit' →       Tests  62 passed (62))
Task 10: Ruling: one fresh whole-branch review covers the polish round and stage 5a together (run after 5a) — the two plans touch the same files and the user ran them back to back — cost if wrong: polish findings arrive later.
```
