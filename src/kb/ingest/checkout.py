"""Resolve a configured repo to a folder on disk at a branch, and record its commit."""

from __future__ import annotations

import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from kb.config import RepoConfig
from kb.errors import KbError

SKIP_DIRS = {".git", "node_modules"}


@dataclass(frozen=True)
class Checkout:
    repo: str
    root: Path
    commit: str


def _git(cwd: Path, *args: str) -> str:
    try:
        r = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        raise KbError(f"git {' '.join(args)} failed in {cwd}: {e.stderr.strip()}") from e
    return r.stdout.strip()


def _is_git_repo(path: Path) -> bool:
    """True only when `path` is the top of a git work tree (a copy inside another repo is a plain folder)."""
    r = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=path, capture_output=True, text=True)
    return r.returncode == 0 and Path(r.stdout.strip()).resolve() == Path(path).resolve()


def content_hash(root: Path) -> str:
    h = hashlib.sha256()
    entries = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in filenames:
            p = Path(dirpath) / f
            try:
                st = p.stat()
            except OSError:
                continue
            entries.append((str(p.relative_to(root)), st.st_size, st.st_mtime_ns))
    for rel, size, mtime in sorted(entries):
        h.update(f"{rel}\0{size}\0{mtime}\n".encode())
    return "local-" + h.hexdigest()[:12]


def checkout_repo(repo: RepoConfig, branch: str, work_dir: Path) -> Checkout:
    if repo.url is not None:
        base = Path(work_dir) / repo.name
        if (base / ".git").exists():
            _git(base, "fetch", "--quiet", "origin")
        else:
            base.parent.mkdir(parents=True, exist_ok=True)
            _git(base.parent, "clone", "--quiet", repo.url, str(base))
        _git(base, "checkout", "--quiet", branch)
        if _git(base, "rev-parse", "--abbrev-ref", "HEAD") != "HEAD":
            _git(base, "reset", "--quiet", "--hard", f"origin/{branch}")
        commit = _git(base, "rev-parse", "HEAD")
        root = base / repo.path if repo.path else base
        return Checkout(repo.name, root, commit)

    base = Path(repo.local_path)  # type: ignore[arg-type]
    if not base.is_dir():
        raise KbError(f"repo '{repo.name}': local_path {base} does not exist")
    root = base / repo.path if repo.path else base
    if not root.is_dir():
        raise KbError(f"repo '{repo.name}': {root} does not exist")
    if _is_git_repo(base):
        current = _git(base, "rev-parse", "--abbrev-ref", "HEAD")
        if current != branch:
            raise KbError(
                f"repo '{repo.name}' at {base} is on branch '{current}', not '{branch}'. "
                "Switch it yourself; kb does not change branches in your own checkout."
            )
        return Checkout(repo.name, root, _git(base, "rev-parse", "HEAD"))
    return Checkout(repo.name, root, content_hash(root))
