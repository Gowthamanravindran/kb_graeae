"""Who is calling, and may they do this? Static API tokens (hashed at rest); OIDC added later."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from kb_registry.models import ROLES, ApiToken, Membership, Project, User


def new_token() -> str:
    return "kbr_" + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def get_session(request: Request) -> Iterator[Session]:
    with request.app.state.sessionmaker() as s:
        yield s


def _bearer(request: Request) -> str | None:
    h = request.headers.get("authorization", "")
    return h[7:].strip() if h.lower().startswith("bearer ") else None


def current_user(request: Request, s: Session = Depends(get_session)) -> User:
    token = _bearer(request)
    if not token:
        raise HTTPException(401, "not logged in: run kb login <registry-url>")
    user = authenticate(request, s, token)
    if user is None:
        raise HTTPException(401, "invalid or expired credentials: run kb login <registry-url>")
    return user


def authenticate(request: Request, s: Session, token: str) -> User | None:
    row = s.query(ApiToken).filter_by(token_hash=hash_token(token)).one_or_none()
    if row is None:
        return None
    row.last_used_at = datetime.now(timezone.utc)
    s.commit()
    return s.get(User, row.user_id)


def role_of(s: Session, user: User, project: Project) -> str | None:
    m = s.get(Membership, (user.id, project.id))
    return m.role if m else None


def require_role(s: Session, user: User, project_name: str, role: str) -> Project:
    """404 if unknown or not a member (names are not leaked), 403 if the role is too low."""
    p = s.query(Project).filter_by(name=project_name).one_or_none()
    if p is None:
        raise HTTPException(404, f"project not found: {project_name}")
    if user.is_admin:
        return p
    have = role_of(s, user, p)
    if have is None:
        raise HTTPException(404, f"project not found: {project_name}")
    if ROLES.index(have) < ROLES.index(role):
        raise HTTPException(403, f"your role on {project_name} is {have}; this needs {role}")
    return p
