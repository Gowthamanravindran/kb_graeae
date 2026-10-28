# Stage 2: Registry + Sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `kb-registry` server (on-prem Docker Compose: FastAPI + Postgres + MinIO) and CLI commands `kb login / list / push / pull / publish / sync` so one person's analysed branch can be pulled and extended by others.

**Architecture:** A new package `src/kb_registry/` (server) shares pack models with `src/kb/` (client). Pack base files are uploaded as content-addressed, zstd-compressed blobs; a base row maps allowed pack paths to blob hashes. Published overlay items (Q&As, flows, suggestions, module notes) are append-only rows per project+branch. The registry never runs an LLM. Auth is static API tokens plus OIDC (device-code login on the client, JWT validation on the server).

**Tech Stack:** FastAPI + Uvicorn, SQLAlchemy 2 + Alembic, Postgres 16 (SQLite in fast tests), boto3 (S3/MinIO; moto in tests), zstandard, PyJWT[crypto], httpx (client), Typer (CLIs), pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-codebase-knowledge-system-design.md` — §3.3 (versioning, visibility), §3.4 (no source), §7 (sync, auth, permissions, deployment), §8 (errors), §10 items 18–23, §12 stage 2. Stage 1 code: `src/kb/`; ledger: `docs/superpowers/ledgers/2026-10-28-stage1-core-cli.md`.

## Global Constraints

- The registry never runs an LLM and never reads source code; it stores only pack files and overlay items (spec §7).
- Allowed base file paths (both server-validated on push and client-validated on pull): `manifest.json`, `base/links.json`, `base/modules.json`, `base/system.md`, `base/graph/<repo>.json` where `<repo>` matches `^[A-Za-z0-9._-]+$`. Anything else → HTTP 400 / client `KbError`. `index.sqlite` is never uploaded (rebuilt on pull).
- Blobs: key = sha256 hex of the **uncompressed** bytes; transferred and stored zstd-compressed (`Content-Type: application/zstd`); server decompresses to verify the hash before storing. Max uncompressed blob 200 MB.
- One base per `(project, branch, commits_key)`; a second push of the same commit set → HTTP 409 `{"detail": "already exists — pull instead", "version": <n>}`. `commits_key` = `kb.analyze._commits_key(manifest.commits)`.
- Every accepted push creates version `n+1` (first = 1) with `parent = n`; versions are immutable.
- Overlay items are append-only and keyed by item id: same id + identical payload → accepted as `existing`; same id + different payload → HTTP 409. Max 500 items per request, 256 KB per item payload. Kinds: `qa`, `flow`, `suggestion`, `module_note`.
- Roles per project: `viewer` (list, pull), `contributor` (+ publish), `maintainer` (+ push bases, put config). Server admins (`users.is_admin`) pass every check and are the only ones who create projects/users/grants (admin CLI).
- API prefix `/api/v1`. Errors are JSON `{"detail": "..."}`. 401 = missing/invalid credentials; 403 = role too low (detail names the needed role); 404 = unknown project/branch.
- Client credentials live in `$KB_HOME/credentials.json`, mode 0600, keyed by registry URL (no trailing slash).
- `kb.yaml`'s `registry:` is the default registry for push/pull/publish/sync/list; missing → `ConfigError("set registry: in kb.yaml or pass --registry")`.
- Registry unreachable: push/pull/list fail with exit 1 and "registry unreachable: <url>"; `kb publish` queues to the outbox and exits 0 with "queued" (spec §8).
- Server settings come from env: `KB_REGISTRY_DATABASE_URL`, `KB_REGISTRY_STORAGE` (`file:///path` or `s3://bucket`), `KB_REGISTRY_S3_ENDPOINT`, `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `KB_REGISTRY_OIDC_ISSUER`, `KB_REGISTRY_OIDC_AUDIENCE`, `KB_REGISTRY_OIDC_CLIENT_ID`.
- Transfer format (decision, spec §10 #23): each base file is its own content-addressed zstd blob instead of one `.tar.zst`, so an unchanged repo graph is never re-uploaded or re-downloaded.
- `src/kb_registry/__init__.py` and `paths.py` import nothing outside the standard library, because the client imports `kb_registry.paths` without the `registry` extra installed.
- Server deps go in an optional extra: `uv add --optional registry fastapi uvicorn sqlalchemy alembic "psycopg[binary]" boto3 zstandard "pyjwt[crypto]"`; client deps `httpx zstandard` go in main deps; dev: `moto[s3]`.

## Review Focus

1. **Registry unreachable during `kb publish`** — items are queued in `$KB_HOME/outbox/`, the command exits 0 saying "queued", and the next successful push/pull/publish/sync for that project+branch sends them. → Task 11 `test_publish_offline_queues_then_sync_sends`.
2. **`kb pull` over a local pack that has private, unpublished items (and a different base)** — base is replaced, private items survive untouched. → Task 10 `test_pull_keeps_private_items`.
3. **Malicious or corrupt file map on pull** (`../../.ssh/x`, absolute path, unknown file) — nothing written outside the pack dir; pull fails with `KbError`. → Task 10 `test_pull_rejects_unsafe_paths`.
4. **Viewer token tries `kb push` / `kb publish`** — exit 1 with "your role on sop is viewer; this needs maintainer" (not a traceback, not a silent no-op). → Task 9 `test_push_as_viewer_explains_role`, Task 11 `test_publish_as_viewer_explains_role`.
5. **Push interrupted after some blobs uploaded** — re-running `kb push` uploads only missing blobs and creates the base. → Task 9 `test_repush_after_partial_upload_sends_only_missing`.

---

## File Structure

```
src/kb_registry/
  __init__.py
  settings.py        Settings (env) + get_settings()
  db.py              engine/session factory, Base
  models.py          Project, User, Membership, ApiToken, Blob, BaseRow, BaseFile, OverlayItem
  storage.py         BlobStore protocol, FileBlobStore, S3BlobStore, make_store()
  auth.py            token hashing, current_user dependency, require_role(), OIDC JWT validation
  paths.py           ALLOWED_PATH regex + is_allowed_path()   (shared rule; client imports it too)
  api/__init__.py    create_app(settings) -> FastAPI
  api/projects.py    /auth/config, /me, /projects..., config
  api/blobs.py       /blobs/missing, PUT/GET /blobs/{hash}
  api/packs.py       bases, versions, latest, overlay
  admin.py           Typer app: create-project, create-user, grant, token
  cli.py             `kb-registry` Typer app: serve, admin ...
  migrations/        alembic env + 0001_initial.py
src/kb/sync/
  __init__.py
  client.py          RegistryClient, RegistryError, RegistryUnavailable
  credentials.py     load/save credentials
  oidc.py            device_login(), refresh()
  pack_io.py         base_files(), materialize_base(), overlay_items_for_publish(), apply_overlay()
  outbox.py          Outbox
  commands.py        push_pack(), pull_pack(), publish_items(), flush_outbox()
src/kb/cli.py        + login, list, push, pull, publish, sync
deploy/registry/
  Dockerfile, docker-compose.yml, .env.example, README.md
tests/registry/      conftest.py (app/client fixtures, sqlite + optional postgres), test_*.py
tests/sync/          test_*.py
```

---

### Task 1: Registry scaffold, settings, models, migration

**Files:** Create `src/kb_registry/{__init__,settings,db,models,paths}.py`, `src/kb_registry/migrations/` (alembic.ini at `src/kb_registry/alembic.ini`), `tests/registry/conftest.py`, `tests/registry/test_models.py`; modify `pyproject.toml` (extra `registry`, script `kb-registry = "kb_registry.cli:main"`, package list).

**Interfaces:**
- Produces:
  - `Settings(database_url: str, storage: str, s3_endpoint: str | None, oidc_issuer: str | None, oidc_audience: str | None, oidc_client_id: str | None)`; `get_settings() -> Settings` reading the env names in Global Constraints (defaults: `sqlite:///./kb-registry.db`, `file://./kb-registry-blobs`).
  - `make_engine(url) -> Engine`, `make_sessionmaker(engine)`, declarative `Base`.
  - Models (all ids int autoincrement unless stated):
    - `Project(id, name unique, config_yaml text default "", created_at)`
    - `User(id, email unique, name, is_admin bool default False, oidc_sub str|None unique, created_at)`
    - `Membership(user_id, project_id, role enum viewer|contributor|maintainer)` PK (user_id, project_id)
    - `ApiToken(id, user_id, name, token_hash unique (sha256 hex), created_at, last_used_at|None)`
    - `Blob(hash str(64) PK, size int, created_at)`
    - `BaseRow(id, project_id, branch, commits_key, commits json, manifest json, version int, parent int|None, created_by user_id, created_at)` unique (project_id, branch, commits_key) and unique (project_id, branch, version)
    - `BaseFile(base_id, path, blob_hash FK Blob)` PK (base_id, path)
    - `OverlayItem(project_id, branch, item_id, kind, payload json, payload_sha str(64), author_id, base_id FK, published_at)` PK (project_id, branch, item_id)
  - `paths.is_allowed_path(path: str) -> bool` per Global Constraints.
  - `alembic upgrade head` builds exactly these tables.
- Test fixtures (conftest): `db_url` parametrised `["sqlite"]` plus `"postgres"` when `initdb` is on PATH or under `/usr/lib/postgresql/*/bin` (temp cluster started with `runuser -u postgres` when running as root; skipped otherwise).

- [ ] **Step 1: Failing tests:** `test_migration_creates_all_tables[db_url]` (run alembic upgrade head on a fresh DB; inspector lists the 8 tables); `test_unique_base_per_commit_set` (second BaseRow with same project/branch/commits_key → IntegrityError); `test_allowed_paths` (`manifest.json`, `base/graph/sop-web.json` allowed; `base/graph/../x.json`, `/etc/passwd`, `base/index.sqlite`, `base/graph/a/b.json` rejected).
- [ ] **Step 2:** Run `uv run pytest tests/registry/test_models.py -v` → FAIL (import).
- [ ] **Step 3:** Implement; migration `0001_initial` written by hand to match models (no autogenerate at runtime).
- [ ] **Step 4:** Run → PASS on sqlite (and postgres where available).
- [ ] **Step 5: Commit** `feat(registry): models, settings and initial migration`

---

### Task 2: Blob storage

**Files:** Create `src/kb_registry/storage.py`, `tests/registry/test_storage.py`

**Interfaces:**
- `class BlobStore(Protocol): def put(self, hash: str, compressed: bytes) -> None; def get(self, hash: str) -> bytes; def exists(self, hash: str) -> bool` (stores/returns the zstd-compressed bytes as given).
- `FileBlobStore(root: Path)` — path `root/<hash[:2]>/<hash>`; write via temp file + rename.
- `S3BlobStore(bucket: str, endpoint: str | None)` — key `blobs/<hash>`.
- `make_store(settings) -> BlobStore` from `file://…` / `s3://bucket`.
- `verify_and_size(hash: str, compressed: bytes, max_bytes: int = 200 * 2**20) -> int` — decompresses (streaming, stops past `max_bytes` → `ValueError("too large")`), checks sha256 == hash (`ValueError("hash mismatch")`), returns uncompressed size.

- [ ] **Step 1: Failing tests:** roundtrip for both stores (S3 via `moto.mock_aws`, bucket created in test); `test_verify_rejects_mismatch`; `test_verify_rejects_oversize` (monkeypatch max to 10 bytes); `test_make_store_parses_urls`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): blob storage (file, S3)`

---

### Task 3: Auth (static tokens), roles, admin CLI

**Files:** Create `src/kb_registry/auth.py`, `src/kb_registry/admin.py`, `src/kb_registry/cli.py`, `src/kb_registry/api/__init__.py`, `tests/registry/test_auth.py`

**Interfaces:**
- `new_token() -> str` (`"kbr_" + secrets.token_urlsafe(32)`), `hash_token(t) -> str` (sha256 hex).
- FastAPI dependency `current_user(request) -> User` (Bearer token → ApiToken lookup, updates `last_used_at`; 401 otherwise). OIDC branch added in Task 12.
- `require_role(session, user, project_name, role) -> Project` — 404 unknown project (also when the user has no membership, to avoid leaking names), 403 `"your role on <p> is <r>; this needs <role>"`; admins pass.
- `create_app(settings, store: BlobStore | None = None) -> FastAPI` (stores session factory and store on `app.state`; routers added in later tasks).
- Admin CLI (`kb-registry admin …`, works on `KB_REGISTRY_DATABASE_URL`): `create-project NAME [--config FILE]`, `create-user EMAIL [--name] [--admin]` → prints a new token once, `grant EMAIL PROJECT ROLE`, `token EMAIL [--name]` → prints a new token. `kb-registry serve [--host 0.0.0.0] [--port 8080]`, `kb-registry migrate` (alembic upgrade head).

- [ ] **Step 1: Failing tests** (TestClient on a tiny probe route registered in the test): `test_missing_or_bad_token_is_401`; `test_require_role_ladder` (viewer passes viewer, fails contributor with the exact detail text); `test_non_member_gets_404`; `test_admin_passes_everything`; `test_admin_cli_create_user_prints_working_token` (CliRunner on admin app against sqlite file; token authenticates).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): token auth, roles, admin CLI`

