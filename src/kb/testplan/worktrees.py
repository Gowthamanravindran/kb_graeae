"""Read any commit of a repo without touching the user's working copy: git worktrees under KB_HOME."""

from __future__ import annotations

from pathlib import Path

from kb.config import ProjectConfig, RepoConfig
from kb.errors import KbError
from kb.ingest.checkout import Checkout, _git, _is_git_repo


def git_source(repo: RepoConfig, work: Path) -> Path | None:
    """The git repository to read refs from: the user's local repo, or KB's managed clone of `url`."""
    if repo.local_path is not None:
        base = Path(repo.local_path)
        return base if base.is_dir() and _is_git_repo(base) else None
    clone = Path(work) / "clones" / repo.name
    if (clone / ".git").exists():
        _git(clone, "fetch", "--quiet", "origin")
    else:
        clone.parent.mkdir(parents=True, exist_ok=True)
        _git(clone.parent, "clone", "--quiet", repo.url, str(clone))
    return clone


def require_git(cfg: ProjectConfig, repos: list[str], work: Path) -> None:
    missing = [n for n in repos if git_source(cfg.repo(n), work) is None]
    if missing:
        raise KbError(f"PR analysis needs git repos for: {', '.join(missing)}")


def resolve_commit(source: Path, ref: str) -> str:
    for candidate in (ref, f"origin/{ref}"):
        try:
            return _git(source, "rev-parse", "--verify", "--quiet", f"{candidate}^{{commit}}")
        except KbError:
            continue
    raise KbError(f"no branch or commit '{ref}' in {source}")


def _mirror(source: Path, name: str, work: Path) -> Path:
    """KB's own clone of the user's local repo, so worktree bookkeeping never lands in their .git."""
    m = Path(work) / "mirrors" / name
    if (m / ".git").exists():
        _git(m, "fetch", "--quiet", "--prune", "origin", "+refs/heads/*:refs/remotes/origin/*")
    else:
        m.parent.mkdir(parents=True, exist_ok=True)
        _git(m.parent, "clone", "--quiet", "--no-checkout", str(source), str(m))
    return m


def worktree_at(repo: RepoConfig, ref: str, work: Path) -> Checkout:
    source = git_source(repo, work)
    if source is None:
        raise KbError(f"PR analysis needs git repos for: {repo.name}")
    commit = resolve_commit(source, ref)
    dest = Path(work) / "worktrees" / repo.name / commit[:12]
    if not (dest / ".git").exists():
        mirror = _mirror(source, repo.name, work) if repo.local_path is not None else source
        dest.parent.mkdir(parents=True, exist_ok=True)
        _git(mirror, "worktree", "prune")
        _git(mirror, "worktree", "add", "--detach", "--quiet", str(dest), commit)
    root = dest / repo.path if repo.path else dest
    return Checkout(repo.name, root, commit)
