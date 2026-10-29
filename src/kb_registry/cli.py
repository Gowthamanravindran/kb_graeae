from __future__ import annotations

import typer

from kb_registry.admin import app as admin_app
from kb_registry.settings import get_settings

app = typer.Typer(help="kb-registry: shared store for analysed branches.", no_args_is_help=True)
app.add_typer(admin_app, name="admin")


@app.command()
def migrate() -> None:
    """Create or upgrade the database schema."""
    from kb_registry.migrate import upgrade

    upgrade(get_settings().database_url)
    typer.echo("database is up to date")


@app.command()
def serve(host: str = typer.Option("127.0.0.1", "--host"), port: int = typer.Option(8080, "--port")) -> None:
    """Run the HTTP API."""
    import uvicorn

    from kb_registry.api import create_app

    uvicorn.run(create_app(get_settings()), host=host, port=port)


def main() -> None:
    app()