---

### Task 4: Project and branch read API

**Files:** Create `src/kb_registry/api/projects.py`, `tests/registry/test_api_projects.py`

**Interfaces (all under `/api/v1`):**
- `GET /auth/config` (no auth) → `{"oidc": null}` or `{"oidc": {"issuer", "client_id", "audience"}}`.
- `GET /me` → `{"email", "is_admin", "projects": {name: role}}`.
- `GET /projects` → `[{"name", "role"}]` (only projects with membership; all for admins).
- `GET /projects/{p}` (viewer) → `{"name", "config_yaml"}`; `PUT /projects/{p}/config` (maintainer) body `{"config_yaml"}` — must parse with `kb.config.ProjectConfig` (400 with the ConfigError text otherwise) and its `project` must equal `{p}`.
- `GET /projects/{p}/branches` (viewer) → `[{"branch", "latest_version", "commits", "created_by", "created_at", "overlay_count"}]` sorted by branch.
- `GET /projects/{p}/branches/{b}/versions` (viewer) → `[{"version", "parent", "commits", "created_by", "created_at"}]` newest first. Branch names may contain `/` (route uses `{b:path}` with `/versions` and `/latest` suffix routes declared first).

- [ ] **Step 1: Failing tests:** `test_projects_lists_only_memberships`; `test_put_config_validates_and_requires_maintainer`; `test_branches_and_versions_shapes` (rows inserted directly); `test_branch_with_slash` (`feature/x`).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): project and branch read API`

---

### Task 5: Blob API

**Files:** Create `src/kb_registry/api/blobs.py`, `tests/registry/test_api_blobs.py`

**Interfaces:**
- `POST /blobs/missing` body `{"hashes": [..≤5000]}` → `{"missing": [...]}` (any authenticated user who is maintainer somewhere or admin; else 403).
- `PUT /blobs/{hash}` raw zstd body → 201 `{"hash", "size"}`; 200 if it already exists; 400 `"hash mismatch"` / `"too large"`; same role rule as above. Hash must match `^[0-9a-f]{64}$` (400).
- `GET /blobs/{hash}` → zstd bytes; allowed only if some `BaseFile` with that hash belongs to a base in a project the caller can view (else 404).

- [ ] **Step 1: Failing tests:** `test_missing_then_put_then_not_missing`; `test_put_rejects_hash_mismatch_and_stores_nothing`; `test_get_requires_view_access_to_a_referencing_base`; `test_viewer_only_user_cannot_upload`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): content-addressed blob API`

