from __future__ import annotations

import importlib
import importlib.util

from fastapi import FastAPI

from kb_registry.db import make_engine, make_sessionmaker
from kb_registry.settings import Settings
from kb_registry.storage import BlobStore, make_store


def create_app(settings: Settings, store: BlobStore | None = None) -> FastAPI:
    app = FastAPI(title="kb-registry", version="0.1.0")
    app.state.settings = settings
    app.state.sessionmaker = make_sessionmaker(make_engine(settings.database_url))
    app.state.store = store or make_store(settings)
    for name in ("projects", "blobs", "packs"):
        modname = f"kb_registry.api.{name}"
        if importlib.util.find_spec(modname) is None:
            continue
        app.include_router(importlib.import_module(modname).router, prefix="/api/v1")
    return app
