# Codebase Knowledge System — Design Spec

- **Date:** 2026-10-28
- **Status:** Draft — awaiting review
- **Working name:** `kb` (CLI), KB Desktop (app), `kb-registry` (server)

---

## 1. Purpose

Teams need to understand how a product works — e.g. *"How does scheduling work in Tasks?"* — across several repos (frontend, backend, mobile), without each person re-reading the code. This system:

1. **Explains a feature end to end**: the UI flow and the backend flow, each step citing real code.
2. **Shows the system in plain English**: a module map and diagrams whose labels are sentences, not class names.
3. **Turns that understanding into shareable memory**: an analysed branch is a "memory" that can be kept private, synced to a central store, pulled by others, and asked further questions with their own LLM. Useful answers can be published back so the memory grows.
4. **Works for any set of repos**, in any language or framework, via configuration.

### Users
- **Developers** — mostly via the CLI.
- **Non-developers** (PM, QA, support) — via the desktop app.

### Success criteria
- `kb analyze` on `sop-fe` + `sop-backend` produces a module map whose module names match the team's glossary (`CONTEXT.md`) and a plain-English system overview.
- `kb ask "How does scheduling work in Tasks?"` returns a UI flow and a backend flow (including the Bull queue work) where every step cites a valid `file:line`, plus a plain-English diagram.
- A second user can `kb pull` that analysed branch and ask follow-up questions without re-running analysis.
- Pointing `kb.yaml` at a different repo set (another language/framework) works without code changes, provided a linker plugin exists for how its repos talk.
- Every LLM run is visible as a Langfuse trace, including Claude Code's internal steps.

### Decisions recorded from brainstorming
| Decision | Choice |
|---|---|
| Audience | Both devs and non-devs; one app with the engine as its core |
| Clients | CLI + desktop app over the same core |
| Model | Claude Code (headless) for now; provider interface allows others later |
| Tracing | Langfuse, self-hosted in Docker (free; LangSmith self-hosting needs an enterprise licence) |
| What a "memory" is | An **analysed branch** (repo set @ branch @ commits) |
| Sharing | Shared space in the app; memories private by default, synced to the central store on demand |
| Central store flavours | (1) On-prem in the customer's own cloud, (2) hosted on our servers — same server image |
| Q&A feedback | Saved locally against the branch; user **chooses** which to publish back |
| Up-front analysis depth | Structure + LLM-generated module map; deeper flows created on demand and saved |
| Suggested scenarios | Queued as suggestions, never auto-run |

### Out of scope (v1)
- LLM providers other than Claude Code.
- Running any LLM on the registry server.
- Real-time collaboration / chat between users.
- Editing code; this system is read-only over repos.
- Linker plugins beyond `graphql`, `bull-queue`, `react-router`, `rest`, `kafka`, `sqs`, `rabbitmq` (the last four
  were added after v1; see docs/desktop.md).
- Hosted multi-tenant registry (stage 4; design accommodates it).

---

## 2. Architecture

```
┌────────────── User's machine ──────────────┐        ┌────── Central (on-prem or hosted) ──────┐
│                                            │        │                                          │
│  KB Desktop (Electron + React)             │        │  kb-registry (FastAPI)                   │
│        │  spawns & talks HTTP to           │        │     ├─ Postgres (index, users, roles)    │
│        ▼                                   │  push  │     └─ S3-compatible storage (packs)     │
│  kb serve ──┐                              │ ─────▶ │                                          │
│  kb CLI ────┤──▶ kb-core (Python)          │ ◀───── │  No LLM runs here.                       │
│             │     ├─ config                │  pull  └──────────────────────────────────────────┘
│             │     ├─ ingest (Graphify)     │
│             │     ├─ linkers (plugins)     │        ┌────── Optional, local Docker ───────────┐
│             │     ├─ module mapper  ───────┼──LLM──▶│  Claude Code (via Claude Agent SDK)     │
│             │     ├─ agent (LangGraph) ────┼────────┤                                          │
│             │     ├─ LLM provider iface    │ traces │  Langfuse                                │
│             │     └─ pack store (disk)     │ ─────▶ └──────────────────────────────────────────┘
│  Git checkouts of configured repos         │
└────────────────────────────────────────────┘
```

