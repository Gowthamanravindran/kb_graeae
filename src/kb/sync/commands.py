"""push / pull / publish / sync: what the CLI commands do, minus printing."""

from __future__ import annotations

import hashlib

import yaml

from kb.config import ProjectConfig
from kb.errors import KbError, RegistryError
from kb.packs.store import PackStore
from kb.sync.client import RegistryClient
from kb.sync.pack_io import base_files

SEED_HEADER = "# repos without url: add local_path in your own copy\n"


def flush_outbox(project: str, branch: str, client: RegistryClient, store: PackStore) -> int:
    """Send queued publishes. Filled in with the outbox (Task 11)."""
    return 0


ROLES = ("viewer", "contributor", "maintainer")  # same ladder as the registry (no server import here)


def _require(client: RegistryClient, project: str, role: str) -> None:
    me = client.me()
    if me.get("is_admin"):
        return
    have = me.get("projects", {}).get(project)
    if have is None:
        raise KbError(f"project {project} is not on {client.base_url} or you have no access to it")
    if ROLES.index(have) < ROLES.index(role):
        raise KbError(f"your role on {project} is {have}; this needs {role}")


def seed_config_text(cfg: ProjectConfig) -> str:
    data = cfg.model_dump(mode="json", by_alias=True, exclude_none=True)
    for r in data.get("repos", []):
        r.pop("local_path", None)
    for lk in data.get("linkers", []):
        if not lk.get("options"):
            lk.pop("options", None)
    return SEED_HEADER + yaml.safe_dump(data, sort_keys=False)


def push_pack(cfg: ProjectConfig, branch: str, store: PackStore, client: RegistryClient) -> dict:
    project = cfg.project
    _require(client, project, "maintainer")
    flush_outbox(project, branch, client, store)
    pack = store.load(project, branch)
    if pack.manifest.status != "complete":
        raise KbError("pack is incomplete; run kb analyze --resume before pushing")
    files = base_files(pack.dir)
    hashes = {path: hashlib.sha256(data).hexdigest() for path, data in files.items()}
    missing = set(client.missing_blobs(sorted(set(hashes.values()))))
    uploaded = 0
    for path, h in hashes.items():
        if h in missing:
            client.put_blob(h, files[path])
            missing.discard(h)
            uploaded += 1
    try:
        res = client.create_base(project, branch, pack.manifest.model_dump(mode="json"), hashes)
    except RegistryError as e:
        if e.status == 409:
            raise KbError(f"{project}@{branch} at these commits is already on the registry "
                          f"(version {e.body.get('version')}) — run kb pull instead") from e
        raise
    store.update_manifest(project, branch, version=res["version"], parent=res["parent"])
    if not client.project(project).get("config_yaml"):
        client.put_config(project, seed_config_text(cfg), seed=True)
    return {"version": res["version"], "parent": res["parent"], "files": len(files), "uploaded": uploaded}