---

### Task 6: Push base API

**Files:** Create `src/kb_registry/api/packs.py`, `tests/registry/test_api_bases.py`

**Interfaces:**
- `POST /projects/{p}/branches/{b}/bases` (maintainer) body `{"manifest": {...}, "files": {path: hash}}`:
  - `manifest` validates as `kb.packs.model.Manifest`, `manifest.project == p`, `manifest.branch == b` (400 otherwise);
  - every path passes `is_allowed_path`, `manifest.json` must NOT be in `files` (server stores manifest in the row), every hash exists as a Blob (400 listing missing);
  - duplicate commit set → 409 per Global Constraints;
  - creates `BaseRow(version = max+1, parent = max or None)`; returns 201 `{"version", "parent", "base_id"}`. Version assignment in the same transaction; on IntegrityError race → retry once.

- [ ] **Step 1: Failing tests:** `test_first_push_is_version_1_then_2_with_parent`; `test_duplicate_commit_set_409_mentions_pull`; `test_unknown_blob_400`; `test_disallowed_path_400`; `test_contributor_cannot_push_403`; `test_manifest_project_mismatch_400`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): push base with versioning`

---

### Task 7: Overlay publish and latest

**Files:** Modify `src/kb_registry/api/packs.py`; create `tests/registry/test_api_overlay.py`

**Interfaces:**
- `POST /projects/{p}/branches/{b}/overlay` (contributor) body `{"items": [{"id", "kind", "payload"}]}` → `{"added": [...], "existing": [...]}`; 404 `"no analysed base for <p>@<b> — push the analysis first"` when the branch has no base; payload validated per kind: `qa` → `QAItem`, `flow` → `Flow`, `suggestion` → `Suggestion`, `module_note` → `{"module_id": str, "text": str}`; stored payload has `visibility` forced to `"published"`; limits and id-conflict rule per Global Constraints. Item ids: `qa`/`flow`/`suggestion` use the object's own `id`; `module_note` ids are `note:<module_id>:<sha256(text)[:12]>`.
- `GET /projects/{p}/branches/{b}/latest` (viewer) → `{"version", "parent", "base_id", "manifest", "files": {path: hash}, "overlay": [{"id", "kind", "payload", "author", "published_at"}]}` — overlay = all items for the branch (all versions), ordered by `published_at`.

- [ ] **Step 1: Failing tests:** `test_publish_then_latest_contains_items_marked_published`; `test_republish_same_payload_is_existing`; `test_same_id_different_payload_409`; `test_publish_without_base_404`; `test_payload_validation_400`; `test_viewer_cannot_publish_403`; `test_item_and_count_limits_413` (oversize payload → 413).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(registry): overlay publish and latest`