### Components

| Unit | Responsibility | Depends on |
|---|---|---|
| `kb-core.config` | Load/validate `kb.yaml` | — |
| `kb-core.ingest` | Checkout repos at branch; run Graphify (AST-only) per repo | git, Graphify |
| `kb-core.linkers` | Plugin registry; each plugin adds cross-repo or intra-repo edges | tree-sitter, graph |
| `kb-core.mapper` | Cluster graph into product modules; LLM names/summarises them; writes `system.md` | graph, LLM provider |
| `kb-core.agent` | LangGraph Q&A graph (Section 5) | graph, packs, LLM provider |
| `kb-core.llm` | `LLMProvider` interface; `ClaudeCodeProvider` implementation; emits trace spans | Claude Agent SDK, Langfuse |
| `kb-core.packs` | Read/write/version packs on disk; staleness checks | — |
| `kb-core.sync` | Registry client: push / publish / pull / list | registry API |
| `kb` CLI | User commands (Typer) | kb-core |
| `kb serve` | Localhost HTTP API for the desktop app (FastAPI) | kb-core |
| KB Desktop | UI (Electron + React); starts `kb serve` as a bundled binary | kb serve |
| `kb-registry` | Store and serve packs; auth; roles | Postgres, S3-compatible store |

Each unit is testable alone; linker plugins and LLM providers are the two extension points.

---

## 3. Configuration and pack format

### 3.1 `kb.yaml`
```yaml
project: sop
repos:
  - name: sop-web
    url: git@github.com:acme/sop-fe.git
    path: packages/sop-web        # optional sub-folder for monorepos
    role: frontend
  - name: sop-backend
    url: git@github.com:acme/sop-backend.git
    role: backend
linkers:
  - type: graphql
    from: sop-web
    to: sop-backend
  - type: bull-queue
    in: sop-backend
  - type: react-router
    in: sop-web
context_docs:
  - sop-backend:CONTEXT.md
  - sop-backend:docs/adr/**
ignore: ["**/*.svg", "**/__generated__/**"]
registry: https://kb.acme.internal    # or our hosted URL
langfuse: http://localhost:3000       # optional
```
Config lives with the project on the registry so pullers get it; local overrides allowed. A settings UI over this file comes later.

### 3.2 Pack = one analysed branch
```
sop@main/
  manifest.json      project, branch, {repo: commit}, version, parent version,
                     author, created_at, stats (files, unparsed, links, unresolved)
  base/              produced by `kb analyze`; immutable for a commit set
    graph/<repo>.json   Graphify graph, filtered (see 3.4)
    links.json          linker edges, incl. unresolved
    modules.json        modules: id, name, summary, member node ids, entry points
    system.md           plain-English overview + Mermaid diagram
  overlay/           grows with use
    qa/<id>.json        question, answer, cited nodes (+content hashes), author, visibility
    flows/<id>.json     UI and backend steps, citations, Mermaid diagram, visibility
    module_notes/<module>.json   proposed improvements to module summaries
    suggestions.json    queued related scenarios
  index.sqlite       FTS5 index over node names, module summaries, Q&As (rebuildable)
```

### 3.3 Versioning, staleness, visibility
- Every push creates a new immutable version. One canonical **base** per commit set; a second push of the same commit set is refused ("already exists — pull instead").
- On a new commit, `kb analyze` builds a new base and carries the overlay forward. Each overlay item stores content hashes of its cited code; changed hashes mark the item **stale** (kept, flagged, one-click re-trace).
- Overlay items are `private` or `published`. `kb publish <id>` uploads selected items. Items have unique ids and are append-only, so concurrent publishers do not conflict.

### 3.4 No source code leaves the machine
Packs contain paths, symbol names, line numbers, summaries and diagrams — never copied source. Graphify output is filtered to those fields before upload. Source is read only from the user's own checkout. This is required because the registry does not know each user's repo permissions.

---

## 4. Analysis pipeline — `kb analyze <project>@<branch>`

