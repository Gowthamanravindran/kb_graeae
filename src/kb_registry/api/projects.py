"""Read side: who am I, which projects, branches and versions; project config."""

from __future__ import annotations

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from kb_registry.auth import current_user, get_session, require_role, role_of
from kb_registry.models import BaseRow, Membership, OverlayItem, Project, User

router = APIRouter()


@router.get("/auth/config")
def auth_config(request: Request) -> dict:
    st = request.app.state.settings
    if not st.oidc_issuer:
        return {"oidc": None}
    return {"oidc": {"issuer": st.oidc_issuer, "client_id": st.oidc_client_id, "audience": st.oidc_audience}}


@router.get("/me")
def me(user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict:
    rows = s.query(Project.name, Membership.role).join(Membership, Membership.project_id == Project.id) \
        .filter(Membership.user_id == user.id).all()
    return {"email": user.email, "is_admin": user.is_admin, "projects": {n: r for n, r in sorted(rows)}}


@router.get("/projects")
def projects(user: User = Depends(current_user), s: Session = Depends(get_session)) -> list[dict]:
    if user.is_admin:
        return [{"name": p.name, "role": role_of(s, user, p) or "admin"} for p in s.query(Project).order_by(Project.name)]
    rows = s.query(Project.name, Membership.role).join(Membership, Membership.project_id == Project.id) \
        .filter(Membership.user_id == user.id).order_by(Project.name).all()
    return [{"name": n, "role": r} for n, r in rows]


@router.get("/projects/{p}")
def project(p: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict:
    proj = require_role(s, user, p, "viewer")
    return {"name": proj.name, "config_yaml": proj.config_yaml}


class ConfigIn(BaseModel):
    config_yaml: str


def validate_config(p: str, text: str, seed: bool) -> None:
    from kb.config import ProjectConfig

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        raise HTTPException(400, f"config is not valid YAML: {e}")
    if not isinstance(data, dict) or data.get("project") != p:
        raise HTTPException(400, f"config must be a mapping whose project is '{p}'")
    if seed:
        return
    try:
        ProjectConfig.model_validate(data)
    except ValueError as e:  # pydantic ValidationError is a ValueError
        raise HTTPException(400, f"invalid config: {e}") from e


@router.put("/projects/{p}/config")
def put_config(p: str, body: ConfigIn, seed: bool = False, user: User = Depends(current_user),
               s: Session = Depends(get_session)) -> dict:
    proj = require_role(s, user, p, "maintainer")
    validate_config(p, body.config_yaml, seed)
    proj.config_yaml = body.config_yaml
    s.commit()
    return {"ok": True}


def _email(s: Session, user_id: int) -> str:
    u = s.get(User, user_id)
    return u.email if u else ""


@router.get("/projects/{p}/branches")
def branches(p: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> list[dict]:
    proj = require_role(s, user, p, "viewer")
    latest = dict(s.query(BaseRow.branch, func.max(BaseRow.version)).filter_by(project_id=proj.id)
                  .group_by(BaseRow.branch).all())
    counts = dict(s.query(OverlayItem.branch, func.count()).filter_by(project_id=proj.id)
                  .group_by(OverlayItem.branch).all())
    out = []
    for branch in sorted(latest):
        b = s.query(BaseRow).filter_by(project_id=proj.id, branch=branch, version=latest[branch]).one()
        out.append({"branch": branch, "latest_version": b.version, "commits": b.commits,
                    "created_by": _email(s, b.created_by), "created_at": b.created_at.isoformat(),
                    "overlay_count": counts.get(branch, 0)})
    return out


@router.get("/projects/{p}/branches/{b:path}/versions")
def versions(p: str, b: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> list[dict]:
    proj = require_role(s, user, p, "viewer")
    rows = s.query(BaseRow).filter_by(project_id=proj.id, branch=b).order_by(BaseRow.version.desc()).all()
    if not rows:
        raise HTTPException(404, f"no analysed base for {p}@{b}")
    return [{"version": r.version, "parent": r.parent, "commits": r.commits, "created_by": _email(s, r.created_by),
             "created_at": r.created_at.isoformat()} for r in rows]