---

### Task 8: Client, credentials, `kb login --token`, `kb list`

**Files:** Create `src/kb/sync/{__init__,client,credentials}.py`, `tests/sync/conftest.py`, `tests/sync/test_client.py`; modify `src/kb/cli.py`, `src/kb/errors.py`

**Interfaces:**
- `errors.py`: `RegistryError(KbError)` with `.status: int | None`; `RegistryUnavailable(RegistryError)`.
- `credentials.py`: `load_credentials(kb_home) -> dict`, `save_token(kb_home, registry_url, token: str, kind: Literal["static","oidc"], refresh_token: str | None = None, expires_at: float | None = None) -> None` (file mode 0600), `get_token(kb_home, registry_url) -> dict | None`.
- `client.py`: `RegistryClient(base_url: str, token: str | None, http: httpx.Client | None = None, on_refresh=None)` (tests pass a FastAPI `TestClient`, which is an `httpx.Client`); methods `auth_config() -> dict`, `me() -> dict`, `projects() -> list`, `project(p) -> dict`, `put_config(p, yaml) -> None`, `branches(p) -> list`, `versions(p, b) -> list`, `latest(p, b) -> dict`, `missing_blobs(hashes) -> list[str]`, `put_blob(hash, raw: bytes) -> None` (compresses), `get_blob(hash) -> bytes` (decompresses + verifies sha256), `create_base(p, b, manifest: dict, files: dict) -> dict`, `publish(p, b, items: list[dict]) -> dict`. Error mapping: connection/timeout → `RegistryUnavailable("registry unreachable: <url>")`; 401 → `RegistryError("not logged in to <url>: run kb login <url>")`; other 4xx/5xx → `RegistryError(detail)` with status.
- `client_for(cfg_or_url, kb_home) -> RegistryClient` — resolves URL (kb.yaml `registry:` or explicit), token from credentials.
- CLI: `kb login URL --token T` (verifies with `/me`, saves, prints "logged in to URL as EMAIL"); `kb list [CONFIG] [--registry URL]` → projects, or branches of `cfg.project` with version, commits (12 chars), author, overlay count.
- Test fixture (`tests/sync/conftest.py`): `registry` — in-process app on sqlite + FileBlobStore with project `mini` and users `maint@x` (maintainer), `contrib@x` (contributor), `view@x` (viewer), each with a token; `client_for(email) -> RegistryClient(base_url="http://testserver", token=…, http=TestClient(app))`. CLI tests monkeypatch `kb.sync.client.client_for` to return these.