| Step | What | LLM? |
|---|---|---|
| 1. Checkout | Clone/fetch each repo at the branch; record commits | No |
| 2. Graph | Graphify AST-only per repo (files, components, functions, imports, calls). Graphify's own LLM extraction is disabled | No |
| 3. Link | Run configured linker plugins (below) | No |
| 4. Module map | Cluster by folder structure + graph connectivity; Claude names each cluster as a product module using `context_docs`, writes a 3–5 sentence summary with entry points; one final call writes `system.md` | Yes (≈20–40 calls for SOP) |
| 5. Write | Save pack locally as private | No |

### Linker plugins (v1)
- **`graphql`** — frontend operations (`gql` tags, `.graphql` files) ↔ backend resolver fields, matched by field name. e.g. `mutation AddTask` in `sop-web/.../create-task/task.graphql.ts` → `addTask` in `sop-backend/src/module/tasks/task/resolvers.ts`.
- **`bull-queue`** — `queue.add('<job>')` producers ↔ `process('<job>')` consumers (e.g. `src/queue/task/`).
- **`react-router`** — routes ↔ page components, so flows start at a user-visible screen.
- **`rest`**, **`kafka`**, **`sqs`**, **`rabbitmq`** (added later) — HTTP calls ↔ server routes, and message
  senders ↔ handlers through each broker. Details in docs/desktop.md.

Unmatched references are recorded as **unresolved** edges, never guessed. Plugin interface: `name`, `config schema`, `run(graphs, checkouts) -> list[Edge]`.

Step 4 saves progress per module; `kb analyze --resume` continues after failure. Pack is marked `incomplete` until step 4 finishes.

---

## 5. Question-answering agent (LangGraph)

```
understand ─▶ collect ─▶ trace ─▶ verify ─▶ render ─▶ learn
     │                                                    
     └── reuse hit (fresh flow already answers) ─▶ render
```

1. **Understand** — classify (how-it-works / where-is / impact / diagram); find candidate modules via FTS over module map, node names, prior Q&As. If a non-stale flow answers it, reuse it (no LLM call).
2. **Collect** — bounded graph walk from entry points along route → page → component → GraphQL op → resolver → service → model/queue → consumer. Output: shortlist of files + symbols.
3. **Trace** — Claude Code reads the shortlisted code at the pack's commits; returns structured UI and backend steps, each with `file:line`.
4. **Verify** — check every citation exists in graph and source; uncited or invalid steps are flagged "unverified".
5. **Render** — plain-English answer + Mermaid diagram with sentence labels.
6. **Learn** — save Q&A + flow to overlay (private); draft module-note improvements; queue 3–5 related scenarios as suggestions.

**Ask a memory:** opening a saved Q&A/flow seeds the agent's context with its citations and diagram.

**No source checkout** (e.g. user lacks repo access): browse everything in the pack; questions answered from summaries only, marked "no source verification".

---

## 6. Tracing (Langfuse)

