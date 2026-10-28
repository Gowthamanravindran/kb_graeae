"""Only the desktop app may talk to kb serve: a per-launch secret on every request, and a local Host
header (blocks DNS-rebinding pages that resolve their own name to 127.0.0.1)."""

from __future__ import annotations

import hmac
import json

from starlette.types import ASGIApp, Receive, Scope, Send

LOCAL_HOSTS = {"127.0.0.1", "localhost"}


async def _deny(send: Send, status: int, detail: str) -> None:
    body = json.dumps({"detail": detail}).encode()
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})


class TokenHostMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self.app = app
        self.token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        headers = dict(scope.get("headers") or [])
        host = headers.get(b"host", b"").decode("latin-1")
        hostname = host.rsplit(":", 1)[0] if not host.startswith("[") else host
        if hostname not in LOCAL_HOSTS:
            await _deny(send, 421, "misdirected request")
            return
        if not hmac.compare_digest(headers.get(b"x-kb-token", b""), self.token):
            await _deny(send, 401, "missing or wrong X-KB-Token")
            return
        await self.app(scope, receive, send)
