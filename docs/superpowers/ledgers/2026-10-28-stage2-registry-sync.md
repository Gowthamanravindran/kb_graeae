# SDD ledger — plan: docs/superpowers/plans/2026-10-28-stage2-registry-sync.md
Spec: docs/superpowers/specs/2026-10-28-codebase-knowledge-system-design.md (read). Branch: phase1 from 2b98b52. Commit only, never push (user instruction).
Pre-flight:
- T6/T7 consume T1 models + T3 require_role + T2 store: consistent.
- T8 client methods map 1:1 to T4–T7 endpoints: consistent.
- T9/T10 use kb_registry.paths.is_allowed_path (T1) client-side: paths.py must stay stdlib-only (Global Constraint) — consistent.
- T9 adds ?seed=1 to T4 PUT config: T4 must accept the query param later — noted.
- T11 flush_outbox is called by T9/T10 push/pull: T9/T10 call it only if outbox exists; implemented as a no-op stub until T11 — ruling below.
- T14 consumes stage-1 run_analyze/run_ask/FakeProvider: consistent.
Pre-flight Ruling: commands.flush_outbox is created in T9 as a no-op returning 0 and filled in T11 — T9/T10 need to call it before T11 exists — cost if wrong: none.
Task 1: Ruling: registry deps are also in the dev group so plain `uv run pytest` imports kb_registry; pyproject declares `module-name = ["kb", "kb_registry"]` for uv_build — cost if wrong: none.
Task 1: Ruling: added `kb_registry/migrate.py: upgrade(database_url)` (programmatic alembic) used by tests and `kb-registry migrate`; SQLite gets PRAGMA foreign_keys=ON — cost if wrong: none.
Task 1: complete (commits 2b98b52..8f7e6ba, tests: uv run pytest -q → 150 passed in 15.78s)
Task 2: complete (commits 8f7e6ba..16ba7c8, tests: uv run pytest -q → 157 passed in 12.44s)
Task 3: complete (commits 16ba7c8..3490438, tests: uv run pytest -q → 163 passed in 13.19s)
Task 4: Ruling: PUT /projects/{p}/config accepts ?seed=1 already (plan put it in Task 9; trivial here, tested in Task 9) — cost if wrong: none.
Task 4: complete (commits 3490438..8d98c71, tests: uv run pytest -q → 169 passed in 14.28s)
Task 5: complete (commits 8d98c71..ade62e0, tests: uv run pytest -q → 173 passed in 14.73s)
Task 6: Ruling: commits_key moved to kb.packs.model.commits_key (kb.analyze._commits_key now delegates) — the registry must not import the analysis stack (graphify/langgraph) — cost if wrong: none.
Task 6: Ruling: stored manifest gets the registry-assigned version/parent; bad manifest JSON → 400 (not FastAPI's 422) — cost if wrong: none.
Task 6: complete (commits ade62e0..ccec716, tests: uv run pytest -q → 180 passed in 16.37s)
Task 7: Ruling: a publish request with any id conflict is rejected whole (409, nothing written) rather than partially applied — cost if wrong: client must drop the conflicting id and retry.
Task 7: complete (commits ccec716..6a71ea6, tests: uv run pytest -q → 188 passed in 16.82s)
Task 8: Ruling: RegistryClient takes `http: httpx.Client` (plan text already corrected to this); `make_http(url)` is the one seam tests patch for login; `client_for(cfg=None, registry=None, kb_home=None)` resolves kb.yaml registry or --registry; RegistryError carries `.status` and `.body` (409 version needed by push) — cost if wrong: none.
Task 8: complete (commits 6a71ea6..0a58db8, tests: uv run pytest -q → 197 passed in 16.28s)
Task 9: Ruling: push checks the caller's role via /me first so a viewer sees 'your role on mini is viewer; this needs maintainer' (blob endpoints alone would say 'uploading needs the maintainer role on a project'); config seeding re-dumps the ProjectConfig (no kb.yaml path needed); added test_client_needs_no_server_dependencies to guard the stdlib-only import rule — cost if wrong: one extra /me call per push.
Task 9: complete (commits 0a58db8..98add5e, tests: uv run pytest -q → 206 passed in 18.88s)
Task 10: Ruling: apply_overlay returns (written, warnings) instead of int — the plan requires reporting kept-private conflicts; unsafe-path check runs on the file map before any download (check_unsafe_paths in kb.packs.store, also used by write_pulled_base); a pulled QA keeps its local `stale` flag until _mark_stale re-checks — cost if wrong: none.
Task 10: Ruling: two test-only fixes (header comment contains "local_path"; stale test needs local checkouts for both repos) — the code was right — cost if wrong: none.
Task 10: complete (commits 98add5e..e416f05, tests: uv run pytest -q → 216 passed in 20.94s)
Task 11: Ruling: memory list shows 'queued' in place of the visibility column for outbox items; publish with no ids and no --notes is an error ('nothing to publish'); a role check failure while offline also queues (the /me call is the first network call) — cost if wrong: a viewer offline sees 'queued' and gets the role error on the next sync.
Task 11: complete (commits e416f05..fad150b, tests: uv run pytest -q → 224 passed in 23.91s)
Task 12: Ruling: server JWKS lookup is one seam, kb_registry.auth.signing_key(app, token) (tests patch it; real path discovers jwks_uri once and caches a PyJWKClient on the app); any JWT failure, including an unreachable IdP, is a 401; the device flow sleeps one interval before each poll (RFC 8628) and prefers a JWT access_token, else id_token — cost if wrong: IdPs issuing opaque access tokens rely on id_token audience = client id, which must then equal KB_REGISTRY_OIDC_AUDIENCE.
Task 12: complete (commits fad150b..892524a, tests: uv run pytest -q → 236 passed in 23.64s)
Task 13: Ruling: 'docker compose config' validated here (exit 0); image build and running the stack not possible in this workspace (Docker Hub blocked) — deferred to Task 15 on the user's Mac. minio-init waits for MinIO in a retry loop instead of a healthcheck (minio image has no curl) — cost if wrong: first start may log a few retries.
Task 13: complete (commits 892524a..1bd6125, tests: uv run pytest -q → 239 passed in 25.49s)
Task 14: Ruling: E2E exposed a stage-1 defect — answer reuse required the saved flow's FTS score ≥ 0.8× the best hit of ANY kind, so an identical question was not reused when a module summary outranked the flow (3.56 vs 5.02). Fixed in kb.agent.nodes: reuse candidates come from a flow/qa-only search; decision = question overlap ≥ 0.6 + shared module + fresh (REUSE_SCORE removed). test_reuse_when_module_summary_outranks_the_saved_flow RED→GREEN — cost if wrong: two differently-worded questions with ≥60% term overlap in the same module share an answer (unchanged from before).
Task 14: complete (commits 1bd6125..8ba7562, tests: uv run pytest -q → 241 passed in 26.65s)
Task 15: Ruling: acceptance run in the cloud workspace on the real stack minus Docker (registry over HTTP + Postgres 16 + moto S3 server + real SOP pack); the docker compose run is left as a checklist for the user's Mac (Run 2 in docs/registry-acceptance.md) because neither this workspace (Docker Hub blocked) nor the Mac-side shell (no Docker) can run it — cost if wrong: compose/Dockerfile bugs surface on first real start.
Task 15: Ruling: test Postgres cluster now initdb -E UTF8 --no-locale (SQL_ASCII made psycopg return bytes); the script is kept as scripts/registry_acceptance.sh — cost if wrong: none.
Task 15: complete (commits 8ba7562..a92ef2d, tests: uv run pytest -q → 241 passed in 26.50s)
Final review: fresh reviewer (opus) — "Not ready, needs fixes": 2 Critical, 5 Important, 11 Minor.
Final: re-graded Minor -> Important: OIDC issuer without audience accepts tokens minted for any other app at that IdP; no TLS guidance while tokens cross plain HTTP on 8080.
Final: fixed C1 overlay ids allowed path traversal / absolute writes on pullers (server rejects, client skips, store refuses) — test_is_safe_id, test_publish_rejects_unsafe_ids, test_apply_overlay_skips_unsafe_ids, test_store_refuses_ids_that_escape RED→GREEN
Final: fixed C2 OIDC email linking allowed account takeover (now: verified email only, never re-link a linked account, case-insensitive) — test_unverified_email_cannot_take_over_existing_user, test_already_linked_user_is_not_relinked, test_verified_email_links_unlinked_user_case_insensitively RED→GREEN; test_existing_user_matched_by_email_gets_sub updated to send email_verified=True (intended behaviour change)
Final: fixed I3 one rejected queued publish blocked pull/push forever: 4xx-rejected items move to <outbox>.rejected.json with the reason; queue flushed in chunks of 500 — test_rejected_queued_publish_does_not_block_pull RED→GREEN
Final: fixed I4 unbounded bodies parsed before auth: BodyLimit ASGI middleware (8 MB JSON, ~210 MB blob PUT; Content-Length and chunked) — test_oversized_json_rejected_before_auth, test_oversized_chunked_body_rejected RED→GREEN
Final: fixed I5 blob upload blocked the event loop: verify/store/DB in run_in_threadpool; concurrent same-blob upload no longer 500s — test_blob_upload_does_not_block_other_requests RED→GREEN (test first corrected: it timed from after the blocked loop freed, so it passed against the bug)
Final: fixed I6 no way to revoke: admin revoke / revoke-tokens / disable-user, users.disabled (migration 0002) honoured for tokens and OIDC — test_admin_can_revoke_access, test_disabled_user_cannot_use_oidc RED→GREEN
Final: fixed I7 pull silently replaced an unpushed local analysis: Manifest.synced; pull refuses unless --force when local commits differ and were never pushed/pulled — test_pull_will_not_silently_replace_an_unpushed_analysis, test_pull_after_push_or_pull_needs_no_force RED→GREEN
Final: fixed (re-graded) issuer without audience: create_app refuses to start — test_issuer_without_audience_refuses_to_start RED→GREEN; README TLS + removing-access sections — test_readme_covers_tls RED→GREEN
Final: incident: the RED run of test_apply_overlay_skips_unsafe_ids (before the C1 fix) wrote /abs/evil.json in the workspace — the bug demonstrating itself. User approved deleting it; deleted. Tests now use absolute paths inside tmp_path only.
Final: suite 268/268 green.
Final: Ruling: (declined) LLM prose quoting code — stage-1 strip_code + prompt rule already cover overlay text; registry stores it as given — cost if wrong: short code fragments in shared answers.
Final: Ruling: (declined) maintainer sets a hostile config_yaml — pull only saves it to $KB_HOME/projects/<p>/kb.yaml and prints the path; nothing runs it until the user chooses to use it; maintainers are trusted — cost if wrong: a viewer who blindly uses it could point kb at local folders (data still stays local/with their own Claude).
Final: Ruling: (declined) Dockerfile/compose actually running — compose validated with `docker compose config`; real run is Run 2 on the user's Mac — cost if wrong: first start fails.
Final: Ruling: (declined) spec §7 "maintainer removes items" — not in the stage-2 plan; deferred to a later stage — cost if wrong: bad shared answers need SQL to remove until then.
Final: Ruling: (declined) static token expiry/rotation — revocation added; expiry deferred — cost if wrong: a leaked token works until revoked.
Final: Ruling: (declined) hostile registry zstd frame declaring a huge size — client decompress uses max_output_size=200 MB, so it fails rather than allocating — cost if wrong: client memory spike.
Final: Ruling: (declined) multi-org hosted mode — stage 4 — cost if wrong: none now.
Final: minor (deferred): concurrent publish of the same overlay id on Postgres can 500 (IntegrityError); branch > 255 / id > 200 / path > 300 chars 500 on Postgres (SQLite tests don't enforce lengths)
Final: minor (deferred): lost push race returns 409 without version -> client says "version None — run kb pull"
Final: minor (deferred): signing_key caches neither failed discovery nor unknown kids (IdP request per bad token)
Final: minor (deferred): tracebacks instead of KbError for kb login to an unreachable URL (device flow), ZstdError in get_blob, pydantic errors in apply_overlay
Final: minor (deferred): hashes from latest not format-checked before use as cache file names (safe today: get_blob verifies before writing)
Final: minor (deferred): /blobs/missing lets a maintainer of any project probe whether a hash exists; create_base can reference any known blob
Final: minor (deferred): duplicate ids in one publish request silently dropped even with different payloads
Final: minor (deferred): latest loads every user row on each pull
Final: minor (deferred): reuse skips the shared-module check when the question matched no module
