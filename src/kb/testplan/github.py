"""Read a GitHub pull request's branches, title and description (REST API, optional GITHUB_TOKEN)."""

from __future__ import annotations

import os
import re

from kb.errors import KbError
from kb.testplan.models import PrInfo

_URL = re.compile(r"github\.com/([^/]+)/([^/]+)/pull/(\d+)")
NO_TOKEN = "Add GITHUB_TOKEN to ~/.kb/kb.env, or pick the branches yourself"


class HttpError(Exception):
    def __init__(self, status: int):
        super().__init__(f"HTTP {status}")
        self.status = status


def _get(url: str, token: str | None) -> dict:
    import httpx

    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    r = httpx.get(url, headers=headers, timeout=20)
    if r.status_code >= 400:
        raise HttpError(r.status_code)
    return r.json()


class PullRequest(PrInfo):
    base_ref: str = ""
    head_ref: str = ""


def pr_info(url: str) -> PullRequest:
    m = _URL.search(url)
    if not m:
        raise KbError(f"not a GitHub pull request link: {url}")
    owner, repo, num = m.groups()
    token = os.environ.get("GITHUB_TOKEN") or None
    try:
        d = _get(f"https://api.github.com/repos/{owner}/{repo}/pulls/{num}", token)
    except HttpError as e:
        if e.status in (401, 403, 404) and not token:
            raise KbError(NO_TOKEN) from e
        raise KbError(f"GitHub refused {url} ({e.status})") from e
    except Exception as e:  # noqa: BLE001 — network
        raise KbError(f"could not reach GitHub for {url}: {e}") from e
    body = d.get("body") or ""
    base, head = d.get("base") or {}, d.get("head") or {}
    base_repo = (base.get("repo") or {}).get("full_name")
    head_repo = (head.get("repo") or {}).get("full_name")
    # a fork's branch isn't in the base repo (and a deleted fork has no repo at all): GitHub keeps every PR's
    # commits under refs/pull/<n>/head of the base repo
    fork = "repo" in head and (head_repo is None or (base_repo is not None and head_repo != base_repo))
    return PullRequest(url=url, title=d.get("title") or "", body=body,
                       issues=sorted(set(re.findall(r"#\d+", body)), key=lambda s: int(s[1:])),
                       base_ref=base.get("ref", ""), head_ref=f"pull/{num}/head" if fork else head.get("ref", ""))
