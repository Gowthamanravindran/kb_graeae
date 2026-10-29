"""OIDC device-code login (RFC 8628) and refresh, for registries that use single sign-on."""

from __future__ import annotations

import time

import httpx

from kb.errors import KbError

DEVICE_GRANT = "urn:ietf:params:oauth:grant-type:device_code"


def _discover(http: httpx.Client, issuer: str) -> dict:
    return http.get(f"{issuer.rstrip('/')}/.well-known/openid-configuration").json()


def _token_from(body: dict) -> dict:
    access, idt = body.get("access_token"), body.get("id_token")
    token = access if access and access.count(".") == 2 else idt  # registry validates JWTs
    if not token:
        raise KbError("the identity provider returned no usable token (need a JWT access token or id_token)")
    return {"token": token, "refresh_token": body.get("refresh_token"),
            "expires_at": time.time() + int(body.get("expires_in", 3600))}


def _oidc_config(registry_url: str, http: httpx.Client) -> dict:
    cfg = http.get(f"{registry_url.rstrip('/')}/api/v1/auth/config").json().get("oidc")
    if not cfg:
        raise KbError(f"this registry uses API tokens: kb login {registry_url.rstrip('/')} --token <token>")
    return cfg


def device_login(registry_url: str, http: httpx.Client, sleep=time.sleep, out=print) -> dict:
    cfg = _oidc_config(registry_url, http)
    meta = _discover(http, cfg["issuer"])
    form = {"client_id": cfg["client_id"], "scope": "openid email profile offline_access"}
    if cfg.get("audience"):
        form["audience"] = cfg["audience"]
    d = http.post(meta["device_authorization_endpoint"], data=form).json()
    if "device_code" not in d:
        raise KbError(f"identity provider refused the login request: {d.get('error_description') or d}")
    out(f"Open {d.get('verification_uri_complete') or d['verification_uri']} and confirm code {d['user_code']}")
    interval = int(d.get("interval", 5))
    deadline = time.time() + int(d.get("expires_in", 600))
    while time.time() < deadline:
        sleep(interval)
        r = http.post(meta["token_endpoint"], data={"grant_type": DEVICE_GRANT, "device_code": d["device_code"],
                                                    "client_id": cfg["client_id"]})
        body = r.json()
        if r.status_code == 200:
            return _token_from(body)
        err = body.get("error")
        if err == "authorization_pending":
            continue
        if err == "slow_down":
            interval += 5
            continue
        if err == "access_denied":
            raise KbError("login was denied in the browser")
        if err == "expired_token":
            break
        raise KbError(f"login failed: {body.get('error_description') or err}")
    raise KbError("login code expired; run kb login again")


def refresh(registry_url: str, refresh_token: str, http: httpx.Client) -> dict:
    cfg = _oidc_config(registry_url, http)
    meta = _discover(http, cfg["issuer"])
    r = http.post(meta["token_endpoint"], data={"grant_type": "refresh_token", "refresh_token": refresh_token,
                                                "client_id": cfg["client_id"]})
    if r.status_code != 200:
        raise KbError(f"session expired: run kb login {registry_url.rstrip('/')}")
    got = _token_from(r.json())
    got["refresh_token"] = got["refresh_token"] or refresh_token
    return got
