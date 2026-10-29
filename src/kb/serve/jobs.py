"""Background jobs for slow work (analyse, ask, push, pull). They live in the server process, so a
renderer reload or a switch of screens does not lose them."""

from __future__ import annotations

import logging
import threading
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import HTTPException

from kb.errors import KbError, LLMUnavailable

ACTIVE = ("queued", "running")
ASK_KIND = "ask"


@dataclass
class Job:
    id: str
    kind: str
    key: str | None
    state: str = "queued"
    step: str = ""
    progress: float = 0.0
    result: Any = None
    error: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    scope: str | None = None
    exclusive: bool = False

    def to_dict(self) -> dict:
        d = {k: getattr(self, k) for k in ("id", "kind", "key", "state", "step", "progress", "result", "error",
                                           "created_at")}
        runner = getattr(self, "runner", None)
        d["queue_position"] = runner.queue_position(self) if runner else None
        return d


def branch_scope(project: str, branch: str) -> str:
    return f"{project}@{branch}"


class BranchBusy(Exception):
    """A writer (analyse/pull) and readers (ask/push/views) would overlap on one branch's pack."""


class JobConflict(Exception):
    def __init__(self, job: Job) -> None:
        super().__init__(f"already running: {job.id}")
        self.job = job


class Reporter:
    def __init__(self, job: Job) -> None:
        self.job = job

    def step(self, text: str, progress: float | None = None) -> None:
        self.job.step = text
        if progress is not None:
            self.job.progress = max(0.0, min(1.0, progress))


def error_message(exc: BaseException) -> str:
    if isinstance(exc, LLMUnavailable):
        return f"Claude Code is not available: {exc}"
    if isinstance(exc, KbError):
        return str(exc)
    return f"unexpected error: {type(exc).__name__}: {exc}"


class JobRunner:
    def __init__(self, max_workers: int = 2, log_dir: Path | None = None, ask_workers: int = 2) -> None:
        # Questions get their own pool so an hour-long analysis never makes a question wait.
        self.pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="kb-job")
        self.ask_pool = ThreadPoolExecutor(max_workers=ask_workers, thread_name_prefix="kb-ask")
        self.jobs: dict[str, Job] = {}
        self.lock = threading.Lock()
        self.log = logging.getLogger("kb.serve")
        if log_dir is not None:
            Path(log_dir).mkdir(parents=True, exist_ok=True)
            h = logging.FileHandler(Path(log_dir) / "serve.log")
            h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
            self.log.addHandler(h)

    def submit(self, kind: str, fn: Callable[[Reporter], Any], key: str | None = None,
               scope: str | None = None, exclusive: bool = False) -> Job:
        """`scope` names the branch pack a job touches: an `exclusive` job (rewrites the pack) never
        overlaps any other job on that scope; shared jobs run together but not beside an exclusive one."""
        with self.lock:
            if key is not None:
                for j in self.jobs.values():
                    if j.key == key and j.state in ACTIVE:
                        raise JobConflict(j)
            if scope is not None:
                self._check_scope(scope, exclusive)
            job = Job(id=uuid.uuid4().hex[:12], kind=kind, key=key)
            job.scope, job.exclusive = scope, exclusive
            job.runner = self
            self.jobs[job.id] = job
        (self.ask_pool if kind == ASK_KIND else self.pool).submit(self._run, job, fn)
        return job

    def queue_position(self, job: "Job") -> int | None:
        """1-based place in line among queued jobs of the same pool, or None once running."""
        if job.state != "queued":
            return None
        same = lambda j: (j.kind == ASK_KIND) == (job.kind == ASK_KIND)  # noqa: E731
        ahead = [j for j in list(self.jobs.values()) if j.state == "queued" and same(j) and j.created_at < job.created_at]
        return len(ahead) + 1

    def _run(self, job: Job, fn: Callable[[Reporter], Any]) -> None:
        job.state = "running"
        try:
            job.result = fn(Reporter(job))
            job.progress = 1.0
            job.state = "done"
        except BaseException as e:  # noqa: BLE001 — every failure must reach the UI
            job.error = error_message(e)
            if not isinstance(e, KbError):
                self.log.error("job %s (%s) failed:\n%s", job.id, job.kind, traceback.format_exc())
            job.state = "failed"

    def _check_scope(self, scope: str, exclusive: bool) -> None:
        branch = scope.split("@", 1)[-1]
        for j in self.jobs.values():
            if j.state not in ACTIVE or j.scope != scope:
                continue
            if j.exclusive:
                raise BranchBusy(f"{branch} is being updated (an analysis or pull is running); try again when it finishes")
            if exclusive:
                raise BranchBusy(f"{branch} has a question or upload still running; try again when it finishes")

    def check_readable(self, scope: str) -> None:
        """Raise BranchBusy if an exclusive job is rewriting this branch's pack."""
        with self.lock:
            self._check_scope(scope, exclusive=False)

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def list(self, kind: str | None = None, active_only: bool = False) -> list[Job]:
        out = [j for j in self.jobs.values() if (kind is None or j.kind == kind)
               and (not active_only or j.state in ACTIVE)]
        return sorted(out, key=lambda j: j.created_at)


def submit_or_409(runner: JobRunner, kind: str, fn, key: str | None = None,
                  scope: str | None = None, exclusive: bool = False) -> Job:
    try:
        return runner.submit(kind, fn, key, scope=scope, exclusive=exclusive)
    except BranchBusy as e:
        raise HTTPException(409, detail=str(e))
    except JobConflict as e:
        raise HTTPException(409, detail={"detail": "already running", "job_id": e.job.id})