- [ ] **Step 1: Failing tests:** `test_login_saves_token_with_0600`; `test_unreachable_raises_registry_unavailable` (base_url `http://127.0.0.1:9`); `test_401_message_says_kb_login`; `test_blob_roundtrip_through_client`; `test_cli_list_shows_projects`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(sync): registry client, credentials, kb login/list`

---

### Task 9: `kb push`

**Files:** Create `src/kb/sync/pack_io.py`, `src/kb/sync/commands.py`, `tests/sync/test_push.py`; modify `src/kb/cli.py`, `src/kb/packs/store.py`

**Interfaces:**
- `pack_io.base_files(pack_dir: Path) -> dict[str, bytes]` — every allowed base path present (not `manifest.json`, not `index.sqlite`).
- `commands.push_pack(cfg, branch, store, client) -> dict` — loads manifest; refuses `status != "complete"` with `KbError("pack is incomplete; run kb analyze --resume before pushing")`; hashes files; `missing_blobs` → `put_blob` only missing; `create_base`; on 409 raises `KbError("<p>@<b> at these commits is already on the registry (version N) — run kb pull instead")`; on success `store.update_manifest(version=…, parent=…)`; flushes the outbox (Task 11) first if one exists; seeds the registry config on first push (Step 3).
- CLI: `kb push CONFIG --branch B` → prints `pushed sop@main as version 3 (12 files, 4 uploaded)`.

- [ ] **Step 1: Failing tests:** `test_push_creates_version_and_updates_local_manifest`; `test_second_push_same_commits_says_pull`; `test_push_incomplete_pack_refused`; `test_repush_after_partial_upload_sends_only_missing` (pre-upload 2 of the blobs, count `put_blob` calls); `test_push_as_viewer_explains_role` (CLI exit 1, output contains "your role on mini is viewer; this needs maintainer"); `test_first_push_seeds_registry_config_without_local_paths`.
- [ ] **Step 2:** FAIL.
- [ ] **Step 3:** Implement. Config seeding: only when the registry's `config_yaml` is empty, upload the local kb.yaml re-dumped with every `local_path` key removed (machine-specific) and the first line `# repos without url: add local_path in your own copy`. Because such repos have no source, add `?seed=1` to `PUT /projects/{p}/config` (maintainer): it checks only that the YAML is a mapping whose `project` equals `{p}`; without `seed` the Task 4 full validation applies.
- [ ] **Step 4:** PASS. **Step 5: Commit** `feat(sync): kb push`

