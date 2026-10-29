"""Read any commit of a repo without touching the user's working copy: git worktrees under KB_HOME."""

from __future__ import annotations

import os
import re
import shutil
import threading
import time
from pathlib import Path

from kb.config import ProjectConfig, RepoConfig
from kb.errors import KbError
from kb.ingest.checkout import Checkout, _git, _is_git_repo


def git_source(repo: RepoConfig, work: Path, refresh: bool = False) -> Path | None:
    """The git repository to read refs from: the user's local repo, or KB's managed clone of `url`."""
    if repo.local_path is not None:
        base = Path(repo.local_path)
        return base if base.is_dir() and _is_git_repo(base) else None
    clone = Path(work) / "clones" / repo.name
    if (clone / ".git").exists():
        if refresh:
            _fetched.pop(clone.resolve(), None)
        _fetch_once(clone, "origin")
    else:
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, "clone", "--quiet", repo.url, str(clone))
        _fetched[clone.resolve()] = time.monotonic()  # a fresh clone is as good as a fetch
    return clone


_fetched: dict[Path, float] = {}
_fetch_lock = threading.Lock()
FETCH_TTL = 60.0


def _fetch_once(repo_dir: Path, remote: str, *refspecs: str) -> None:
    """Fetch at most once a minute per clone (one Generate run would otherwise fetch per flow), one at a time."""
    key = Path(repo_dir).resolve()
    with _fetch_lock:
        if time.monotonic() - _fetched.get(key, -1e9) < FETCH_TTL:
            return
        _git(repo_dir, "fetch", "--quiet", "--prune", remote, *refspecs)
        _fetched[key] = time.monotonic()


def require_git(cfg: ProjectConfig, repos: list[str], work: Path) -> None:
    missing = [n for n in repos if git_source(cfg.repo(n), work) is None]
    if missing:
        raise KbError(f"PR analysis needs git repos for: {', '.join(missing)}")


def resolve_commit(source: Path, ref: str, remote_first: bool = False) -> str:
    """A KB-managed clone's local branches never move, so there the remote's branch must win."""
    for candidate in ((f"origin/{ref}", ref) if remote_first else (ref, f"origin/{ref}")):
        try:
            return _git(source, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        except KbError:
            continue
    raise KbError(f"no branch or commit '{ref}' in {source}")


def _has_commit(repo_dir: Path, commit: str) -> bool:
    try:
        _git(repo_dir, "cat-file", "-e", f"{commit}^{{commit}}")
        return True
    except KbError:
        return False


def _mirror(source: Path, name: str, work: Path, commit: str | None) -> Path:
    """KB's own clone of the user's local repo, so worktree bookkeeping never lands in their .git.
    It takes the user's branches *and* their remote-tracking branches (a PR branch is often only origin/x)."""
    m = Path(work) / "mirrors" / name
    if not (m / ".git").exists():
        m.parent.mkdir(parents=True, exist_ok=True)
        _git(m.parent, "clone", "--quiet", "--no-checkout", str(source), str(m))
    if commit is not None and not _has_commit(m, commit):
        _git(m, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*",
             "+refs/remotes/*:refs/remotes/src/*")
    return m


PULL_REF = re.compile(r"^pull/(\d+)/head$")


def _origin_url(source: Path) -> str:
    try:
        return _git(source, "remote", "get-url", "origin")
    except KbError as e:
        raise KbError(f"{source} has no 'origin' remote to fetch the pull request from") from e


def _pull_commit(repo: RepoConfig, source: Path, ref: str, work: Path) -> tuple[str, Path]:
    """A PR's commits by `pull/<n>/head` (a fork's branch is only there). Fetched into KB's own clone —
    the mirror for a local repo, from that repo's GitHub remote — never into the user's .git."""
    if repo.local_path is not None:
        target = _mirror(source, repo.name, work, None)
        remote = _origin_url(source)
    else:
        target, remote = source, "origin"
    _git(target, "fetch", "--quiet", remote, f"+refs/{ref}:refs/kb/{ref}")
    return _git(target, "rev-parse", "--verify", "--quiet", f"refs/kb/{ref}^{{commit}}"), target


def diff_source(repo: RepoConfig, work: Path, *commits: str) -> Path | None:
    """Where these commits can be read: the user's repo (or KB's clone), else the mirror — a fork PR's
    commits are only in the mirror."""
    source = git_source(repo, work)
    if source is None or all(_has_commit(source, c) for c in commits):
        return source
    m = Path(work) / "mirrors" / repo.name
    return m if (m / ".git").exists() and all(_has_commit(m, c) for c in commits) else source


def worktree_at(repo: RepoConfig, ref: str, work: Path, refresh: bool = False) -> Checkout:
    """`refresh` fetches now (an impact run reads branch tips); otherwise a fetch from the last minute is reused."""
    source = git_source(repo, work, refresh)
    if source is None:
        raise KbError(f"PR analysis needs git repos for: {repo.name}")
    if PULL_REF.match(ref):
        commit, _ = _pull_commit(repo, source, ref, work)
    else:
        try:
            commit = resolve_commit(source, ref, remote_first=repo.url is not None)
        except KbError:
            m = Path(work) / "mirrors" / repo.name  # a commit fetched earlier from a pull ref
            if repo.local_path is None or not (m / ".git").exists():
                raise
            commit = resolve_commit(m, ref)
    dest = Path(work) / "worktrees" / repo.name / commit[:12]
    if not (dest / ".git").exists():
        mirror = _mirror(source, repo.name, work, commit) if repo.local_path is not None else source
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(mirror, "worktree", "prune")
        _git(mirror, "worktree", "add", "--detach", "--quiet", str(dest), commit)
    else:
        os.utime(dest)  # recently used: kept by prune_work
    root = dest / repo.path if repo.path else dest
    return Checkout(repo.name, root, commit)


def prune_work(work: Path, max_age_days: float = 14) -> int:
    """Delete PR graphs, build scratch and worktrees nobody used for `max_age_days` (they are rebuilt on
    demand). Returns how many were removed."""
    work = Path(work)
    cutoff = time.time() - max_age_days * 86400
    removed = 0

    def old(p: Path) -> bool:
        try:
            return p.stat().st_mtime < cutoff
        except OSError:
            return False
    for p in list((work / "head_graphs").glob("*.json")):
        if old(p):
            p.unlink(missing_ok=True)
            removed += 1
    for p in list((work / "head_build").glob("*")):
        if old(p):
            shutil.rmtree(p, ignore_errors=True)
            removed += 1
    for repo_dir in list((work / "worktrees").glob("*")):
        gone = False
        for wt in list(repo_dir.glob("*")):
            if old(wt):
                shutil.rmtree(wt, ignore_errors=True)
                removed += 1
                gone = True
        if gone:  # tell the clone the worktree is gone, so the same commit can be checked out again
            for owner in (work / "mirrors" / repo_dir.name, work / "clones" / repo_dir.name):
                if (owner / ".git").exists():
                    try:
                        _git(owner, "worktree", "prune")
                    except KbError:
                        pass
    return removed
