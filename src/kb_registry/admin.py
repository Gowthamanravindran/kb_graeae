"""Server-side administration: projects, users, grants and tokens."""

from __future__ import annotations

from pathlib import Path

import typer

from kb_registry.auth import hash_token, new_token
from kb_registry.db import make_engine, make_sessionmaker
from kb_registry.models import ROLES, ApiToken, Membership, Project, User
from kb_registry.settings import get_settings

app = typer.Typer(help="Manage projects, users and access.", no_args_is_help=True)


def _session():
    return make_sessionmaker(make_engine(get_settings().database_url))()


def _fail(msg: str) -> None:
    typer.echo(f"Error: {msg}", err=True)
    raise typer.Exit(1)


def _user(s, email: str) -> User:
    u = s.query(User).filter_by(email=email).one_or_none()
    if u is None:
        _fail(f"no user {email}")
    return u


def _issue(s, user: User, name: str) -> str:
    t = new_token()
    s.add(ApiToken(user_id=user.id, name=name, token_hash=hash_token(t)))
    s.commit()
    return t


@app.command("create-project")
def create_project(name: str, config: Path = typer.Option(None, "--config", help="kb.yaml to store for pullers")):
    with _session() as s:
        if s.query(Project).filter_by(name=name).one_or_none():
            _fail(f"project {name} already exists")
        s.add(Project(name=name, config_yaml=config.read_text() if config else ""))
        s.commit()
    typer.echo(f"created project {name}")


@app.command("create-user")
def create_user(email: str, name: str = typer.Option("", "--name"), admin: bool = typer.Option(False, "--admin")):
    """Create a user and print an API token (shown once)."""
    with _session() as s:
        if s.query(User).filter_by(email=email).one_or_none():
            _fail(f"user {email} already exists (use `token` for a new token)")
        u = User(email=email, name=name, is_admin=admin)
        s.add(u)
        s.flush()
        token = _issue(s, u, "initial")
    typer.echo(f"created {'admin ' if admin else ''}user {email}; API token (shown once):")
    typer.echo(token)


@app.command()
def grant(email: str, project: str, role: str):
    """Give EMAIL a ROLE (viewer|contributor|maintainer) on PROJECT."""
    if role not in ROLES:
        _fail(f"role must be one of {', '.join(ROLES)}")
    with _session() as s:
        u = _user(s, email)
        p = s.query(Project).filter_by(name=project).one_or_none()
        if p is None:
            _fail(f"no project {project}")
        s.merge(Membership(user_id=u.id, project_id=p.id, role=role))
        s.commit()
    typer.echo(f"{email} is now {role} on {project}")


@app.command()
def token(email: str, name: str = typer.Option("", "--name")):
    """Issue an additional API token for EMAIL (shown once)."""
    with _session() as s:
        t = _issue(s, _user(s, email), name)
    typer.echo(f"new API token for {email} (shown once):")
    typer.echo(t)


@app.command()
def revoke(email: str, project: str):
    """Remove EMAIL's role on PROJECT."""
    with _session() as s:
        u = _user(s, email)
        p = s.query(Project).filter_by(name=project).one_or_none()
        if p is None:
            _fail(f"no project {project}")
        m = s.get(Membership, (u.id, p.id))
        if m is None:
            _fail(f"{email} has no role on {project}")
        s.delete(m)
        s.commit()
    typer.echo(f"{email} no longer has access to {project}")


@app.command("revoke-tokens")
def revoke_tokens(email: str):
    """Delete every API token EMAIL has (they must get a new one to use the registry)."""
    with _session() as s:
        u = _user(s, email)
        n = s.query(ApiToken).filter_by(user_id=u.id).delete()
        s.commit()
    typer.echo(f"revoked {n} token(s) for {email}")


@app.command("disable-user")
def disable_user(email: str):
    """Block EMAIL entirely: tokens and roles removed, single sign-on refused. Their published answers stay."""
    with _session() as s:
        u = _user(s, email)
        s.query(ApiToken).filter_by(user_id=u.id).delete()
        s.query(Membership).filter_by(user_id=u.id).delete()
        u.disabled = True
        u.is_admin = False
        s.commit()
    typer.echo(f"disabled {email}")