---

### Task 10: `kb pull`

**Files:** Modify `src/kb/sync/pack_io.py`, `src/kb/sync/commands.py`, `src/kb/packs/store.py`, `src/kb/cli.py`; create `tests/sync/test_pull.py`

**Interfaces:**
- `PackStore.write_pulled_base(project, branch, manifest: Manifest, files: dict[str, bytes]) -> Path` — validates every path with `kb_registry.paths.is_allowed_path` **and** that the resolved target is inside `pack_dir/base` (else `KbError("unsafe path from registry: …")`, nothing written); replaces `base/` atomically (write to `base.tmp`, then swap); writes manifest; keeps `overlay/`.
- `pack_io.apply_overlay(store, project, branch, items: list[dict]) -> int` — writes published items (qa → `save_qa`, flow → `save_flow`, suggestion → `add_suggestions`, module_note → newest `published_at` per module wins via `save_module_note`); never overwrites a local item with the same id whose local visibility is `private` and payload differs (local wins, a warning is returned); returns count written.
- `commands.pull_pack(project, branch, store, client, cfg: ProjectConfig | None) -> dict` — `latest`; download only blobs not in the local blob cache `$KB_HOME/blobs/<hash>` (raw, verified); `write_pulled_base`; `apply_overlay`; saves registry config to `$KB_HOME/projects/<project>/kb.yaml` when non-empty; if `cfg` given, marks stale via `kb.ask.available_checkouts` + `kb.analyze._mark_stale`; rebuilds index; flushes outbox first.
- CLI: `kb pull [CONFIG] --branch B [--project P --registry URL]` (either CONFIG or both flags) → `pulled sop@main version 3: 60 modules, 14 shared answers (2 stale)`.

