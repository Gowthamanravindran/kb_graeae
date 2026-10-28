import pytest
from fastapi.testclient import TestClient

from kb.serve.app import ServeSettings, create_app

from agent_helpers import FIX, build_pack
from fakes import FakeProvider

from serve_helpers import TOKEN, write_cfg


@pytest.fixture
def serve(tmp_path):
    """A kb serve app over a KB_HOME holding the mini pack; returns (client, settings, store, provider)."""
    store, _ = build_pack(tmp_path)  # KB_HOME = tmp_path/home
    provider = FakeProvider()
    settings = ServeSettings(kb_home=tmp_path / "home", token=TOKEN, provider_factory=lambda: provider)
    app = create_app(settings)
    c = TestClient(app, base_url="http://127.0.0.1:5555")
    c.headers["X-KB-Token"] = TOKEN
    cfg = write_cfg(tmp_path)
    return c, settings, store, provider, cfg
