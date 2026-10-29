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
    return PullRequest(url=url, title=d.get("title") or "", body=body,
                       issues=sorted(set(re.findall(r"#\d+", body)), key=lambda s: int(s[1:])),
                       base_ref=(d.get("base") or {}).get("ref", ""), head_ref=(d.get("head") or {}).get("ref", ""))
