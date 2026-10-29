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


def _mark_published(store: PackStore, project: str, branch: str, items: list[dict]) -> None:
    pack = store.load(project, branch)
    ids = {i["id"] for i in items}
    for q in pack.qa:
        if q.id in ids and q.visibility != "published":
            store.save_qa(project, branch, q.model_copy(update={"visibility": "published"}))
    for f in pack.flows:
        if f.id in ids and f.visibility != "published":
            store.save_flow(project, branch, f.model_copy(update={"visibility": "published"}))


def flush_outbox(project: str, branch: str, client: RegistryClient, store: PackStore,
                 warnings: list[str] | None = None) -> int:
    """Send queued publishes in chunks. Still unreachable: they stay queued. Refused by the registry
    (4xx: role, conflict, size): moved to <outbox>.rejected.json with the reason so they stop blocking."""
    from kb.errors import RegistryUnavailable
    from kb.sync.outbox import Outbox

    box = Outbox(store.kb_home)
    items = box.pending(project, branch)
    sent = 0
    for i in range(0, len(items), 500):
        chunk = items[i:i + 500]
        try:
            client.publish(project, branch, chunk)
        except RegistryUnavailable:
            return sent
        except RegistryError as e:
            if e.status is None or not 400 <= e.status < 500:
                raise
            box.reject(project, branch, chunk, str(e))
            if warnings is not None:
                warnings.append(f"registry rejected {len(chunk)} queued items ({e}); kept in "
                                f"{box.dir}/…rejected.json")
            continue
        box.clear(project, branch, [x["id"] for x in chunk])
        if store.exists(project, branch):
            _mark_published(store, project, branch, chunk)
        sent += len(chunk)
    return sent


def publish_items(cfg: ProjectConfig, branch: str, store: PackStore, client: RegistryClient,
                  qa_ids: list[str], include_notes: bool) -> dict:
    from kb.errors import RegistryUnavailable
    from kb.sync.outbox import Outbox
    from kb.sync.pack_io import overlay_items_for_publish

    project = cfg.project
    items = overlay_items_for_publish(store.load(project, branch), qa_ids, include_notes)
    if not items:
        raise KbError("nothing to publish: give answer ids and/or --notes")
    try:
        _require(client, project, "contributor")
        flush_outbox(project, branch, client, store, [])
        res = client.publish(project, branch, items)
    except RegistryUnavailable:
        Outbox(store.kb_home).add(project, branch, items)
        return {"queued": len(items)}
    _mark_published(store, project, branch, items)
    return res


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
    store.update_manifest(project, branch, version=res["version"], parent=res["parent"], synced=True)
    if not client.project(project).get("config_yaml"):
        client.put_config(project, seed_config_text(cfg), seed=True)
    return {"version": res["version"], "parent": res["parent"], "files": len(files), "uploaded": uploaded}


def pull_pack(project: str, branch: str, store: PackStore, client: RegistryClient,
              cfg: ProjectConfig | None = None, force: bool = False) -> dict:
    from kb.packs.index import build_index
    from kb.packs.model import Manifest
    from kb.packs.store import check_unsafe_paths
    from kb.sync.pack_io import apply_overlay

    warnings: list[str] = []
    flush_outbox(project, branch, client, store, warnings)
    lt = client.latest(project, branch)
    if store.exists(project, branch) and not force:
        from kb.packs.model import commits_key

        local = store.load(project, branch).manifest
        remote_commits = lt["manifest"].get("commits", {})
        if not local.synced and commits_key(local.commits) != commits_key(remote_commits):
            raise KbError(f"your local analysis of {project}@{branch} is for different commits and was never "
                          "pushed; run kb push first, or kb pull --force to replace it with the registry's")
    files_map: dict[str, str] = lt["files"]
    check_unsafe_paths(store.pack_dir(project, branch), files_map)  # before downloading anything
    cache = store.kb_home / "blobs"
    cache.mkdir(parents=True, exist_ok=True)
    files: dict[str, bytes] = {}
    for rel, h in files_map.items():
        cached = cache / h
        if cached.is_file() and hashlib.sha256(cached.read_bytes()).hexdigest() == h:
            files[rel] = cached.read_bytes()
            continue
        data = client.get_blob(h)
        cached.write_bytes(data)
        files[rel] = data
    manifest = Manifest.model_validate({**lt["manifest"], "synced": True})
    store.write_pulled_base(project, branch, manifest, files)
    _, more = apply_overlay(store, project, branch, lt["overlay"])
    warnings += more

    config_yaml = client.project(project).get("config_yaml", "")
    if config_yaml:
        dest = store.kb_home / "projects" / project / "kb.yaml"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(config_yaml)

    if cfg is not None:
        from kb.analyze import _mark_stale
        from kb.ask import available_checkouts

        checkouts, more2 = available_checkouts(cfg, branch, store, manifest.commits, manifest.branches)
        warnings += more2
        _mark_stale(store, project, branch, checkouts)
    pack = store.load(project, branch)
    build_index(pack.dir, pack)
    return {"version": manifest.version, "modules": len(pack.modules),
            "shared": sum(1 for q in pack.qa if q.visibility == "published"),
            "stale": sum(1 for q in pack.qa if q.stale), "warnings": warnings}
