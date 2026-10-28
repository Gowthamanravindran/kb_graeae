import json
import time

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from kb.errors import KbError
from kb.sync.credentials import get_token, save_token
from kb.sync.oidc import device_login, refresh

REG, ISS = "https://kb.corp", "https://idp.corp"
KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
ACCESS = jwt.encode({"sub": "s", "exp": int(time.time()) + 3600}, KEY, algorithm="RS256")


def idp(token_responses, oidc=True):
    calls = {"token": 0, "device": None}

    def handler(req: httpx.Request):
        if req.url.path == "/api/v1/auth/config":
            return httpx.Response(200, json={"oidc": {"issuer": ISS, "client_id": "kb-cli", "audience": "kb"} if oidc else None})
        if req.url.path == "/.well-known/openid-configuration":
            return httpx.Response(200, json={"device_authorization_endpoint": f"{ISS}/device", "token_endpoint": f"{ISS}/token"})
        if req.url.path == "/device":
            calls["device"] = dict(httpx.QueryParams(req.content.decode()))
            return httpx.Response(200, json={"device_code": "dc", "user_code": "ABCD-EFGH", "interval": 5, "expires_in": 600,
                                             "verification_uri": f"{ISS}/activate",
                                             "verification_uri_complete": f"{ISS}/activate?code=ABCD-EFGH"})
        if req.url.path == "/token":
            form = dict(httpx.QueryParams(req.content.decode()))
            calls.setdefault("forms", []).append(form)
            status, body = token_responses[min(calls["token"], len(token_responses) - 1)]
            calls["token"] += 1
            return httpx.Response(status, json=body)
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler)), calls


def test_device_flow_polls_until_token():
    http, calls = idp([(400, {"error": "authorization_pending"}), (400, {"error": "slow_down"}),
                       (200, {"access_token": ACCESS, "id_token": "idt", "refresh_token": "rt", "expires_in": 3600})])
    slept, said = [], []
    got = device_login(REG, http, sleep=slept.append, out=said.append)
    assert got["token"] == ACCESS and got["refresh_token"] == "rt" and got["expires_at"] > time.time()
    assert slept == [5, 5, 10]
    assert any("ABCD-EFGH" in s for s in said)
    assert calls["device"]["client_id"] == "kb-cli" and "openid" in calls["device"]["scope"]
    assert calls["forms"][0]["grant_type"] == "urn:ietf:params:oauth:grant-type:device_code"


def test_opaque_access_token_falls_back_to_id_token():
    http, _ = idp([(200, {"access_token": "opaque", "id_token": "a.b.c", "expires_in": 60})])
    assert device_login(REG, http, sleep=lambda s: None, out=lambda s: None)["token"] == "a.b.c"


def test_device_flow_denied():
    http, _ = idp([(400, {"error": "access_denied"})])
    with pytest.raises(KbError, match="denied"):
        device_login(REG, http, sleep=lambda s: None, out=lambda s: None)


def test_token_registry_says_use_token():
    http, _ = idp([], oidc=False)
    with pytest.raises(KbError, match="kb login https://kb.corp --token"):
        device_login(REG, http, sleep=lambda s: None, out=lambda s: None)


def test_client_refreshes_expiring_token(tmp_path, monkeypatch):
    from kb.sync import client as sc

    save_token(tmp_path, REG, "old", "oidc", refresh_token="rt", expires_at=time.time() + 10)
    monkeypatch.setattr("kb.sync.oidc.refresh", lambda url, rt, http: {"token": "new", "refresh_token": "rt2",
                                                                         "expires_at": time.time() + 3600})
    seen = []

    def handler(req):
        seen.append(req.headers.get("authorization"))
        return httpx.Response(200, json={"email": "e", "is_admin": False, "projects": {}})

    monkeypatch.setattr(sc, "make_http", lambda url: httpx.Client(transport=httpx.MockTransport(handler)))
    sc.client_for(registry=REG, kb_home=tmp_path).me()
    assert seen == ["Bearer new"] and get_token(tmp_path, REG)["refresh_token"] == "rt2"
