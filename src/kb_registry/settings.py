from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str = "sqlite:///./kb-registry.db"
    storage: str = "file://./kb-registry-blobs"
    s3_endpoint: str | None = None
    oidc_issuer: str | None = None
    oidc_audience: str | None = None
    oidc_client_id: str | None = None


def get_settings() -> Settings:
    e = os.environ.get
    return Settings(
        database_url=e("KB_REGISTRY_DATABASE_URL", Settings.database_url),
        storage=e("KB_REGISTRY_STORAGE", Settings.storage),
        s3_endpoint=e("KB_REGISTRY_S3_ENDPOINT") or None,
        oidc_issuer=e("KB_REGISTRY_OIDC_ISSUER") or None,
        oidc_audience=e("KB_REGISTRY_OIDC_AUDIENCE") or None,
        oidc_client_id=e("KB_REGISTRY_OIDC_CLIENT_ID") or None,
    )