- One trace per `kb analyze` / `kb ask`, tagged project, branch, commits, pack version.
- One span per LangGraph node (via Langfuse's LangChain/LangGraph callback handler).
- Claude Code's internal steps (Read, Grep, …) become child spans with tokens and timing, built from the Claude Agent SDK's per-step messages.
- Analysis steps 1–3 emit timing spans with counts (links resolved/unresolved, files unparsed).
- Desktop thumbs up/down → Langfuse score on the trace.
- A golden-question Langfuse dataset is re-run after prompt or linker changes.
- Tracing is off unless `langfuse` is configured.

---

## 7. Sync, auth, permissions

### Registry API
| Endpoint | Purpose |
|---|---|
| `GET /projects`, `/projects/{p}/branches`, `/…/versions` | Browse library |
| `POST /packs` | Push a new base (refused if commit set exists) |
| `POST /packs/{id}/overlay` | Publish selected overlay items |
| `GET /packs/{id}/manifest`, `GET /blobs/{hash}` | Pull |

Blobs are content-addressed; unchanged per-repo graphs are reused across versions.

### Roles
Organisation → projects. **viewer**: pull. **contributor**: + publish overlay items. **maintainer**: + push bases, remove items, manage project config.

### Auth
`kb login` uses OIDC device-code flow. On-prem: customer's IdP via OIDC, or static tokens for small teams. Hosted: SSO provider chosen at stage 4.

### Deployment flavours
- **On-prem:** Docker Compose (registry + Postgres + MinIO), customer storage swappable via S3-compatible endpoint; Helm chart later.
- **Hosted:** same image, multi-org, our storage.

---

## 8. Error handling

| Failure | Behaviour |
|---|---|
| Claude Code missing / not logged in | Clear message; browse-only mode |
| Claude call fails / rate limit | Retry with backoff; per-module checkpoint; `--resume` |
| File fails to parse | Skip, log, count in manifest |
| Linker can't match | Unresolved edge, shown in UI and Langfuse |
| Registry unreachable | Work locally; publishes queued for next sync |
| Cited code changed | Item marked stale; one-click re-trace |
| Push of existing commit set | Refused with "pull instead" |

---

## 9. Desktop app

Four views:
- **Library** — projects → branches → versions; freshness, coverage, local vs synced.
- **Map** — interactive module map (React Flow) + plain-English system overview.
- **Ask** — chat; answers with UI/backend flow steps, Mermaid diagrams, clickable citations.
- **Memory** — my private/published Q&As and flows, stale items, suggested scenarios; publish controls.

Electron main process starts the bundled `kb serve` binary and talks to it over localhost HTTP.

**UI mocks are to be approved before implementation planning.**

---

## 10. Tech stack (signed off 2026-10-28)

| # | Item | Choice |
|---|---|---|
| 1 | Core language | Python 3.12 |
| 2 | Packaging / envs | uv |
| 3 | Code graph | Graphify (AST-only mode) |
| 4 | Linker parsing | tree-sitter (py-tree-sitter) |
| 5 | Graph querying | NetworkX in memory (Kùzu if graphs outgrow memory) |
| 6 | Search | SQLite FTS5 (embeddings via sqlite-vec later) |
| 7 | Agent orchestration | LangGraph |
| 8 | Model | Claude Code via Claude Agent SDK (Python) |
| 9 | Tracing | Langfuse, self-hosted Docker Compose |
| 10 | CLI | Typer |
| 11 | Local API | FastAPI + Uvicorn |
| 12 | Desktop shell | **Electron** (packaged with electron-builder) |
| 13 | Engine packaging | PyInstaller binary bundled in the Electron app |
| 14 | UI | React + TypeScript + Vite |
| 15 | Components / styling | Tailwind + shadcn/ui |
| 16 | Flow diagrams | Mermaid |
| 17 | Interactive module map | React Flow |
| 18 | Registry API | FastAPI |
| 19 | Registry DB | Postgres 16, SQLAlchemy 2, Alembic |
| 20 | Pack storage | S3-compatible (boto3); MinIO for on-prem Docker; Azure adapter later |
| 21 | Auth | OIDC device-code; static tokens for small on-prem teams |
| 22 | On-prem deploy | Docker Compose; Helm later |
| 23 | Pack transfer | JSON + SQLite, `.tar.zst`, content-addressed |
| 24 | Python tests | pytest |
| 25 | Desktop tests | Vitest + Playwright |

---

## 11. Testing

- **Linkers:** fixture mini-repos with known expected edges per plugin.
- **Packs:** round-trip write/read, versioning, overlay carry-forward, staleness detection.
- **Filtering:** test that no source text survives into an uploadable pack.
- **Agent:** golden set of 10–15 SOP questions (Langfuse dataset), scored on citation validity and expected-file recall. Includes "How does scheduling work in Tasks?".
- **Registry:** API tests for roles, duplicate-base refusal, append-only publish.
- **Desktop:** Vitest component tests; Playwright for library → pull → ask → publish.

---

## 12. Build order

1. **Core + CLI** — `analyze`, `ask`, Langfuse tracing; proven on SOP repos.
2. **Registry + sync** — `push`, `pull`, `publish`; on-prem Docker Compose.
3. **Desktop app** — after UI mocks are approved.
4. **Hosted multi-org registry.**

Each stage gets its own implementation plan.
