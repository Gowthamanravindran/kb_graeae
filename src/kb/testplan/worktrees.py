"""Read any commit of a repo without touching the user's working copy: git worktrees under KB_HOME."""

from __future__ import annotations

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


def _mirror(source: Path, name: str, work: Path, commit: str) -> Path:
    """KB's own clone of the user's local repo, so worktree bookkeeping never lands in their .git.
    It takes the user's branches *and* their remote-tracking branches (a PR branch is often only origin/x)."""
    m = Path(work) / "mirrors" / name
    if not (m / ".git").exists():
        m.parent.mkdir(parents=True, exist_ok=True)
        _git(m.parent, "clone", "--quiet", "--no-checkout", str(source), str(m))
    if not _has_commit(m, commit):
        _git(m, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*",
             "+refs/remotes/*:refs/remotes/src/*")
    return m


def worktree_at(repo: RepoConfig, ref: str, work: Path, refresh: bool = False) -> Checkout:
    """`refresh` fetches now (an impact run reads branch tips); otherwise a fetch from the last minute is reused."""
    source = git_source(repo, work, refresh)
    if source is None:
        raise KbError(f"PR analysis needs git repos for: {repo.name}")
    commit = resolve_commit(source, ref, remote_first=repo.url is not None)
    dest = Path(work) / "worktrees" / repo.name / commit[:12]
    if not (dest / ".git").exists():
        mirror = _mirror(source, repo.name, work, commit) if repo.local_path is not None else source
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(mirror, "worktree", "prune")
        _git(mirror, "worktree", "add", "--detach", "--quiet", str(dest), commit)
    root = dest / repo.path if repo.path else dest
    return Checkout(repo.name, root, commit)
