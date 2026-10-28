"""Write side of packs: push a base (new version), publish overlay items, fetch the latest."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kb.packs.model import Manifest, commits_key
from kb_registry.auth import current_user, get_session, require_role
from kb_registry.models import BaseFile, BaseRow, Blob, User
from kb_registry.paths import is_allowed_path

router = APIRouter()


class BaseIn(BaseModel):
    manifest: dict
    files: dict[str, str]


@router.post("/projects/{p}/branches/{b:path}/bases", status_code=201)
def create_base(p: str, b: str, body: BaseIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    proj = require_role(s, user, p, "maintainer")
    try:
        m = Manifest.model_validate(body.manifest)
    except ValidationError as e:
        raise HTTPException(400, f"invalid manifest: {e}")
    if m.project != p or m.branch != b:
        raise HTTPException(400, f"manifest is for {m.project}@{m.branch}, not {p}@{b}")
    bad = [path for path in body.files if path == "manifest.json" or not is_allowed_path(path)]
    if bad:
        raise HTTPException(400, f"files not allowed in a pack base: {', '.join(sorted(bad))}")
    hashes = set(body.files.values())
    known = {h for (h,) in s.query(Blob.hash).filter(Blob.hash.in_(hashes))} if hashes else set()
    if hashes - known:
        raise HTTPException(400, f"unknown blobs (upload them first): {', '.join(sorted(hashes - known))}")

    key = commits_key(m.commits)
    for attempt in range(2):
        dup = s.query(BaseRow).filter_by(project_id=proj.id, branch=b, commits_key=key).one_or_none()
        if dup is not None:
            return JSONResponse(status_code=409, content={"detail": "already exists — pull instead", "version": dup.version})
        last = s.query(func.max(BaseRow.version)).filter_by(project_id=proj.id, branch=b).scalar()
        version = (last or 0) + 1
        manifest = m.model_dump(mode="json") | {"version": version, "parent": last}
        row = BaseRow(project_id=proj.id, branch=b, commits_key=key, commits=m.commits, manifest=manifest,
                      version=version, parent=last, created_by=user.id)
        s.add(row)
        try:
            s.flush()
            s.add_all(BaseFile(base_id=row.id, path=path, blob_hash=h) for path, h in body.files.items())
            s.commit()
            return {"version": version, "parent": last, "base_id": row.id}
        except IntegrityError:
            s.rollback()  # a concurrent push took this version or commit set; look again once
    raise HTTPException(409, "concurrent push; try again")
