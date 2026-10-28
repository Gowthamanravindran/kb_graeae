# Registry acceptance

## Run 1: cloud workspace, 2026-10-28

The real stack was used throughout. kb-registry ran under uvicorn over HTTP, with a real Postgres 16 server and S3 storage through moto's S3 server in place of MinIO. The data was the real SOP pack from stage 1: 60 modules, about 6,700 files. Docker Hub is blocked in that workspace, so the `docker compose` stack itself still needs Run 2.

| Step | Result |
|---|---|
| `kb-registry migrate` on Postgres 16 (UTF-8) | database is up to date |
| `GET /api/v1/auth/config` | `{"oidc":null}` |
| admin: create admin user, project `sop`, viewer `carol` | ok |
| `kb login --token` (admin) | logged in as gowthaman@local |
| `kb push` SOP pack | **version 1 (5 files, 5 uploaded) in 4.7 s** |
| `kb push` again, same commits | refused: "already on the registry (version 1) — run kb pull instead" |
| `kb publish <scheduling answer>` | published 7 new (answer + flow + 5 suggestions) |
| `kb list` | `main  v1  7 shared  gowthaman@local  sop-web=local-2512f6, sop-backend=local-dc3dcd` |
| viewer on a fresh `KB_HOME`: `kb pull --project sop --registry … --branch main` (no kb.yaml) | 60 modules, 1 shared answer, config saved; **5.7 s** |
| viewer: `kb ask "How does scheduling work in Tasks?"` | **reused the shared flow, no Claude call** |
| viewer: `kb publish` | refused: "your role on sop is viewer; this needs contributor" |
| restart the registry, then `kb pull` | same version 1 and the same shared answer: state survived |

Postgres needs a UTF-8 database. The `postgres:16` image is UTF-8 by default. A cluster created with `initdb` and no locale defaults to `SQL_ASCII`, which psycopg can't read, so use `-E UTF8`.

## Run 2: your Mac with Docker Desktop (to do)

1. Run `cd deploy/registry && cp .env.example .env`, set the passwords, then run `docker compose up -d --build`.
2. Check that `curl localhost:8080/api/v1/auth/config` returns `{"oidc":null}`.
3. Create the users:
   - Run `docker compose exec registry kb-registry admin create-user you@corp --admin` and keep the token it prints.
   - Run `docker compose exec registry kb-registry admin create-project sop`.
   - Create a second user and grant them `viewer`.
4. Log in and share:
   - Run `kb login http://localhost:8080 --token …`.
   - Add `registry: http://localhost:8080` to `kb.yaml`.
   - Run `kb push`, then `kb publish <answer-id>`.
5. Check from a second machine profile:
   - With another `KB_HOME` and the viewer's token, run `kb pull --project sop --registry http://localhost:8080 --branch main`.
   - Then run `kb ask` with the same question and expect "Reused saved flow".
6. Run `docker compose restart registry`, then `kb pull` again. Expect the same version.
7. Optionally, open the MinIO console at `localhost:9001` and look under `kb-packs/blobs/` for the content-addressed blobs.
