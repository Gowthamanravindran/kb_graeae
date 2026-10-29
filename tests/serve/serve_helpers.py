from agent_helpers import FIX

TOKEN = "t" * 64


def write_cfg(tmp_path, registry=None, name="mini"):
    y = tmp_path / f"{name}.kb.yaml"
    lines = [f"project: {name}", "repos:"]
    for r in ("mini-fe", "mini-be"):
        lines.append(f"  - {{name: {r}, role: {'frontend' if r == 'mini-fe' else 'backend'}, local_path: {FIX / r}}}")
    if registry:
        lines.append(f"registry: {registry}")
    y.write_text("\n".join(lines) + "\n")
    return y




def wait_job(client, job_id, timeout=60):
    import time

    end = time.time() + timeout
    while time.time() < end:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["state"] in ("done", "failed"):
            return j
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish")


def registry_serve(tmp_path, serve, email="maint@x"):
    """Wire the serve app to an in-process stage-2 registry as EMAIL; returns (registry, cfg_path)."""
    from fastapi.testclient import TestClient

    from helpers import seeded
    from kb.sync.client import RegistryClient

    reg = seeded(tmp_path / "registry")
    c, settings, store, provider, _ = serve
    settings.client_factory = lambda cfg=None, registry=None: RegistryClient(
        "http://testserver", reg.tokens[email], http=TestClient(reg.app))
    cfg = write_cfg(tmp_path, registry="http://testserver")
    c.post("/api/projects", json={"config_path": str(cfg)})
    return reg, cfg
