"""Reject oversized request bodies before routing, auth or JSON parsing."""

from __future__ import annotations

import re

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from kb_registry.storage import MAX_BLOB

JSON_LIMIT = 8 * 2**20
BLOB_LIMIT = MAX_BLOB + 10 * 2**20  # compressed upload; decompressed size is capped separately
_BLOB_PUT = re.compile(r"^/api/v1/blobs/[^/]+$")


async def _reject(send: Send, limit: int) -> None:
    body = ('{"detail": "request body larger than %d MB"}' % (limit // 2**20)).encode()
    await send({"type": "http.response.start", "status": 413,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class BodyLimit:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in ("GET", "HEAD", "OPTIONS"):
            await self.app(scope, receive, send)
            return
        limit = BLOB_LIMIT if scope["method"] == "PUT" and _BLOB_PUT.match(scope["path"]) else JSON_LIMIT
        declared = dict(scope.get("headers") or []).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _reject(send, limit)
            return
        chunks: list[bytes] = []
        seen = 0
        while True:  # read (bounded) before the app sees anything, then replay
            msg = await receive()
            if msg["type"] != "http.request":
                await self.app(scope, _replay([], msg), send)
                return
            seen += len(msg.get("body", b""))
            if seen > limit:
                await _reject(send, limit)
                return
            chunks.append(msg.get("body", b""))
            if not msg.get("more_body", False):
                break
        await self.app(scope, _replay(chunks, None), send)


def _replay(chunks: list[bytes], tail: Message | None) -> Receive:
    sent = False

    async def receive() -> Message:
        nonlocal sent
        if not sent:
            sent = True
            if tail is not None:
                return tail
            return {"type": "http.request", "body": b"".join(chunks), "more_body": False}
        return {"type": "http.disconnect"}
    return receive
