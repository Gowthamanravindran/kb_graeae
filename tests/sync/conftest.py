import pytest
from fastapi.testclient import TestClient

from kb.sync.client import RegistryClient

from helpers import seeded

URL = "http://testserver"


@pytest.fixture
def registry(tmp_path):
    return seeded(tmp_path / "registry")


@pytest.fixture
def client_for_user(registry):
    def make(email: str) -> RegistryClient:
        return RegistryClient(URL, registry.tokens[email], http=TestClient(registry.app))
    return make


@pytest.fixture
def as_user(monkeypatch, client_for_user):
    """Make CLI commands talk to the in-process registry as EMAIL."""
    def use(email: str):
        monkeypatch.setattr("kb.sync.client.client_for", lambda *a, **k: client_for_user(email))
    return use
