import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kb_registry.models import User

from helpers import seeded

ISS, AUD = "https://idp.example.com", "kb-registry"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeJWK:
    key = KEY.public_key()


@pytest.fixture
def reg(tmp_path, monkeypatch):
    r = seeded(tmp_path, oidc_issuer=ISS, oidc_audience=AUD, oidc_client_id="kb-cli")
    monkeypatch.setattr("kb_registry.auth.signing_key", lambda app, token: FakeJWK.key)
    return r


def tok(**over):
    claims = {"iss": ISS, "aud": AUD, "sub": "u-123", "email": "new@corp", "exp": int(time.time()) + 300} | over
    return jwt.encode(claims, KEY, algorithm="RS256")


def call(reg, token):
    c = reg.client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c.get("/api/v1/me")


def test_valid_jwt_authenticates_and_autocreates_user(reg):
    r = call(reg, tok())
    assert r.status_code == 200 and r.json() == {"email": "new@corp", "is_admin": False, "projects": {}}
    with reg.S() as s:
        assert s.query(User).filter_by(email="new@corp").one().oidc_sub == "u-123"
    assert call(reg, tok()).status_code == 200  # second login finds the same user


def test_existing_user_matched_by_email_gets_sub(reg):
    r = call(reg, tok(email="view@x", sub="sub-v"))
    assert r.json()["projects"] == {"mini": "viewer"}


def test_wrong_audience_401(reg):
    assert call(reg, tok(aud="other")).status_code == 401


def test_expired_401(reg):
    assert call(reg, tok(exp=int(time.time()) - 10)).status_code == 401


def test_missing_email_401(reg):
    claims = {"iss": ISS, "aud": AUD, "sub": "x", "exp": int(time.time()) + 300}
    assert call(reg, jwt.encode(claims, KEY, algorithm="RS256")).status_code == 401


def test_static_tokens_still_work(reg):
    assert reg.client("view@x").get("/api/v1/me").status_code == 200


def test_auth_config_exposes_issuer(reg):
    assert reg.client().get("/api/v1/auth/config").json() == {"oidc": {"issuer": ISS, "client_id": "kb-cli", "audience": AUD}}
