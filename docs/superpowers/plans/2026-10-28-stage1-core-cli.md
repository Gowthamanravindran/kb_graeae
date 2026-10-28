# Stage 1: Core Engine + CLI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A Python package `kb` whose CLI can `analyze` a configured repo set into a local pack (graph, cross-repo links, module map, system overview) and `ask` questions that return cited UI and backend flows, with every LLM run traced in Langfuse.

**Architecture:** `kb-core` is a set of small modules: config → checkout → Graphify adapter → linker plugins → module mapper (LLM) → pack store; the agent is a LangGraph `StateGraph` over a loaded pack. All LLM access goes through an `LLMProvider` interface (Claude Code via the Claude Agent SDK is the only real implementation), and all tracing goes through a `Tracer` interface (Langfuse or no-op), so both are faked in tests.

**Tech Stack:** Python 3.12, uv, Typer, pydantic v2, graphifyy (CLI), py-tree-sitter + tree-sitter-typescript, graphql-core, NetworkX, SQLite FTS5, LangGraph, claude-agent-sdk, langfuse (v3 SDK), pytest.

**Spec:** `docs/superpowers/specs/2026-10-28-codebase-knowledge-system-design.md` (Sections 1–6, 8, 10, 11; stage 1 of Section 12). Registry, sync, desktop are later plans.

## Global Constraints

- Python 3.12; dependencies managed with `uv`; package layout `src/kb/`, tests in `tests/`.
- Pack contents: paths, symbol names, line numbers, summaries, diagrams — **never copied source text** (spec 3.4). Enforced by `packs/filter.py` and a test.
- Graphify runs code-only with LLM disabled: `GRAPHIFY_BACKEND=none graphify extract <dir> --code-only --no-viz`.
- LLM calls happen only in the module mapper (spec §4 step 4) and agent nodes `trace` and `learn` (spec §5). Steps 1–3 of analysis never call an LLM.
- Claude Code runs with read-only tools only: `allowed_tools=["Read", "Grep", "Glob"]`.
- Tracing is off unless `langfuse` is set in `kb.yaml` **and** `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` env vars exist.
- Unmatched links are recorded with `resolved=False`, never guessed.
- Suggested scenarios are queued, never auto-run.
- Every overlay item is saved with `visibility="private"` in stage 1.
- Packs live under `$KB_HOME/packs/<project>@<branch>/` (`KB_HOME` defaults to `~/.kb`; `/` in branch names becomes `__`).
- **Addition to spec:** `RepoConfig` accepts `local_path` as an alternative to `url`, because the SOP test repos are plain folders with no `.git`. A non-git folder gets commit id `local-<first 12 hex of sha256 over sorted (relpath, size, mtime_ns)>`.

## Review Focus

1. **Repo folder that is not a git repo** (the provided SOP folders) — analyze must work and record a `local-…` commit id. → Task 4 test `test_local_non_git_folder_gets_content_hash_commit`.
2. **Claude Code missing or not signed in** — `kb ask`/`kb analyze` exit code 2 with message "Claude Code is not available: <reason>. Run `claude` once to sign in."; no traceback; analyze keeps steps 1–3 output and marks pack `incomplete`. → Task 10 `test_provider_unavailable_raises_llm_unavailable`, Task 13 `test_analyze_without_llm_saves_structure_and_marks_incomplete`.
3. **Claude returns text that isn't the requested JSON** — retry once, then that module is recorded as failed and `--resume` retries it. → Task 12 `test_bad_json_retried_then_module_marked_failed`.
4. **Question matches no module and no nodes** — answer says what was searched and that nothing matched; no flow invented; nothing saved. → Task 15 `test_no_match_returns_not_found_and_saves_nothing`.
5. **Citation points at a file outside the checkouts or a line past end-of-file** — verify marks the step `verified=False`. → Task 15 `test_verify_flags_missing_file_and_out_of_range_line`.

---

## File Structure

```
kb-system/
  pyproject.toml
  src/kb/
    __init__.py            __version__
    cli.py                 Typer app: version, analyze, ask, memory list
    config.py              kb.yaml models + load_config()
    errors.py              KbError, ConfigError, LLMUnavailable, LLMOutputError
    graph/model.py         CodeNode, CodeEdge, CodeGraph (NetworkX MultiDiGraph)
    ingest/checkout.py     checkout_repo() → Checkout
    ingest/graphify.py     run_graphify(), parse_graphify_json()
    linkers/base.py        Linker protocol, LinkContext, register/get_linker
    linkers/ts.py          tree-sitter helpers for .ts/.tsx/.js/.jsx
    linkers/graphql.py     GraphQL frontend-op ↔ backend-resolver linker
    linkers/bull_queue.py  queue producer ↔ consumer linker
    linkers/react_router.py route ↔ page component linker
    packs/model.py         Manifest, Module, Citation, FlowStep, Flow, QAItem, Suggestion
    packs/filter.py        sanitize_graph()
    packs/store.py         PackStore
    packs/index.py         build_index(), search()
    packs/staleness.py     hash_span(), is_stale()
    llm/base.py            LLMProvider protocol, LLMRequest, LLMResult, ToolStep
    llm/json_call.py       ask_json()
    llm/claude_code.py     ClaudeCodeProvider
    tracing.py             Tracer protocol, NoopTracer, LangfuseTracer, make_tracer()
    mapper/cluster.py      cluster_graph()
    mapper/mapper.py       map_modules(), write_system_overview()
    analyze.py             run_analyze()
    agent/state.py         AgentState
    agent/nodes.py         understand, collect, trace, verify, render, learn
    agent/graph.py         build_agent()
    ask.py                 run_ask()
  tests/
    fakes.py               FakeProvider, RecordingTracer
    fixtures/mini-fe/      tiny React+Apollo app (routes, gql ops)
    fixtures/mini-be/      tiny graphql-compose + Bull backend
    fixtures/graphify/     captured graphify graph.json (Task 5)
    golden/sop_questions.yaml
    test_*.py              one per module
  examples/sop.kb.yaml
```

