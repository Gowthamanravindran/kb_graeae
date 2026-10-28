"""Registry credentials in $KB_HOME/credentials.json (mode 0600), keyed by registry URL."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal


def norm_url(url: str) -> str:
    return url.rstrip("/")


def _path(kb_home: Path) -> Path:
    return Path(kb_home) / "credentials.json"


def load_credentials(kb_home: Path) -> dict:
    p = _path(kb_home)
    return json.loads(p.read_text()) if p.exists() else {}


def save_token(kb_home: Path, registry_url: str, token: str, kind: Literal["static", "oidc"],
               refresh_token: str | None = None, expires_at: float | None = None) -> None:
    creds = load_credentials(kb_home)
    creds[norm_url(registry_url)] = {"token": token, "kind": kind, "refresh_token": refresh_token,
                                     "expires_at": expires_at}
    p = _path(kb_home)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(creds, f, indent=1)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def get_token(kb_home: Path, registry_url: str) -> dict | None:
    return load_credentials(kb_home).get(norm_url(registry_url))
