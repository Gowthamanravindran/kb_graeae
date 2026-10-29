"""kb serve: the desktop app's local API over the stage 1-2 engine."""

from __future__ import annotations

import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from kb import __version__
from kb.errors import KbError, LLMUnavailable, RegistryUnavailable
from kb.serve.jobs import BranchBusy, JobRunner, branch_scope, submit_or_409
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
    ui_dir: Path | None = None
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

    def claude_status(self, fresh: bool = False) -> dict:
        now = time.monotonic()
        if self._claude and not fresh and now - self._claude[0] < 60:
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


SETTINGS_KEYS = ("LANGFUSE_PUBLIC_KEY", "LANGFUSE_SECRET_KEY", "LANGFUSE_HOST", "GITHUB_TOKEN")
PLAIN_SETTINGS = {"LANGFUSE_HOST"}


class SettingsIn(BaseModel):
    values: dict[str, str | None]


class LogoutIn(BaseModel):
    registry: str


class LoginIn(BaseModel):
    registry: str
    token: str


class SsoIn(BaseModel):
    registry: str


class AnalyzeIn(BaseModel):
    resume: bool = False


class AskIn(BaseModel):
    question: str
    from_qa: str | None = None
    no_reuse: bool = False


class PublishIn(BaseModel):
    qa_ids: list[str] = []
    notes: bool = False


class FeedbackIn(BaseModel):
    trace_id: str
    value: int


class PullIn(BaseModel):
    force: bool = False