---

### Task 1: Project scaffold and CLI entry point

**Files:**
- Create: `pyproject.toml`, `src/kb/__init__.py`, `src/kb/cli.py`, `src/kb/errors.py`, `tests/test_cli.py`, `.gitignore`

**Interfaces:**
- Produces: console script `kb`; `kb.cli.app: typer.Typer`; `kb.__version__ = "0.1.0"`; exception classes `KbError(Exception)`, `ConfigError(KbError)`, `LLMUnavailable(KbError)`, `LLMOutputError(KbError)`. CLI maps any `KbError` to a one-line stderr message; `LLMUnavailable` → exit 2, other `KbError` → exit 1.

- [ ] **Step 1: Create project** — `uv init --package --name kb --python 3.12`, then `uv add typer pydantic pyyaml networkx graphql-core tree-sitter tree-sitter-typescript langgraph claude-agent-sdk langfuse` and `uv add --dev pytest`. `graphifyy` is a CLI dependency: `uv tool install graphifyy`.
- [ ] **Step 2: Write failing test**

```python
from typer.testing import CliRunner
from kb.cli import app

def test_version_prints_version():
    r = CliRunner().invoke(app, ["version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.stdout
```

- [ ] **Step 3:** Run `uv run pytest tests/test_cli.py -v` → FAIL (import error).
- [ ] **Step 4:** Implement `app` with a `version` command and the error-mapping wrapper.
- [ ] **Step 5:** Run → PASS. `uv run kb version` prints `0.1.0`.
- [ ] **Step 6: Commit** `git commit -m "feat: scaffold kb package and CLI"`

---

### Task 2: Config (`kb.yaml`)

**Files:** Create `src/kb/config.py`, `tests/test_config.py`, `examples/sop.kb.yaml`

**Interfaces:**
- Produces:
  - `RepoConfig(name: str, role: str, url: str | None, local_path: Path | None, path: str | None)` — exactly one of `url`/`local_path` required.
  - `LinkerConfig(type: str, from_: str | None (alias "from"), to: str | None, in_: str | None (alias "in"), options: dict = {})`
  - `ProjectConfig(project: str, repos: list[RepoConfig], linkers: list[LinkerConfig] = [], context_docs: list[str] = [], ignore: list[str] = [], registry: str | None, langfuse: str | None)`
  - `load_config(path: Path) -> ProjectConfig` — raises `ConfigError` naming the field on invalid input. Relative `local_path` resolves against the yaml's folder.
  - `context_docs` entries are `"<repo>:<glob>"`; `ProjectConfig.context_doc_globs() -> list[tuple[str, str]]`.

- [ ] **Step 1: Failing tests**

```python
def test_loads_spec_example(tmp_path): ...            # spec §3.1 yaml parses; repos[0].role == "frontend"
def test_repo_needs_exactly_one_source(tmp_path):     # both url+local_path → ConfigError mentioning "url" and "local_path"
def test_linker_refers_to_unknown_repo(tmp_path):     # from: nope → ConfigError mentioning "nope"
def test_local_path_resolved_relative_to_yaml(tmp_path)
def test_context_doc_globs_split(tmp_path):           # "sop-backend:docs/adr/**" → ("sop-backend", "docs/adr/**")
```

- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement with pydantic models + `model_validator`s.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Write `examples/sop.kb.yaml` for the two SOP folders using `local_path` (`sop-web` = `sop-fe-master` with `path: packages/sop-web`, role frontend; `sop-backend` role backend; linkers graphql(from sop-web to sop-backend, options `{backend_style: graphql-compose}`), bull-queue(in sop-backend), react-router(in sop-web); context_docs `sop-backend:CONTEXT.md`, `sop-backend:docs/adr/**`; ignore `**/*.svg`, `**/__generated__/**`).
- [ ] **Step 6: Commit** `feat: kb.yaml config loading`

---

### Task 3: Graph model

**Files:** Create `src/kb/graph/model.py`, `tests/test_graph_model.py`

