from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable

import typer

from kb import __version__
from kb.errors import KbError, LLMUnavailable

app = typer.Typer(no_args_is_help=True, add_completion=False)


def handle_errors(fn: Callable) -> Callable:
    """Turn KbError into a one-line message: exit 2 for LLMUnavailable, 1 otherwise."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LLMUnavailable as e:
            typer.echo(f"Claude Code is not available: {e}. Run `claude` once to sign in.", err=True)
            raise typer.Exit(2)
        except KbError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    return wrapper


@app.callback()
def _root() -> None:
    """kb — analyse codebases into shareable knowledge packs."""


@app.command()
def version() -> None:
    """Print the kb version."""
    typer.echo(__version__)


def get_provider():
    from kb.llm.claude_code import ClaudeCodeProvider

    return ClaudeCodeProvider()


def _author() -> str:
    import getpass
    import subprocess

    r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return r.stdout.strip() or getpass.getuser()


@app.command()
@handle_errors
def analyze(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: str = typer.Option(..., "--branch", "-b", help="Branch to analyse (any label for plain folders)"),
    resume: bool = typer.Option(False, "--resume", help="Reuse module names already produced for these commits"),
) -> None:
    """Analyse a repo set at a branch into a local pack."""
    from kb.analyze import run_analyze
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.tracing import make_tracer

    cfg = load_config(config)
    store = PackStore()
    m = run_analyze(cfg, branch, store, get_provider(), make_tracer(cfg), resume=resume, author=_author())
    s = m.stats
    typer.echo(f"{cfg.project}@{branch}: {m.status}")
    typer.echo("  commits: " + ", ".join(f"{k}={v[:12]}" for k, v in m.commits.items()))
    typer.echo(f"  files {s.get('files', 0)} (unparsed {s.get('unparsed', 0)}), nodes {s.get('nodes', 0)}")
    typer.echo(f"  links {s.get('links_resolved', 0)} resolved, {s.get('links_unresolved', 0)} unresolved")
    typer.echo(f"  modules {s.get('modules', 0)}" + (f", failed: {', '.join(m.failed_modules)} (rerun with --resume)"
                                                      if m.failed_modules else ""))
    typer.echo(f"  pack: {store.pack_dir(cfg.project, branch)}")


def _cite(step) -> str:
    c = step.citation
    return f"{c.repo}:{c.file}:{c.line_start}-{c.line_end}" if c else "no valid citation"


@app.command()
@handle_errors
def ask(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    question: str = typer.Argument(..., help="Your question, e.g. 'How does scheduling work in Tasks?'"),
    branch: str = typer.Option(..., "--branch", "-b"),
    from_qa: str = typer.Option(None, "--from", help="Continue from a saved answer id (see kb memory list)"),
    as_json: bool = typer.Option(False, "--json", help="Print the full result as JSON"),
) -> None:
    """Ask a question about an analysed branch."""
    import json

    from kb.ask import run_ask
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.tracing import make_tracer

    cfg = load_config(config)
    st = run_ask(cfg, branch, question, PackStore(), get_provider(), make_tracer(cfg), from_qa=from_qa, author=_author())
    if as_json:
        out = {k: v for k, v in st.items() if k != "draft_steps"}
        out["steps"] = [s.model_dump() for s in st.get("steps", [])]
        typer.echo(json.dumps(out, indent=2, default=str))
        return
    for w in st.get("warnings", []):
        typer.echo(f"warning: {w}", err=True)
    typer.echo(st.get("answer", ""))
    steps = st.get("steps", [])
    for title, side in (("UI flow", "ui"), ("Backend flow", "backend")):
        part = [s for s in steps if s.side == side]
        if part:
            typer.echo(f"\n{title}:")
            for i, s in enumerate(part, 1):
                mark = "✓" if s.verified else "✗ unverified"
                typer.echo(f"  {i}. {s.text}\n     {mark}  {_cite(s)}")
    if st.get("mermaid"):
        typer.echo("\nDiagram:\n```mermaid\n" + st["mermaid"] + "\n```")
    if st.get("reused_flow_id"):
        typer.echo(f"\n(Reused saved flow {st['reused_flow_id']}; no Claude call made.)")
    if st.get("saved_qa_id"):
        typer.echo(f"\nSaved as {st['saved_qa_id']} (private).")
    if st.get("suggestions"):
        typer.echo("\nYou might also ask:")
        for q in st["suggestions"]:
            typer.echo(f"  - {q}")


memory_app = typer.Typer(help="Saved answers on an analysed branch.")
app.add_typer(memory_app, name="memory")


@memory_app.command("list")
@handle_errors
def memory_list(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: str = typer.Option(..., "--branch", "-b"),
) -> None:
    """List saved Q&As: id, visibility, stale flag, question."""
    from kb.config import load_config
    from kb.packs.store import PackStore

    cfg = load_config(config)
    pack = PackStore().load(cfg.project, branch)
    if not pack.qa:
        typer.echo("No saved answers yet.")
    for q in sorted(pack.qa, key=lambda q: q.created_at):
        typer.echo(f"{q.id}  {q.visibility:<9} {'STALE ' if q.stale else ''}{q.question}")


def main() -> None:
    app()
