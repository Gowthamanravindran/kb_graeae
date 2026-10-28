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


def main() -> None:
    app()