def create_app(settings: ServeSettings) -> FastAPI:
    app = FastAPI(title="kb serve", version=__version__, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TokenHostMiddleware, token=settings.token)
    app.state.settings = settings
    ws = Workspace(settings.kb_home)
    app.state.workspace = ws
    jobs = JobRunner(log_dir=Path(settings.kb_home) / "logs")
    app.state.jobs = jobs

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        body = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
        return JSONResponse(body, status_code=exc.status_code)

    @app.exception_handler(KbError)
    async def kb_error(request: Request, exc: KbError):
        if isinstance(exc, LLMUnavailable):
            return JSONResponse({"detail": f"Claude Code is not available: {exc}"}, status_code=503)
        if isinstance(exc, RegistryUnavailable):
            return JSONResponse({"detail": str(exc)}, status_code=503)
        status = getattr(exc, "status", None)
        return JSONResponse({"detail": str(exc)}, status_code=status if isinstance(status, int) and 400 <= status < 500 else 400)

    @app.get("/api/status")
    def status(fresh: int = 0) -> dict:
        configured = bool(os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY"))
        host = (os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL") or "").rstrip("/") or None
        return {"version": __version__, "claude": settings.claude_status(bool(fresh)),
                "langfuse": {"configured": configured, "host": host if configured else None}}

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

    @app.post("/api/logout")
    def logout(body: LogoutIn) -> dict:
        from kb.sync.credentials import forget_token

        return {"forgotten": forget_token(settings.kb_home, body.registry)}

    # ---------- settings (kb.env) ----------

    def _settings_view() -> dict:
        from kb.envfile import env_source, read_env_file

        path = settings.kb_home / "kb.env"
        filed = read_env_file(path)
        values = {}
        for k in SETTINGS_KEYS:
            src = env_source(path, k)
            val = os.environ.get(k) if src == "environment" else filed.get(k)
            item = {"set": bool(val), "last4": val[-4:] if val else None, "source": src if val else None}
            if k in PLAIN_SETTINGS:
                item["value"] = val
            values[k] = item
        return {"env_path": str(path), "values": values}

    @app.get("/api/settings")
    def get_settings() -> dict:
        return _settings_view()

    @app.put("/api/settings")
    def put_settings(body: SettingsIn) -> dict:
        from kb.envfile import apply_setting, write_env_file

        for k in body.values:
            if k not in SETTINGS_KEYS:
                raise HTTPException(400, f"unknown setting {k}")
        clean = {k: (v.strip() or None) if isinstance(v, str) else None for k, v in body.values.items()}
        write_env_file(settings.kb_home / "kb.env", clean)
        for k, v in clean.items():
            apply_setting(k, v)
        return _settings_view()

    # ---------- branches, analyze, ask, push, pull ----------

    def _branch_key(name: str, branch: str) -> str:
        return f"analyze:{name}@{branch}"

    def _readable(project: str, branch: str) -> None:
        try:
            jobs.check_readable(branch_scope(project, branch))
        except BranchBusy as e:
            raise HTTPException(409, detail=str(e))

    @app.get("/api/projects/{name}/branches")
    def branches(name: str) -> dict:
        from kb.serve.views import branch_rows

        cfg = ws.config(name)
        remote, err = None, None
        if cfg.registry:
            try:
                remote = settings.client(cfg).branches(cfg.project)
            except KbError as e:
                err = str(e)
        active = {j.key.split("@", 1)[1]: j.id for j in jobs.list("analyze", active_only=True)
                  if j.key and j.key.startswith(f"analyze:{cfg.project}@")}
        return {"rows": branch_rows(cfg.project, settings.store(), remote, active), "registry_error": err}


    @app.post("/api/projects/{name}/branches/{branch:path}/analyze")
    def analyze(name: str, branch: str, body: AnalyzeIn) -> dict:
        from kb.analyze import run_analyze
        from kb.cli import _author
        from kb.tracing import make_tracer

        cfg = ws.config(name)

        def run(rep):
            m = run_analyze(cfg, branch, settings.store(), settings.provider(), make_tracer(cfg), resume=body.resume,
                            author=_author(), progress=lambda text, frac: rep.step(text, frac))
            return {"status": m.status, "stats": m.stats, "failed_modules": m.failed_modules}
        return {"job_id": submit_or_409(jobs, "analyze", run, key=_branch_key(cfg.project, branch),
                                          scope=branch_scope(cfg.project, branch), exclusive=True).id}


    def _ask_job(cfg, branch: str, question: str, from_qa=None, no_reuse=False):
        def run(rep):
            from kb.ask import run_ask
            from kb.cli import _author
            from kb.serve.stats import StatsTracer
            from kb.serve.views import ask_payload
            from kb.tracing import make_tracer

            rep.step("Finding the right code", 0.1)
            stats = StatsTracer(make_tracer(cfg))
            t0 = time.monotonic()
            store = settings.store()
            st = run_ask(cfg, branch, question, store, settings.provider(), stats, from_qa=from_qa,
                         author=_author(), no_reuse=no_reuse)
            return ask_payload(st, stats, time.monotonic() - t0, store.load(cfg.project, branch))
        return {"job_id": submit_or_409(jobs, "ask", run, scope=branch_scope(cfg.project, branch)).id}

    @app.post("/api/projects/{name}/branches/{branch:path}/ask")
    def ask(name: str, branch: str, body: AskIn) -> dict:
        cfg = ws.config(name)
        _readable(cfg.project, branch)
        settings.store().load(cfg.project, branch)  # 400 now if there is no pack
        return _ask_job(cfg, branch, body.question, body.from_qa, body.no_reuse)

    app.state.ask_job = _ask_job

    @app.post("/api/projects/{name}/branches/{branch:path}/push")
    def push(name: str, branch: str) -> dict:
        from kb.sync.commands import push_pack

        cfg = ws.config(name)

        def run(rep):
            rep.step("Uploading the analysis", 0.2)
            return push_pack(cfg, branch, settings.store(), settings.client(cfg))
        return {"job_id": submit_or_409(jobs, "push", run, key=f"push:{cfg.project}@{branch}",
                                          scope=branch_scope(cfg.project, branch)).id}


    @app.post("/api/projects/{name}/branches/{branch:path}/pull")
    def pull(name: str, branch: str, body: PullIn) -> dict:
        from kb.sync.commands import pull_pack

        cfg = ws.config(name)

        def run(rep):
            rep.step("Downloading the analysis", 0.2)
            return pull_pack(cfg.project, branch, settings.store(), settings.client(cfg), cfg=cfg, force=body.force)
        return {"job_id": submit_or_409(jobs, "pull", run, key=f"pull:{cfg.project}@{branch}",
                                          scope=branch_scope(cfg.project, branch), exclusive=True).id}

    # ---------- map, memory, publish, retrace, feedback ----------

    @app.get("/api/projects/{name}/branches/{branch:path}/map")
    def map_(name: str, branch: str) -> dict:
        from kb.serve.views import map_view

        cfg = ws.config(name)
        _readable(cfg.project, branch)
        return map_view(cfg, settings.store().load(cfg.project, branch))

    @app.get("/api/projects/{name}/branches/{branch:path}/memory")
    def memory(name: str, branch: str) -> dict:
        from kb.serve.views import memory_view
        from kb.sync.outbox import Outbox

        cfg = ws.config(name)
        _readable(cfg.project, branch)
        queued = {i["id"] for i in Outbox(settings.kb_home).pending(cfg.project, branch)}
        return memory_view(settings.store().load(cfg.project, branch), queued)

    def _qa(cfg, branch: str, qa_id: str):
        _readable(cfg.project, branch)
        pack = settings.store().load(cfg.project, branch)
        qa = next((q for q in pack.qa if q.id == qa_id), None)
        if qa is None:
            raise HTTPException(404, f"no saved answer {qa_id}")
        flow = next((f for f in pack.flows if f.id == qa.flow_id), None)
        return pack, qa, flow

    @app.get("/api/projects/{name}/branches/{branch:path}/qa/{qa_id}")
    def open_qa(name: str, branch: str, qa_id: str) -> dict:
        _, qa, flow = _qa(ws.config(name), branch, qa_id)
        return {**qa.model_dump(mode="json"), "flow": flow.model_dump(mode="json") if flow else None}

    @app.post("/api/projects/{name}/branches/{branch:path}/qa/{qa_id}/retrace")
    def retrace(name: str, branch: str, qa_id: str) -> dict:
        cfg = ws.config(name)
        _, qa, _ = _qa(cfg, branch, qa_id)
        return _ask_job(cfg, branch, qa.question, no_reuse=True)

    @app.post("/api/projects/{name}/branches/{branch:path}/suggestions/{sid}/run")
    def run_suggestion(name: str, branch: str, sid: str) -> dict:
        cfg = ws.config(name)
        _readable(cfg.project, branch)
        pack = settings.store().load(cfg.project, branch)
        sug = next((x for x in pack.suggestions if x.id == sid), None)
        if sug is None:
            raise HTTPException(404, f"no suggestion {sid}")
        return _ask_job(cfg, branch, sug.question)

    def _need_registry(cfg):
        if not cfg.registry:
            raise KbError(f"{cfg.project} has no registry: add `registry:` to its kb.yaml to share")

    @app.post("/api/projects/{name}/branches/{branch:path}/publish")
    def publish(name: str, branch: str, body: PublishIn) -> dict:
        from kb.sync.commands import publish_items

        cfg = ws.config(name)
        _need_registry(cfg)
        _readable(cfg.project, branch)
        return publish_items(cfg, branch, settings.store(), settings.client(cfg), body.qa_ids, body.notes)

    @app.post("/api/projects/{name}/branches/{branch:path}/sync")
    def sync(name: str, branch: str) -> dict:
        from kb.sync.commands import flush_outbox
        from kb.sync.outbox import Outbox

        cfg = ws.config(name)
        _need_registry(cfg)
        warnings: list[str] = []
        sent = flush_outbox(cfg.project, branch, settings.client(cfg), settings.store(), warnings)
        return {"sent": sent, "left": len(Outbox(settings.kb_home).pending(cfg.project, branch)), "warnings": warnings}

    @app.post("/api/feedback")
    def feedback(body: FeedbackIn) -> dict:
        if not (os.environ.get("LANGFUSE_PUBLIC_KEY") and os.environ.get("LANGFUSE_SECRET_KEY")):
            return {"recorded": False}
        from langfuse import Langfuse

        try:
            lf = Langfuse()
            lf.create_score(trace_id=body.trace_id, name="helpful", value=float(1 if body.value else 0))
            lf.flush()
        except Exception as e:  # noqa: BLE001 — any SDK/network failure is reported, never a bare 500
            jobs.log.exception("feedback for trace %s not recorded", body.trace_id)
            raise HTTPException(502, f"Langfuse is unreachable: {e}")
        return {"recorded": True}

    @app.get("/api/projects/{name}/branches/{branch:path}/citation")
    def citation(name: str, branch: str, repo: str, file: str, line: int = 1) -> dict:
        from kb.ask import available_checkouts

        cfg = ws.config(name)
        pack = settings.store().load(cfg.project, branch)
        checkouts, _ = available_checkouts(cfg, branch, settings.store(), pack.manifest.commits)
        co = checkouts.get(repo)
        not_found = HTTPException(404, f"file not found in your checkout of {repo}")
        if co is None or Path(file).is_absolute():
            raise not_found
        root = Path(co.root).resolve()
        target = (root / file).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise not_found
        return {"path": str(target), "line": max(1, line)}

    @app.get("/api/jobs")
    def list_jobs(kind: str | None = None, active: int = 0) -> list[dict]:
        return [j.to_dict() for j in jobs.list(kind, bool(active))]

    @app.get("/api/jobs/{job_id}")
    def get_job(job_id: str) -> dict:
        j = jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "no such job")
        return j.to_dict()


    @app.post("/api/login/sso")
    def login_sso(body: SsoIn) -> dict:
        from kb.sync import client as sc
        from kb.sync import oidc
        from kb.sync.credentials import norm_url, save_token

        url = norm_url(body.registry)

        def run(rep):
            got = oidc.device_login(url, sc.make_http(url), out=lambda text: rep.step(text))
            save_token(settings.kb_home, url, got["token"], "oidc", got.get("refresh_token"), got.get("expires_at"))
            return {"email": settings.client(registry=url).me()["email"]}
        return {"job_id": submit_or_409(jobs, "login", run, key=f"login:{url}").id}

    if settings.ui_dir is not None:  # the desktop renderer, same origin as the API (no CORS needed)
        from fastapi.staticfiles import StaticFiles

        app.mount("/", StaticFiles(directory=str(settings.ui_dir), html=True), name="ui")
    return app
