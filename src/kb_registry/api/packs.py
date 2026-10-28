"""Write side of packs: push a base (new version), publish overlay items, fetch the latest."""

from __future__ import annotations

import hashlib
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kb.packs.model import Manifest, commits_key
from kb_registry.auth import current_user, get_session, require_role
from kb_registry.models import BaseFile, BaseRow, Blob, OverlayItem, User
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


# ---------- overlay ----------

MAX_ITEMS = 500
MAX_ITEM_BYTES = 256 * 1024


class ItemIn(BaseModel):
    id: str
    kind: str
    payload: dict


class OverlayIn(BaseModel):
    items: list[ItemIn]


def _note_id(module_id: str, text: str) -> str:
    return f"note:{module_id}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def _validate_item(it: ItemIn) -> dict:
    from kb.packs.model import Flow, QAItem, Suggestion

    models = {"qa": QAItem, "flow": Flow, "suggestion": Suggestion}
    if it.kind in models:
        try:
            obj = models[it.kind].model_validate(it.payload)
        except ValidationError as e:
            raise HTTPException(400, f"item {it.id}: invalid {it.kind}: {e}")
        if obj.id != it.id:
            raise HTTPException(400, f"item {it.id}: payload id is {obj.id}")
        data = obj.model_dump(mode="json")
        if "visibility" in data:
            data["visibility"] = "published"
        return data
    if it.kind == "module_note":
        mid, text = it.payload.get("module_id"), it.payload.get("text")
        if not isinstance(mid, str) or not isinstance(text, str) or not text.strip():
            raise HTTPException(400, f"item {it.id}: module_note needs module_id and text")
        if it.id != _note_id(mid, text):
            raise HTTPException(400, f"item {it.id}: module_note id must be {_note_id(mid, text)}")
        return {"module_id": mid, "text": text}
    raise HTTPException(400, f"item {it.id}: unknown kind {it.kind}")


def _sha(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def _latest_base(s: Session, project_id: int, b: str) -> BaseRow | None:
    return s.query(BaseRow).filter_by(project_id=project_id, branch=b).order_by(BaseRow.version.desc()).first()


@router.post("/projects/{p}/branches/{b:path}/overlay")
def publish(p: str, b: str, body: OverlayIn, user: User = Depends(current_user), s: Session = Depends(get_session)):
    proj = require_role(s, user, p, "contributor")
    if len(body.items) > MAX_ITEMS:
        raise HTTPException(413, f"at most {MAX_ITEMS} items per request")
    base = _latest_base(s, proj.id, b)
    if base is None:
        raise HTTPException(404, f"no analysed base for {p}@{b} — push the analysis first")
    prepared = []
    for it in body.items:
        if len(json.dumps(it.payload)) > MAX_ITEM_BYTES:
            raise HTTPException(413, f"item {it.id} is larger than {MAX_ITEM_BYTES // 1024} KB")
        data = _validate_item(it)
        prepared.append((it, data, _sha(data)))
    added, existing, conflicts = [], [], []
    for it, data, sha in prepared:
        row = s.get(OverlayItem, (proj.id, b, it.id))
        if row is None:
            continue
        (existing if row.payload_sha == sha else conflicts).append(it.id)
    if conflicts:
        raise HTTPException(409, f"items already published with different content: {', '.join(conflicts)}")
    for it, data, sha in prepared:
        if it.id in existing or it.id in added:
            continue
        s.add(OverlayItem(project_id=proj.id, branch=b, item_id=it.id, kind=it.kind, payload=data, payload_sha=sha,
                          author_id=user.id, base_id=base.id))
        added.append(it.id)
    s.commit()
    return {"added": added, "existing": existing}


@router.get("/projects/{p}/branches/{b:path}/latest")
def latest(p: str, b: str, user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict:
    proj = require_role(s, user, p, "viewer")
    base = _latest_base(s, proj.id, b)
    if base is None:
        raise HTTPException(404, f"no analysed base for {p}@{b}")
    files = {f.path: f.blob_hash for f in s.query(BaseFile).filter_by(base_id=base.id)}
    emails = {u.id: u.email for u in s.query(User)}
    items = s.query(OverlayItem).filter_by(project_id=proj.id, branch=b).order_by(OverlayItem.published_at).all()
    return {"version": base.version, "parent": base.parent, "base_id": base.id, "manifest": base.manifest,
            "files": files,
            "overlay": [{"id": i.item_id, "kind": i.kind, "payload": i.payload, "author": emails.get(i.author_id, ""),
                         "published_at": i.published_at.isoformat()} for i in items]}
