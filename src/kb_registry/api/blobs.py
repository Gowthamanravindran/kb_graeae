"""Content-addressed blobs: ask what is missing, upload it (verified), download what you may see."""

from __future__ import annotations

import re

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.concurrency import run_in_threadpool
from sqlalchemy.exc import IntegrityError
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from kb_registry.auth import current_user, get_session
from kb_registry.models import BaseFile, BaseRow, Blob, Membership, User
from kb_registry.storage import verify_and_size

router = APIRouter()
_HASH = re.compile(r"^[0-9a-f]{64}$")


def _require_uploader(s: Session, user: User) -> None:
    if user.is_admin:
        return
    if not s.query(Membership).filter_by(user_id=user.id, role="maintainer").first():
        raise HTTPException(403, "uploading needs the maintainer role on a project")


class MissingIn(BaseModel):
    hashes: list[str] = Field(max_length=5000)


@router.post("/blobs/missing")
def missing(body: MissingIn, user: User = Depends(current_user), s: Session = Depends(get_session)) -> dict:
    _require_uploader(s, user)
    have = {h for (h,) in s.query(Blob.hash).filter(Blob.hash.in_(body.hashes))} if body.hashes else set()
    return {"missing": [h for h in dict.fromkeys(body.hashes) if h not in have]}


def _store_blob(request: Request, h: str, data: bytes) -> tuple[int, int]:
    """Blocking part of an upload (decompress + hash, object store write, DB) — runs in a worker thread."""
    with request.app.state.sessionmaker() as s:
        existing = s.get(Blob, h)
        if existing is not None:
            return 200, existing.size
        try:
            size = verify_and_size(h, data)
        except ValueError as e:
            raise HTTPException(400, str(e))
        request.app.state.store.put(h, data)
        s.add(Blob(hash=h, size=size))
        try:
            s.commit()
        except IntegrityError:  # a concurrent upload of the same blob won
            s.rollback()
            return 200, size
        return 201, size


@router.put("/blobs/{h}")
async def put_blob(h: str, request: Request, response: Response, user: User = Depends(current_user),
                   s: Session = Depends(get_session)) -> dict:
    if not _HASH.match(h):
        raise HTTPException(400, "blob id must be a lowercase sha256 hex digest")
    await run_in_threadpool(_require_uploader, s, user)
    data = await request.body()  # bounded by the BodyLimit middleware
    status, size = await run_in_threadpool(_store_blob, request, h, data)
    response.status_code = status
    return {"hash": h, "size": size}


@router.get("/blobs/{h}")
def get_blob(h: str, request: Request, user: User = Depends(current_user), s: Session = Depends(get_session)):
    q = s.query(BaseRow.project_id).join(BaseFile, BaseFile.base_id == BaseRow.id).filter(BaseFile.blob_hash == h)
    if not user.is_admin:
        q = q.join(Membership, (Membership.project_id == BaseRow.project_id) & (Membership.user_id == user.id))
    if q.first() is None or not _HASH.match(h):
        raise HTTPException(404, "blob not found")
    return Response(request.app.state.store.get(h), media_type="application/zstd")
