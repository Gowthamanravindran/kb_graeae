"""kb serve: the desktop app's local API over the stage 1-2 engine."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kb import __version__
from kb.errors import KbError, LLMUnavailable, RegistryUnavailable
from kb.serve.security import TokenHostMiddleware
from kb.serve.workspace import Workspace


def _default_provider():
    from kb.llm.claude_code import ClaudeCodeProvider

    return ClaudeCodeProvider()


def _provider_from_env() -> Callable | None:
    spec = os.environ.get("KB_SERVE_PROVIDER")  # testing hook: "module:function"
    if not spec:
        return None
    import importlib

    mod, fn = spec.split(":", 1)
    return getattr(importlib.import_module(mod), fn)


@dataclass
class ServeSettings:
    kb_home: Path
    token: str
    provider_factory: Callable | None = None
    client_factory: Callable | None = None
    _provider: object | None = field(default=None, repr=False)
    _claude: tuple[float, dict] | None = field(default=None, repr=False)

    def provider(self):
        if self._provider is None:
            factory = self.provider_factory or _provider_from_env() or _default_provider
            self._provider = factory()
        return self._provider

    def client(self, cfg=None, registry: str | None = None):
        from kb.sync import client as sc

        if self.client_factory is not None:
            return self.client_factory(cfg, registry)
        return sc.client_for(cfg, registry, kb_home=self.kb_home)

    def store(self):
        from kb.packs.store import PackStore

        return PackStore(self.kb_home)

    def claude_status(self) -> dict:
        now = time.monotonic()
        if self._claude and now - self._claude[0] < 60:
            return self._claude[1]
        try:
            self.provider().check()
            st = {"available": True, "reason": None}
        except LLMUnavailable as e:
            st = {"available": False, "reason": str(e)}
        self._claude = (now, st)
        return st


class ProjectIn(BaseModel):
    config_path: str


class LoginIn(BaseModel):
    registry: str
    token: str


def create_app(settings: ServeSettings) -> FastAPI:
    app = FastAPI(title="kb serve", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TokenHostMiddleware, token=settings.token)
    app.state.settings = settings
    ws = Workspace(settings.kb_home)
    app.state.workspace = ws

    @app.exception_handler(KbError)
    async def kb_error(request: Request, exc: KbError):
        if isinstance(exc, LLMUnavailable):
            return JSONResponse({"detail": f"Claude Code is not available: {exc}"}, status_code=503)
        if isinstance(exc, RegistryUnavailable):
            return JSONResponse({"detail": str(exc)}, status_code=503)
        status = getattr(exc, "status", None)
        return JSONResponse({"detail": str(exc)}, status_code=status if isinstance(status, int) and 400 <= status < 500 else 400)

    @app.get("/api/status")
    def status() -> dict:
        return {"version": __version__, "claude": settings.claude_status(),
                "langfuse": {"configured": bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))}}

    @app.get("/api/projects")
    def projects() -> list[dict]:
        return ws.projects()

    @app.post("/api/projects")
    def add_project(body: ProjectIn) -> dict:
        return ws.add(Path(body.config_path))

    @app.delete("/api/projects/{name}")
    def remove_project(name: str) -> dict:
        ws.remove(name)
        return {"ok": True}

    @app.get("/api/projects/{name}/registry")
    def registry_status(name: str) -> dict:
        from kb.sync.credentials import get_token

        cfg = ws.config(name)
        out = {"url": cfg.registry, "logged_in": False, "email": None, "reachable": False, "sso": False}
        if not cfg.registry:
            return out
        out["logged_in"] = get_token(settings.kb_home, cfg.registry) is not None
        try:
            c = settings.client(cfg)
            out["sso"] = bool(c.auth_config().get("oidc"))
            out["reachable"] = True
            if out["logged_in"]:
                out["email"] = c.me().get("email")
        except KbError:
            pass
        return out

    @app.post("/api/login")
    def login(body: LoginIn) -> dict:
        from kb.sync import client as sc
        from kb.sync.credentials import norm_url, save_token

        url = norm_url(body.registry)
        me = sc.RegistryClient(url, body.token, http=sc.make_http(url)).me()
        save_token(settings.kb_home, url, body.token, "static")
        return {"email": me["email"]}

    return app
