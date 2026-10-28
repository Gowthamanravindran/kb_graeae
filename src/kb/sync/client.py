"""HTTP client for kb-registry."""

from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import zstandard

from kb.errors import ConfigError, KbError, RegistryError, RegistryUnavailable
from kb.sync.credentials import get_token, norm_url

API = "/api/v1"


def make_http(url: str) -> httpx.Client:
    return httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True)


class RegistryClient:
    def __init__(self, base_url: str, token: str | None, http: httpx.Client | None = None, on_refresh=None) -> None:
        self.base_url = norm_url(base_url)
        self.token = token
        self.http = http or make_http(self.base_url)
        self.on_refresh = on_refresh

    # ---- plumbing ----
    def _headers(self, extra: dict | None = None) -> dict:
        if self.on_refresh is not None:
            self.token = self.on_refresh() or self.token
        h = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        return {**h, **(extra or {})}

    def _req(self, method: str, path: str, **kw) -> httpx.Response:
        url = f"{self.base_url}{API}{path}"
        try:
            r = self.http.request(method, url, headers=self._headers(kw.pop("headers", None)), **kw)
        except httpx.TransportError as e:
            raise RegistryUnavailable(f"registry unreachable: {self.base_url} ({type(e).__name__})") from e
        if r.status_code >= 400:
            try:
                body = r.json()
            except ValueError:
                body = {"detail": r.text[:300]}
            detail = body.get("detail", r.reason_phrase) if isinstance(body, dict) else str(body)
            if r.status_code == 401:
                raise RegistryError(f"not logged in to {self.base_url}: run kb login {self.base_url}", 401, body)
            raise RegistryError(str(detail), r.status_code, body if isinstance(body, dict) else {})
        return r

    # ---- API ----
    def auth_config(self) -> dict:
        return self._req("GET", "/auth/config").json()

    def me(self) -> dict:
        return self._req("GET", "/me").json()

    def projects(self) -> list:
        return self._req("GET", "/projects").json()

    def project(self, p: str) -> dict:
        return self._req("GET", f"/projects/{p}").json()

    def put_config(self, p: str, config_yaml: str, seed: bool = False) -> None:
        self._req("PUT", f"/projects/{p}/config", params={"seed": "true"} if seed else None,
                  json={"config_yaml": config_yaml})

    def branches(self, p: str) -> list:
        return self._req("GET", f"/projects/{p}/branches").json()

    def versions(self, p: str, b: str) -> list:
        return self._req("GET", f"/projects/{p}/branches/{b}/versions").json()

    def latest(self, p: str, b: str) -> dict:
        return self._req("GET", f"/projects/{p}/branches/{b}/latest").json()

    def missing_blobs(self, hashes: list[str]) -> list[str]:
        out: list[str] = []
        for i in range(0, len(hashes), 5000):
            out += self._req("POST", "/blobs/missing", json={"hashes": hashes[i:i + 5000]}).json()["missing"]
        return out

    def put_blob(self, h: str, raw: bytes) -> None:
        self._req("PUT", f"/blobs/{h}", content=zstandard.ZstdCompressor(level=10).compress(raw),
                  headers={"Content-Type": "application/zstd"})

    def get_blob(self, h: str) -> bytes:
        data = zstandard.ZstdDecompressor().decompress(self._req("GET", f"/blobs/{h}").content,
                                                       max_output_size=200 * 2**20)
        if hashlib.sha256(data).hexdigest() != h:
            raise KbError(f"blob {h} from the registry is corrupt (hash mismatch)")
        return data

    def create_base(self, p: str, b: str, manifest: dict, files: dict) -> dict:
        return self._req("POST", f"/projects/{p}/branches/{b}/bases", json={"manifest": manifest, "files": files}).json()

    def publish(self, p: str, b: str, items: list[dict]) -> dict:
        return self._req("POST", f"/projects/{p}/branches/{b}/overlay", json={"items": items}).json()


def resolve_url(cfg=None, registry: str | None = None) -> str:
    url = registry or (cfg.registry if cfg is not None else None)
    if not url:
        raise ConfigError("set registry: in kb.yaml or pass --registry")
    return norm_url(url)


def client_for(cfg=None, registry: str | None = None, kb_home: Path | None = None) -> RegistryClient:
    from kb.packs.store import default_kb_home

    url = resolve_url(cfg, registry)
    cred = get_token(kb_home or default_kb_home(), url)
    return RegistryClient(url, cred["token"] if cred else None, http=make_http(url))