- [ ] **Step 1: Failing tests:** `test_pull_materialises_pack_and_index` (push as maint from KB_HOME A, pull as viewer into KB_HOME B, `PackStore(B).load` works and `search` finds a module); `test_pull_keeps_private_items`; `test_pull_rejects_unsafe_paths` (registry mocked to return `{"../../evil": h}` and `/abs` → KbError, no file outside pack dir); `test_pull_uses_blob_cache` (second pull: zero `get_blob` calls); `test_pull_saves_registry_config`; `test_pull_marks_stale_with_local_checkout`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(sync): kb pull`

---

### Task 11: `kb publish`, outbox, `kb sync`, memory list

**Files:** Create `src/kb/sync/outbox.py`; modify `src/kb/sync/pack_io.py`, `src/kb/sync/commands.py`, `src/kb/cli.py`; create `tests/sync/test_publish.py`

**Interfaces:**
- `pack_io.overlay_items_for_publish(pack, qa_ids: list[str], include_notes: bool) -> list[dict]` — each QA + its flow + suggestions with that `source_qa_id`; with `include_notes`, every local module note as a `module_note` item; unknown id → `KbError`.
- `Outbox(kb_home)`: `add(project, branch, items)`, `pending(project, branch) -> list[dict]`, `clear(project, branch, ids)`; file `$KB_HOME/outbox/<project>@<branch with / → __>.json`.
- `commands.publish_items(cfg, branch, store, client, qa_ids, include_notes) -> dict` — on `RegistryUnavailable` → outbox add, return `{"queued": n}`; on success mark local QA/flow `visibility="published"`; returns `{"added", "existing"}`.
- `commands.flush_outbox(project, branch, client, store) -> int` (called at the start of push/pull/publish/sync; leaves items queued if still unavailable).
- CLI: `kb publish CONFIG --branch B QA_ID... [--notes]`; `kb sync CONFIG --branch B` (flush only); `kb memory list` gains a `queued` marker for ids in the outbox.

- [ ] **Step 1: Failing tests:** `test_publish_sends_qa_flow_and_suggestions_and_marks_published`; `test_publish_offline_queues_then_sync_sends` (client http raising `httpx.ConnectError`, then real); `test_publish_as_viewer_explains_role`; `test_publish_unknown_id`; `test_memory_list_shows_queued`; `test_notes_flag_publishes_module_notes`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat(sync): kb publish, outbox and sync`

---

### Task 12: OIDC (server validation + device-code login)

**Files:** Modify `src/kb_registry/auth.py`, `src/kb_registry/api/projects.py` (auth/config); create `src/kb/sync/oidc.py`, `tests/registry/test_oidc.py`, `tests/sync/test_oidc_login.py`; modify `src/kb/cli.py`, `src/kb/sync/client.py`

**Interfaces:**
- Server: when `oidc_issuer` is set, a Bearer value with two dots is validated as a JWT: JWKS from the issuer's `/.well-known/openid-configuration` `jwks_uri` (cached `PyJWKClient`), `RS256/ES256`, `iss == issuer`, `aud == oidc_audience`, `exp`; claims `sub` and `email` required. User found by `oidc_sub`, else by `email` (then `oidc_sub` stored), else auto-created (no memberships, not admin). Invalid JWT → 401.
- Client `oidc.device_login(registry_url, http: httpx.Client, sleep=time.sleep, out=print) -> dict` — `GET /api/v1/auth/config` (oidc null → `KbError("this registry uses API tokens: kb login <url> --token <token>")`); discovery; POST `device_authorization_endpoint` (`client_id`, `scope="openid email offline_access"`, `audience`); prints `verification_uri_complete` (or uri + `user_code`); polls `token_endpoint` with `grant_type=urn:ietf:params:oauth:grant-type:device_code` every `interval` s, `slow_down` adds 5 s, `expired_token`/`access_denied` → `KbError`; returns `{"token": access_token if it is a JWT else id_token, "refresh_token", "expires_at"}`.
- `oidc.refresh(registry_url, refresh_token, http) -> dict` — `grant_type=refresh_token`; `RegistryClient` calls it (via `on_refresh`) when the saved token expires within 60 s, saving the new token.
- CLI: `kb login URL` without `--token` runs the device flow.