**Interfaces:**
- Produces:
  - `CodeNode(id: str, repo: str, kind: str, name: str, file: str, line: int | None, end_line: int | None)` — `kind` ∈ `file|module|class|function|component|variable|graphql_op|resolver|queue_topic|route`. `id` is `"<repo>:<file>#<name>"` (files: `"<repo>:<file>"`).
  - `CodeEdge(src: str, dst: str, kind: str, origin: Literal["extracted","inferred","linker"], resolved: bool = True, label: str | None)` — linker `kind`s: `sends_graphql`, `handled_by`, `enqueues`, `consumed_by`, `routes_to`. Unresolved edges have `dst = "unresolved:<key>"`.
  - `CodeGraph` wrapping `nx.MultiDiGraph`: `add_node(CodeNode)`, `add_edge(CodeEdge)`, `node(id) -> CodeNode`, `nodes(repo: str | None = None, kind: str | None = None) -> list[CodeNode]`, `edges(kind: str | None = None) -> list[CodeEdge]`, `neighbors(id, kinds: set[str] | None, direction: Literal["out","in","both"]) -> list[CodeNode]`, `merge(other: CodeGraph) -> None`, `to_json() -> dict`, `CodeGraph.from_json(d) -> CodeGraph`.

- [ ] **Step 1: Failing tests:** round-trip `to_json`/`from_json` equality; `merge` of two repos' graphs keeps both; `neighbors(..., kinds={"handled_by"}, direction="out")` returns only that edge kind; adding an edge whose `src` is missing raises `KeyError`.
- [ ] **Step 2:** Run → FAIL. **Step 3:** Implement. **Step 4:** Run → PASS.
- [ ] **Step 5: Commit** `feat: code graph model`

---

### Task 4: Checkout

**Files:** Create `src/kb/ingest/checkout.py`, `tests/test_checkout.py`

**Interfaces:**
- Consumes: `RepoConfig`.
- Produces: `Checkout(repo: str, root: Path, commit: str)`; `checkout_repo(repo: RepoConfig, branch: str, work_dir: Path) -> Checkout`. `root` already includes `repo.path` when set.
  - `url`: clone into `work_dir/<name>` (or `git fetch` if present), `git checkout <branch>`, commit = `git rev-parse HEAD`.
  - `local_path` that is a git repo: no branch switch (raise `KbError` if its current branch ≠ requested); commit = HEAD.
  - `local_path` not a git repo: commit = `local-` + content hash (Global Constraints), ignoring `node_modules`, `.git`; any `branch` label is accepted and only recorded.

- [ ] **Step 1: Failing tests:** `test_git_repo_checkout_records_head` (init a temp git repo, commit, assert sha); `test_local_non_git_folder_gets_content_hash_commit` (two calls equal; editing a file changes it; starts with `local-`); `test_local_git_repo_wrong_branch_raises`; `test_subpath_applied_to_root`.
- [ ] **Step 2–4:** FAIL → implement with `subprocess.run(["git", ...], check=True)` → PASS.
- [ ] **Step 5: Commit** `feat: repo checkout with local-folder support`

---

### Task 5: Graphify adapter

**Files:** Create `src/kb/ingest/graphify.py`, `tests/fixtures/graphify/` (captured), `tests/test_graphify.py`

**Interfaces:**
- Consumes: `Checkout`, `CodeGraph`.
- Produces: `run_graphify(checkout: Checkout, out_dir: Path, ignore: list[str]) -> Path` (returns path to `graph.json`); `parse_graphify_json(path: Path, repo: str, repo_root: Path) -> CodeGraph`.

