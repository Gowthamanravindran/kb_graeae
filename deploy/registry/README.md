# kb-registry on-prem

The registry stores analysed branches (packs) and shared answers for your team. It never runs an
LLM and never sees source code. This folder runs it with Postgres 16 and MinIO on one machine.

## Start

```bash
cd deploy/registry
cp .env.example .env          # set the passwords
docker compose up -d --build
curl http://localhost:8080/api/v1/auth/config   # → {"oidc":null}
```

## First admin, project and people

```bash
docker compose exec registry kb-registry admin create-user you@corp --admin   # prints your token once
docker compose exec registry kb-registry admin create-project sop
docker compose exec registry kb-registry admin create-user dev@corp            # prints their token
docker compose exec registry kb-registry admin grant dev@corp sop contributor  # viewer|contributor|maintainer
docker compose exec registry kb-registry admin token dev@corp --name laptop    # extra token
```

Roles: **viewer** pulls and lists; **contributor** also publishes answers; **maintainer** also pushes
analyses and sets the project config.

## Use it

```bash
kb login http://localhost:8080 --token kbr_...
# kb.yaml: registry: http://localhost:8080
kb push kb.yaml --branch main          # maintainer, after kb analyze
kb pull kb.yaml --branch main          # anyone with access
kb publish kb.yaml --branch main <answer-id>
```

## Use your own cloud storage instead of MinIO

Set these on the `registry` service and remove `minio` / `minio-init`:

| Storage | KB_REGISTRY_STORAGE | KB_REGISTRY_S3_ENDPOINT | Keys |
|---|---|---|---|
| AWS S3 | `s3://your-bucket` | (empty) | `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_DEFAULT_REGION` |
| Google Cloud Storage | `s3://your-bucket` | `https://storage.googleapis.com` | HMAC key pair as the AWS keys |
| Any S3-compatible | `s3://your-bucket` | its endpoint URL | its access keys |

Use your own Postgres the same way: set `KB_REGISTRY_DATABASE_URL=postgresql+psycopg://user:pass@host:5432/db`
and remove the `postgres` service.

## Single sign-on (OIDC)

Register a public client that allows the **device authorization grant**, then set
`KB_REGISTRY_OIDC_ISSUER` (e.g. `https://login.corp.com/realms/eng`), `KB_REGISTRY_OIDC_CLIENT_ID`
and `KB_REGISTRY_OIDC_AUDIENCE` (the `aud` your tokens carry). Users run `kb login <url>` and confirm
a code in the browser. Their first login creates their account with no access; an admin grants roles.
API tokens keep working alongside SSO.

## TLS

API tokens travel in every request, so never expose port 8080 beyond the machine without TLS. Put
the registry behind a reverse proxy that terminates HTTPS (nginx, Caddy, Traefik, or your cloud
load balancer), publish only the proxy, and point clients at `https://…`. Remove the `8080:8080`
port mapping (or bind it to `127.0.0.1:8080:8080`) once the proxy is in place. The proxy is also
where to add request-rate limits; the registry itself caps request bodies (8 MB JSON, ~210 MB
blob uploads).

## Removing access

```bash
docker compose exec registry kb-registry admin revoke dev@corp sop     # drop one project role
docker compose exec registry kb-registry admin revoke-tokens dev@corp  # kill all their API tokens
docker compose exec registry kb-registry admin disable-user dev@corp   # leaver: tokens, roles, SSO all blocked
```

## Backups and upgrades

- Back up both the database (`docker compose exec postgres pg_dump -U kb kb > kb.sql`) and the
  bucket (`mc mirror local/kb-packs ./backup`). Blobs are immutable, so incremental copies are safe.
- Upgrade: `git pull && docker compose up -d --build`. The registry runs its migrations on start.