- [ ] **Step 1: Failing tests:** server — `test_valid_jwt_authenticates_and_autocreates_user` (RSA key generated in test, JWKS served by monkeypatched `PyJWKClient.get_signing_key_from_jwt`), `test_wrong_audience_401`, `test_expired_401`, `test_auth_config_exposes_issuer`; client — `test_device_flow_polls_until_token` (httpx `MockTransport` IdP: pending, slow_down, then token; sleeps recorded `[5, 10]` for interval 5), `test_device_flow_denied`, `test_token_registry_says_use_token`, `test_client_refreshes_expiring_token`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: OIDC device-code login and JWT validation`

---

### Task 13: On-prem deployment

**Files:** Create `deploy/registry/Dockerfile`, `deploy/registry/docker-compose.yml`, `deploy/registry/.env.example`, `deploy/registry/README.md`, `tests/registry/test_deploy_files.py`

**Interfaces:**
- Dockerfile: `python:3.12-slim`, installs uv, `uv sync --extra registry --frozen --no-dev`, runs as non-root user `kb`, `CMD ["sh", "-c", "kb-registry migrate && kb-registry serve --host 0.0.0.0 --port 8080"]`, `HEALTHCHECK` on `GET /api/v1/auth/config`.
- Compose services: `postgres` (`postgres:16`, volume `pgdata`, healthcheck `pg_isready`), `minio` (`minio/minio`, `server /data --console-address :9001`, volume `minio`), `minio-init` (`minio/mc`, creates bucket `kb-packs`, exits), `registry` (build `../..` with this Dockerfile, depends on healthy postgres and completed minio-init, port `8080:8080`, env from `.env`: `KB_REGISTRY_DATABASE_URL=postgresql+psycopg://kb:${POSTGRES_PASSWORD}@postgres:5432/kb`, `KB_REGISTRY_STORAGE=s3://kb-packs`, `KB_REGISTRY_S3_ENDPOINT=http://minio:9000`, AWS keys = MinIO root user/password, OIDC vars commented).
- Customer's own S3/GCS: README section "Use your cloud storage" — set `KB_REGISTRY_STORAGE`/endpoint/keys and drop the minio services (GCS via its S3-interoperability endpoint `https://storage.googleapis.com`).
- README: start, first admin (`docker compose exec registry kb-registry admin create-user you@corp --admin`), create project, grant, `kb login`, backups (pg_dump + bucket), upgrade.

- [ ] **Step 1: Failing test:** `test_compose_is_wired` — loads compose YAML: services `postgres, minio, minio-init, registry`; registry env keys include the five storage/db keys; registry `depends_on.postgres.condition == "service_healthy"`; Dockerfile contains `kb-registry migrate`, `USER kb`, `HEALTHCHECK`.
- [ ] **Step 2–4:** FAIL → write files → PASS. If Docker Hub is reachable, also `docker compose -f deploy/registry/docker-compose.yml config` → exit 0 (otherwise record a ledger note).
- [ ] **Step 5: Commit** `feat(deploy): on-prem registry compose`

---

### Task 14: End-to-end sharing test

**Files:** Create `tests/sync/test_e2e.py`

**Interfaces:** Consumes everything above; stage 1 `run_analyze` with `FakeProvider` on the mini fixtures (graphify required → skip if absent).

- [ ] **Step 1: Write test `test_three_users_share_one_branch`:**
  1. Maintainer (KB_HOME A): `run_analyze` → `push_pack` → version 1.
  2. Contributor (KB_HOME B): `pull_pack` → `run_ask` (FakeProvider trace + learn) → `publish_items` with the saved QA id.
  3. Viewer (KB_HOME C): `pull_pack` → pack has the module map and B's QA with `visibility == "published"`; `run_ask` on the same question reuses B's flow with **zero** provider calls.
  4. Maintainer re-analyses after editing a cited fixture file → `push_pack` → version 2 (parent 1); viewer pulls → B's QA is marked `stale`.
- [ ] **Step 2:** Run → it should pass if Tasks 1–11 are right; any failure is a defect in an earlier task: fix there with a failing unit test first.
- [ ] **Step 3: Commit** `test: end-to-end sharing across three users`

---

### Task 15: Acceptance with the real stack (user's Mac)

**Files:** Create `docs/registry-acceptance.md` (results recorded there)

- [ ] **Step 1:** On the Mac with Docker Desktop: `cd deploy/registry && cp .env.example .env && docker compose up -d --build`; `curl localhost:8080/api/v1/auth/config` → `{"oidc":null}`.
- [ ] **Step 2:** `docker compose exec registry kb-registry admin create-user <you> --admin` (save token), `create-project sop`, create a second user (viewer) and grant.
- [ ] **Step 3:** `kb login http://localhost:8080 --token …`; `kb analyze` (or reuse the stage-1 pack copied into `KB_HOME`) → `kb push`; with a second `KB_HOME` and the viewer token: `kb pull`, `kb ask "How does scheduling work in Tasks?"` (reuses the shared flow if one was published), `kb publish` as contributor, pull again from the first home.
- [ ] **Step 4:** `docker compose restart registry` and repeat `kb pull` → same version (state survives in Postgres + MinIO).
- [ ] **Step 5:** Record outputs and timings in `docs/registry-acceptance.md`; commit `docs: registry acceptance run`.