- [ ] **Step 1: Capture the real schema.** Run `GRAPHIFY_BACKEND=none graphify extract tests/fixtures/mini-be --code-only --no-viz` (create the `mini-be` fixture from Task 7 first if needed, or any 3-file TS folder). Copy `graphify-out/graph.json` to `tests/fixtures/graphify/mini_graph.json`. Record in a module docstring which graphify keys map to `CodeNode.id/kind/name/file/line` and `CodeEdge.src/dst/kind/origin` (graphify's `EXTRACTED`/`INFERRED` tag → `origin`). Paths become relative to `repo_root`; ids are rebuilt to our `"<repo>:<file>#<name>"` form.
- [ ] **Step 2: Failing tests** against the captured fixture: every node has `repo == "mini-be"` and a relative `file`; at least one `function` node with a `line`; edge origins are only `extracted|inferred`; `run_graphify` passes env `GRAPHIFY_BACKEND=none` (assert via monkeypatched `subprocess.run`); `ignore` globs are excluded from the parsed graph.
- [ ] **Step 3–4:** Implement → PASS. If `graphify` is not on PATH, `run_graphify` raises `KbError("graphify not found: uv tool install graphifyy")`.
- [ ] **Step 5: Commit** `feat: graphify adapter`

---

### Task 6: Linker framework and TypeScript helpers

**Files:** Create `src/kb/linkers/base.py`, `src/kb/linkers/ts.py`, `tests/test_ts_helpers.py`

**Interfaces:**
- Produces:
  - `LinkContext(graph: CodeGraph, checkouts: dict[str, Checkout], config: LinkerConfig)`
  - `class Linker(Protocol): type: str; def run(self, ctx: LinkContext) -> list[CodeEdge]`
  - `register_linker(cls)`, `get_linker(type: str) -> Linker` (unknown → `ConfigError`).
  - `ts.iter_source_files(root: Path, ignore: list[str]) -> Iterator[Path]` (`.ts .tsx .js .jsx`, skips `node_modules`).
  - `ts.parse(path: Path) -> tree_sitter.Tree | None` (None on read/parse failure; logs path).
  - `ts.tagged_templates(tree, tag: str) -> list[TemplateHit(text: str, line: int, var_name: str | None)]`
  - `ts.calls(tree, method: str) -> list[CallHit(receiver: str, first_arg: str, first_arg_kind: Literal["string","member","identifier","other"], line: int)]`
  - `ts.object_literals_with(tree, key: str) -> list[ObjHit(props: dict[str, str], line: int, var_name: str | None)]` (props = key → raw value text)
  - `ts.jsx_elements(tree, name: str) -> list[JsxHit(attrs: dict[str, str], line: int)]`
  - `ts.string_constants(tree) -> dict[str, str]` (`export const X = '...'`)

- [ ] **Step 1: Failing tests** with inline TS snippets: gql template text and `var_name`; `queue.add(Topics.A, …)` → `first_arg="Topics.A"`, kind `member`; object literal `{ name: 'AddTask', resolve: … }` props; `<Route path={X} component={Page} />` attrs; string constants; a file with a syntax error still returns a tree (tree-sitter is error-tolerant) and an unreadable file returns None.
- [ ] **Step 2–4:** FAIL → implement using `tree_sitter_typescript.language_tsx()` for all four extensions → PASS.
- [ ] **Step 5: Commit** `feat: linker framework and TS helpers`

---

### Task 7: GraphQL linker

**Files:** Create `src/kb/linkers/graphql.py`, `tests/fixtures/mini-fe/`, `tests/fixtures/mini-be/`, `tests/test_linker_graphql.py`

**Fixtures (model them on SOP's real shapes):**
- `mini-fe/src/tasks/task.graphql.ts`: `export const addTaskQuery = gql\`mutation AddTask($input: AddTaskTypeInput) { AddTask(input: $input) { eid } }\`` and a query `TaskById` whose backend resolver does **not** exist.
- `mini-fe/src/tasks/TaskCreation.tsx`: `useMutation(addTaskQuery)`.
- `mini-be/src/module/tasks/task/resolvers.ts`: `const AddTask = { name: 'AddTask', type: …, resolve: async () => {} }`.
- `mini-be/src/module/tasks/task/index.ts`: `export const mutation = { AddTask: TaskTC.getResolver('AddTask') }`.

**Interfaces:**
- Consumes: `LinkContext`, `ts` helpers.
- Produces: `GraphqlLinker` (`type = "graphql"`). Options: `backend_style: Literal["graphql-compose", "sdl"]`, default `"sdl"`. Emits:
  - node `kind="graphql_op"` per frontend operation (`name` = operation name, `file`, `line`), plus edge `sends_graphql` from the component file that references the op's `var_name` to the op node.
  - node `kind="resolver"` per backend root field found; edge `handled_by` op → resolver, matched on each root field name in the op's selection set (parsed with `graphql.parse`).
  - unresolved: `handled_by` to `unresolved:graphql:<Field>`, `resolved=False`.
  - Backend discovery: `graphql-compose` = object literals with both `name` (string) and `resolve` keys → field = `name` value; `sdl` = `type Query|Mutation { … }` in `.graphql` files and `gql` templates.

- [ ] **Step 1: Failing tests:** `test_addtask_links_frontend_to_resolver` (edge `handled_by` from op `AddTask` to resolver in `resolvers.ts` at the object's line); `test_component_sends_op` (`TaskCreation.tsx` → op); `test_missing_backend_field_is_unresolved` (`TaskById` → `unresolved:graphql:TaskById`, `resolved=False`); `test_sdl_style` (small SDL fixture inline).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: graphql linker`

---

### Task 8: Bull-queue and React-router linkers

**Files:** Create `src/kb/linkers/bull_queue.py`, `src/kb/linkers/react_router.py`, extend fixtures, `tests/test_linker_queue_router.py`

**Interfaces:**
- `BullQueueLinker` (`type="bull-queue"`): producers = `ts.calls(tree, "add")` where receiver ends with `Queue`; consumers = `ts.calls(tree, "process")`. Topic key = last segment of `first_arg` (`TaskTopics.TASK_REPEAT` → `TASK_REPEAT`; string literal used as-is). Emits `queue_topic` node per key, `enqueues` (producer file → topic), `consumed_by` (topic → consumer file). Consumer whose `first_arg_kind` is `identifier` (dynamic topic, like SOP's `makeModuleProcessor`) produces `consumed_by` to `unresolved:queue:<identifier>`. A topic with a producer but no consumer gets `consumed_by` → `unresolved:queue:<KEY>`.
- `ReactRouterLinker` (`type="react-router"`): `<Route path=… component={X}>` and `element={<X />}`; path identifiers resolved through `ts.string_constants` across the repo (SOP: `TASKS_CREATE_URL = '/tasks/create-task'`). Emits `route` node (`name` = path) and `routes_to` route → component's defining file (via import resolution of `X` relative to the routing file; unresolvable → `unresolved:route:<X>`).

- [ ] **Step 1: Failing tests:** fixture `mini-be/src/queue/task/main.ts` with `TaskQueue.add(TaskTopics.TASK_REPEAT, …)` and no consumer → `test_producer_without_consumer_is_unresolved`; add `queue/index.ts` with `JobsQueue.process('SIGN_UP', handler)` and matching producer → `test_string_topic_links`; `test_dynamic_topic_consumer_unresolved`; `mini-fe/src/appRoutes.ts` + `App.tsx` with `<Route path={TASKS_CREATE_URL} component={TaskCreation} />` → `test_route_constant_resolved_to_component`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: bull-queue and react-router linkers`

---

### Task 9: Pack model, filter, store, FTS index, staleness

**Files:** Create `src/kb/packs/{model,filter,store,index,staleness}.py`, `tests/test_packs.py`

**Interfaces:**
- `packs/model.py` (pydantic):
  - `Manifest(project, branch, commits: dict[str,str], version: int = 1, parent: int | None, author: str, created_at: datetime, status: Literal["incomplete","complete"], stats: dict[str,int], failed_modules: list[str] = [])`
  - `Module(id, name, summary, members: list[str], entry_points: list[str])`
  - `Citation(repo, file, line_start: int, line_end: int, content_hash: str)`
  - `FlowStep(side: Literal["ui","backend"], text: str, citation: Citation | None, verified: bool)`
  - `Flow(id, question, module_ids: list[str], steps: list[FlowStep], mermaid: str, created_at, visibility: Literal["private","published"] = "private")`
  - `QAItem(id, question, answer: str, flow_id: str | None, citations: list[Citation], author, created_at, visibility = "private", stale: bool = False)`
  - `Suggestion(id, question, source_qa_id)`
- `packs/filter.py`: `sanitize_graph(g: CodeGraph) -> dict` — returns JSON with only the `CodeNode`/`CodeEdge` fields; any extra keys dropped.
- `packs/store.py`: `PackStore(kb_home: Path)`; `pack_dir(project, branch) -> Path`; `write_base(project, branch, manifest, graphs: dict[str, CodeGraph], links: list[CodeEdge], modules: list[Module], system_md: str) -> Path`; `load(project, branch) -> Pack` where `Pack` holds manifest, merged `CodeGraph` (repo graphs + links), modules, system_md, and overlay lists; `save_flow(Flow)`, `save_qa(QAItem)`, `add_suggestions(list[Suggestion])`, `save_module_note(module_id, text)`, `update_manifest(...)`. Layout exactly as spec §3.2.
- `packs/index.py`: `build_index(pack_dir: Path, pack: Pack) -> None` (SQLite FTS5 table `docs(kind, ref, text)` over node names+files, module name+summary, QA question+answer); `search(pack_dir, query: str, limit: int = 20) -> list[Hit(kind, ref, score)]`.
- `packs/staleness.py`: `hash_span(root: Path, file: str, start: int, end: int) -> str` (sha256 of those lines, first 16 hex); `is_stale(c: Citation, checkouts: dict[str, Checkout]) -> bool` (missing file counts as stale).

- [ ] **Step 1: Failing tests:** `test_write_then_load_roundtrip`; `test_layout_matches_spec` (asserts `manifest.json`, `base/graph/<repo>.json`, `base/links.json`, `base/modules.json`, `base/system.md`, `overlay/qa/`, `overlay/flows/` exist); `test_sanitized_graph_contains_no_source_text` (build a graph from a fixture file containing the unique string `SECRET_BODY_MARKER` in a function body; assert the marker is absent from every file under the pack dir); `test_fts_finds_module_by_summary_word`; `test_hash_changes_when_cited_lines_change_only`; `test_missing_file_is_stale`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: pack store, index and staleness`

---

### Task 10: LLM provider interface and Claude Code provider

**Files:** Create `src/kb/llm/base.py`, `src/kb/llm/claude_code.py`, `tests/fakes.py`, `tests/test_llm.py`

**Interfaces:**
- `ToolStep(tool: str, input_summary: str, started_at: datetime, duration_ms: int)`
- `LLMRequest(prompt: str, system: str, cwd: Path, extra_dirs: list[Path] = [], allow_tools: bool = False)`
- `LLMResult(text: str, steps: list[ToolStep], input_tokens: int, output_tokens: int, cost_usd: float | None, duration_ms: int)`
- `class LLMProvider(Protocol): name: str; def check(self) -> None; def run(self, req: LLMRequest) -> LLMResult` — `check` raises `LLMUnavailable(reason)`.
- `ClaudeCodeProvider(model: str | None = None)` — `run` calls `claude_agent_sdk.query(prompt=…, options=ClaudeAgentOptions(system_prompt=…, cwd=…, add_dirs=…, allowed_tools=["Read","Grep","Glob"] if allow_tools else [], permission_mode="default", max_turns=30))`, collecting `ToolUseBlock`s into `ToolStep`s and usage/cost from the final `ResultMessage`. `check` runs `claude --version` and a 1-turn `query("Reply OK")`; any `CLINotFoundError`/auth failure → `LLMUnavailable("Claude Code CLI not found" | "not signed in")`.
- `tests/fakes.py`: `FakeProvider(responses: list[str], steps: list[ToolStep] = [], unavailable: str | None = None)` returning responses in order and recording requests.

- [ ] **Step 1: Failing tests:** `test_provider_unavailable_raises_llm_unavailable`; `test_claude_provider_maps_tool_blocks` (monkeypatch `claude_agent_sdk.query` to yield a fake AssistantMessage with two ToolUseBlocks and a ResultMessage → 2 steps, tokens set).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: LLM provider interface and Claude Code provider`

---

### Task 11: Tracing

**Files:** Create `src/kb/tracing.py`, `src/kb/llm/json_call.py`, extend `tests/fakes.py` (`RecordingTracer`), `tests/test_tracing.py`, `tests/test_json_call.py`

**Interfaces:**
- `class Span(Protocol): def end(self, output: Any = None, metadata: dict | None = None) -> None; def child(self, name: str, input: Any = None, metadata: dict | None = None) -> "Span"`
- `class Tracer(Protocol): def trace(self, name: str, tags: dict[str, str]) -> ContextManager[Span]; def span(self, name: str, input: Any = None) -> ContextManager[Span]; def record_llm(self, parent: Span, result: LLMResult, name: str, prompt: str) -> None; def langgraph_callbacks(self) -> list; def score(self, name: str, value: float) -> None; def flush(self) -> None`
- `record_llm` creates a generation observation (tokens, cost, duration) and one child span per `ToolStep` named `"<tool> <input_summary>"`.
- `NoopTracer`; `LangfuseTracer(host: str)` using `langfuse.get_client()` (`start_as_current_span`, `start_as_current_generation`) and `langfuse.langchain.CallbackHandler` for `langgraph_callbacks`.
- `make_tracer(cfg: ProjectConfig) -> Tracer` — Langfuse only when `cfg.langfuse` and both key env vars are set; otherwise `NoopTracer`.
- Trace tags: `project`, `branch`, `commits` (comma-joined `repo=sha`), `pack_version`.
- `kb.llm.json_call.ask_json(provider: LLMProvider, req: LLMRequest, model: type[BaseModel], tracer: Tracer, span_name: str) -> BaseModel` — appends "Reply with one JSON object in a ```json block matching this schema: <schema>"; parses the last ```json block; on failure retries once with the parse error appended; second failure raises `LLMOutputError`. Each call is recorded with `tracer.record_llm` under a span named `span_name`.

- [ ] **Step 1: Failing tests:** `test_make_tracer_noop_without_keys`; `test_make_tracer_langfuse_with_keys` (env set, monkeypatch `get_client`); `test_record_llm_creates_child_per_tool_step` (RecordingTracer: 2 steps → 2 children with names `"Read src/a.ts"`, `"Grep addTask"`); `test_ask_json_parses_block`; `test_ask_json_retries_once_then_raises` (FakeProvider returns two non-JSON replies → `LLMOutputError`, exactly 2 requests).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: tracing, Langfuse, and ask_json`

---

### Task 12: Module mapper

**Files:** Create `src/kb/mapper/cluster.py`, `src/kb/mapper/mapper.py`, `tests/test_mapper.py`

**Interfaces:**
- Consumes: `ask_json` (Task 11), `LLMProvider` (Task 10), `Tracer` (Task 11), `Module` (Task 9).
- `cluster_graph(g: CodeGraph, max_clusters: int = 40, min_size: int = 5) -> list[Cluster(id: str, members: list[str], top_paths: list[str])]` — algorithm: seed groups by the first two path segments under each repo's `src`-like root (folder structure), then merge groups joined by linker edges (`handled_by`, `routes_to`, `consumed_by`) using `nx.community.louvain_communities(seed=0)` on the group-level graph weighted by cross-group edge counts; groups smaller than `min_size` join their most-connected neighbour; stop at `max_clusters`. Cluster `id` = slug of its largest top path. Deterministic for the same graph.
- `ModuleDraft(name: str, summary: str, entry_points: list[str])` (pydantic, for `ask_json`).
- `map_modules(clusters, g, context_text: str, provider, tracer, checkpoint_dir: Path, resume: bool) -> tuple[list[Module], list[str]]` — returns modules and failed cluster ids. One `ask_json` per cluster with prompt containing: glossary (`context_text`, truncated to 12,000 chars), cluster top paths, up to 60 member symbol names, its linker edges. Instruction: name it as a product module using glossary terms; summary 3–5 sentences, plain English; entry points chosen only from given member ids. Each result written to `checkpoint_dir/<cluster_id>.json`; with `resume=True` existing checkpoints are reused without an LLM call.
- `write_system_overview(modules, g, provider, tracer) -> str` — one call; returns Markdown with a plain-English walkthrough and a ```mermaid block whose node labels are sentences.

- [ ] **Step 1: Failing tests:** `test_cluster_deterministic`; `test_linked_folders_merge` (fe tasks folder + be tasks folder joined by `handled_by` end in one cluster); `test_resume_skips_checkpointed` (FakeProvider gets 0 calls for checkpointed clusters); `test_bad_json_retried_then_module_marked_failed`; `test_entry_points_outside_members_dropped`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: module mapper`

---

### Task 13: `kb analyze`

**Files:** Create `src/kb/analyze.py`; modify `src/kb/cli.py`; `tests/test_analyze.py`

**Interfaces:**
- `run_analyze(cfg: ProjectConfig, branch: str, store: PackStore, provider: LLMProvider, tracer: Tracer, resume: bool = False, author: str) -> Manifest`
  1. checkout all repos (span `checkout`); 2. graphify per repo (span `graph`, metadata files/unparsed); 3. run each configured linker (span `link`, metadata resolved/unresolved counts); write base with `status="incomplete"` immediately after step 3; 4. `provider.check()` — on `LLMUnavailable` keep the incomplete pack and re-raise; mapper + overview (span `module_map`); 5. update manifest `status="complete"` unless any module failed; build FTS index.
  - Context text = concatenation of files matched by `context_docs`.
  - `stats` keys: `files`, `unparsed`, `nodes`, `links_resolved`, `links_unresolved`, `modules`.
- CLI: `kb analyze <config.yaml> --branch <b> [--resume]`; prints a one-screen summary (stats, pack path, status). `author` = `git config user.name` or `$USER`.

- [ ] **Step 1: Failing tests** (mini fixtures, FakeProvider, Graphify monkeypatched to return a parsed fixture graph): `test_analyze_writes_complete_pack`; `test_analyze_without_llm_saves_structure_and_marks_incomplete` (FakeProvider `unavailable="not signed in"` → `LLMUnavailable`; pack exists with `links.json`, status `incomplete`); `test_resume_completes_failed_modules`; `test_analyze_emits_spans_in_order` (RecordingTracer names: checkout, graph, link, module_map).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: kb analyze`

---

### Task 14: Agent state and graph wiring

**Files:** Create `src/kb/agent/state.py`, `src/kb/agent/graph.py`, `tests/test_agent_graph.py`

**Interfaces:**
- `AgentState(TypedDict)`: `question: str`, `seed_qa_id: str | None`, `kind: Literal["how","where","impact","diagram"] | None`, `module_ids: list[str]`, `reused_flow_id: str | None`, `shortlist: list[str]` (node ids), `draft_steps: list[FlowStep]`, `steps: list[FlowStep]`, `answer: str`, `mermaid: str`, `not_found: bool`, `saved_qa_id: str | None`, `suggestions: list[str]`.
- `AgentDeps(pack: Pack, pack_dir: Path, checkouts: dict[str, Checkout], provider: LLMProvider, tracer: Tracer, store: PackStore, author: str)`
- `build_agent(deps: AgentDeps) -> CompiledStateGraph` with nodes `understand → collect → trace → verify → render → learn → END`; conditional edges: after `understand`, `reused_flow_id` set → `render`, `not_found` → `render` (render handles both); after `render`, `not_found` or reused → `END` (no `learn`).
- Invoked with `config={"callbacks": deps.tracer.langgraph_callbacks()}`.

- [ ] **Step 1: Failing test:** `test_graph_routes` — with node functions stubbed via monkeypatch, assert visited order for three cases: normal (all six), reuse (understand, render), not found (understand, render).
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: agent graph wiring`

---

### Task 15: Agent nodes

**Files:** Create `src/kb/agent/nodes.py`, `tests/test_agent_nodes.py`

**Interfaces** (each `fn(state: AgentState, deps: AgentDeps) -> dict` partial update):
- `understand`: `kind` by keyword rules (`"how"` default; `"where is"`→where; `"what breaks"|"impact"`→impact; `"diagram"`→diagram). `search(pack_dir, question)` → module ids (top 3 module hits, else modules containing top node hits). If `seed_qa_id`, add that QA's modules. Reuse: an existing non-stale `Flow` whose question has FTS score above 0.8× best and shares a module → `reused_flow_id`. No module and no node hits → `not_found=True`.
- `collect`: BFS from each module's `entry_points` over linker edges `sends_graphql, handled_by, routes_to, enqueues, consumed_by` (both directions) and any edge with `origin` `extracted|inferred` (out direction only), max depth 4, cap 40 nodes; order: route → component → op → resolver → queue nodes first. Output distinct files in `shortlist`.
- `trace`: one `ask_json` with `allow_tools=True`, `cwd` = first checkout, `extra_dirs` = others. Prompt: question, module summaries, shortlist (`repo:file:line name`), seed QA answer if any, glossary excerpt. Response model `TraceOut(steps: list[{side, text, repo, file, line_start, line_end}], answer: str)`. Rule in prompt: every step must cite code it read; plain English.
- `verify` (no LLM): for each draft step, check `repo` in checkouts, file exists, `1 ≤ line_start ≤ line_end ≤ file line count`; set `verified`, build `Citation` with `hash_span`; invalid → `citation=None, verified=False`.
- `render`: not found → `answer = "I couldn't find code for this in <project>@<branch>. Searched modules and symbols for: <terms>."`; reuse → load flow; otherwise build Mermaid `flowchart LR` from steps in order with sentence labels (`ui` steps first), unverified steps labelled with suffix " (unverified)".
- `learn`: save `Flow` and `QAItem` (private), set `saved_qa_id`; one `ask_json` for `Suggestions(questions: list[str])` (3–5) → `add_suggestions`; one `ask_json` for `ModuleNote(module_id, improved_summary)` per involved module → `save_module_note`. Rebuild FTS index.

- [ ] **Step 1: Failing tests:** `test_understand_finds_tasks_module`; `test_no_match_returns_not_found_and_saves_nothing`; `test_reuses_fresh_flow_without_llm` (FakeProvider records 0 calls); `test_stale_flow_not_reused`; `test_collect_follows_linker_edges_to_resolver_and_queue`; `test_verify_flags_missing_file_and_out_of_range_line`; `test_render_marks_unverified_in_mermaid`; `test_learn_saves_private_items_and_suggestions`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: agent nodes`

---

### Task 16: `kb ask` and `kb memory list`

**Files:** Create `src/kb/ask.py`; modify `src/kb/cli.py`; `tests/test_ask_cli.py`

**Interfaces:**
- `run_ask(cfg, branch, question: str, store, provider, tracer, from_qa: str | None = None, author: str) -> AgentState` — loads pack (missing → `KbError("No analysed pack for <p>@<b>. Run kb analyze first.")`), re-resolves checkouts (Task 4), runs agent inside `tracer.trace("ask", tags)`, flushes tracer.
  - Checkout missing for a repo → agent still runs; `trace` prompt says "no source available"; all steps `verified=False`; answer prefixed "No source verification —".
- CLI: `kb ask <config> --branch <b> "<question>" [--from <qa_id>] [--json]` prints answer, `UI flow` and `Backend flow` lists with `file:line` and ✓/unverified, the Mermaid block, saved QA id, and suggestions. `kb memory list <config> --branch <b>` prints QA items (id, question, visibility, stale).

- [ ] **Step 1: Failing tests** (CliRunner, FakeProvider injected via `kb.cli.get_provider` monkeypatch): `test_ask_prints_flows_and_saves`; `test_ask_without_pack_errors_cleanly` (exit 1, message); `test_ask_llm_unavailable_exit_2`; `test_ask_from_seeds_context` (prompt sent to FakeProvider contains the seed answer); `test_memory_list_shows_private_item`.
- [ ] **Step 2–4:** FAIL → implement → PASS.
- [ ] **Step 5: Commit** `feat: kb ask and memory list`

---

### Task 17: Acceptance on the SOP repos (user's machine)

**Files:** Create `tests/golden/sop_questions.yaml`, `scripts/golden.py`, `docs/langfuse-local.md`

**Interfaces:**
- `golden.yaml` entries: `{question, expect_files: [paths], min_verified_ratio}`. First entry:
  `How does scheduling work in Tasks?` → expect `sub-components/tasks/create-task/TaskCreation.tsx`, `sub-components/tasks/create-task/task.graphql.ts`, `module/tasks/task/resolvers.ts`, `queue/task/main.ts`; `min_verified_ratio: 0.75`. Add 9 more covering Chapters, Training, Audits, Launcher, Forms (file expectations filled in by grepping the repos when writing the entry).
- `scripts/golden.py <config> --branch <b>` runs each question via `run_ask` and prints per-question citation recall and verified ratio; exit 1 if any below threshold. When Langfuse is on, it also uploads results as a Langfuse dataset run named `golden-<date>`.

- [ ] **Step 1:** On the user's Mac (connected folders `sop-fe-master`, `sop-backend-main`): check `uv`, `claude --version`, `docker`; install missing ones only with the user's go-ahead.
- [ ] **Step 2:** Langfuse: clone `https://github.com/langfuse/langfuse`, `docker compose up -d`, open `http://localhost:3000`, create a project, export `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY`, `LANGFUSE_HOST=http://localhost:3000`. Write these steps into `docs/langfuse-local.md`.
- [ ] **Step 3:** `uv run kb analyze examples/sop.kb.yaml --branch local`. Expected: status `complete`; `links_resolved` > 0 including `AddTask`; `modules` between 8 and 40; module names include "Tasks".
- [ ] **Step 4:** `uv run kb ask examples/sop.kb.yaml --branch local "How does scheduling work in Tasks?"`. Expected: the four expected files cited; TASK_REPEAT consumer either cited or shown as unverified.
- [ ] **Step 5:** In Langfuse, confirm one `ask` trace with spans understand…learn and child spans named like `Read …/TaskCreation.tsx`.
- [ ] **Step 6:** `uv run python scripts/golden.py examples/sop.kb.yaml --branch local` → exit 0.
- [ ] **Step 7: Commit** `test: SOP golden questions and acceptance run`
