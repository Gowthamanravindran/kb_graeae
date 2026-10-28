"""Who is calling, and may they do this? Static API tokens (hashed at rest); OIDC added later."""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Iterator

from fastapi import Depends, HTTPException, Request
from sqlalchemy import func
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
    if user is None or user.disabled:
        raise HTTPException(401, "invalid or expired credentials: run kb login <registry-url>")
    return user


def signing_key(app, token: str):
    """Public key for a JWT from the issuer's JWKS (discovered once, cached on the app)."""
    import httpx
    import jwt

    client = getattr(app.state, "jwk_client", None)
    if client is None:
        issuer = app.state.settings.oidc_issuer.rstrip("/")
        meta = httpx.get(f"{issuer}/.well-known/openid-configuration", timeout=10).json()
        client = app.state.jwk_client = jwt.PyJWKClient(meta["jwks_uri"], cache_keys=True)
    return client.get_signing_key_from_jwt(token).key


def _oidc_user(request: Request, s: Session, token: str) -> User | None:
    import jwt

    st = request.app.state.settings
    try:
        claims = jwt.decode(token, signing_key(request.app, token), algorithms=["RS256", "ES256"],
                            audience=st.oidc_audience, issuer=st.oidc_issuer,
                            options={"require": ["exp", "sub", "iss"]})
    except Exception:  # bad signature, audience, expiry, unreachable JWKS…: all mean "not authenticated"
        return None
    sub, email = claims.get("sub"), claims.get("email")
    if not sub or not email:
        return None
    user = s.query(User).filter_by(oidc_sub=sub).one_or_none()
    if user is not None:
        return user
    existing = s.query(User).filter(func.lower(User.email) == email.lower()).one_or_none()
    if existing is not None:
        # Link an existing account only on a verified email, and never re-link one already tied to
        # another identity: otherwise an IdP that lets users pick their email hands out admin accounts.
        if claims.get("email_verified") is not True or existing.oidc_sub is not None:
            return None
        existing.oidc_sub = sub
        s.commit()
        return existing
    user = User(email=email, name=claims.get("name", ""), oidc_sub=sub)
    s.add(user)
    s.commit()
    return user


def authenticate(request: Request, s: Session, token: str) -> User | None:
    if request.app.state.settings.oidc_issuer and token.count(".") == 2:
        return _oidc_user(request, s, token)
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
