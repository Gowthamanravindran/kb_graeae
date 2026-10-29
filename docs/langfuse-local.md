# Running Langfuse locally (free, Docker)

kb sends traces to a self-hosted Langfuse when `langfuse:` is set in `kb.yaml` **and** both
API keys are in the environment. Without them, tracing is off and nothing is sent.

## 1. Start Langfuse (needs Docker Desktop)

```bash
git clone --depth 1 https://github.com/langfuse/langfuse.git ~/langfuse
cd ~/langfuse
docker compose up -d          # web on http://localhost:3000; first start takes a few minutes
```

Open http://localhost:3000, sign up (local account), create an organisation and a project
(e.g. `kb`), then **Project settings → API keys → Create**.

## 2. Point kb at it

In the desktop app, open **Settings** and enter the public key, secret key and host, then click **Save** and **Test connection**. Settings writes the same file the CLI reads.

Without the app, edit the file directly:

Put the keys in **`~/.kb/kb.env`** (or `$KB_HOME/kb.env`). The CLI and the desktop app both read it,
including when the app is opened from Finder:

```bash
mkdir -p ~/.kb && cat > ~/.kb/kb.env <<'ENV'
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3000
ENV
chmod 600 ~/.kb/kb.env
```

Restart the app after editing the file. Variables already set in your shell win over the file.
A `langfuse:` URL in a project's `kb.yaml` overrides `LANGFUSE_HOST` for that project.
Already running Langfuse for something else? Create a separate project in it (for example `graeae`) and use that
project's keys; one Langfuse server holds many projects.

## 3. What you will see

| Trace | Spans | Inside |
|---|---|---|
| `analyze` (tags: project, branch) | `checkout`, `graph`, `link`, `module_map` | one `name_module:<id>` per module and `system_overview`; each holds a `claude-code` generation (tokens, cost) |
| `ask` (tags: project, branch, commits, pack_version) | LangGraph nodes `understand` … `learn` | `trace` holds the Claude call; its children are Claude's own steps, e.g. `Read …/TaskCreation.tsx`, `Grep addTask` |

`scripts/golden.py` also records each golden run as a Langfuse dataset named `golden-<date>`.

## Notes

- The SDK is Langfuse Python v5 (OpenTelemetry based); it needs a Langfuse server v3 or newer.
- If Langfuse is down, kb keeps working; the SDK logs export retries and drops the batch.
