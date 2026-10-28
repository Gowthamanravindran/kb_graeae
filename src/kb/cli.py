from __future__ import annotations

import functools
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


def main() -> None:
    app()
