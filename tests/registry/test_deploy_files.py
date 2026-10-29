from pathlib import Path

import yaml

D = Path(__file__).parents[2] / "deploy" / "registry"


def test_compose_is_wired():
    c = yaml.safe_load((D / "docker-compose.yml").read_text())
    svc = c["services"]
    assert set(svc) == {"postgres", "minio", "minio-init", "registry"}
    assert svc["postgres"]["image"].startswith("postgres:16")
    env = svc["registry"]["environment"]
    for k in ("KB_REGISTRY_DATABASE_URL", "KB_REGISTRY_STORAGE", "KB_REGISTRY_S3_ENDPOINT",
              "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
        assert k in env, k
    assert env["KB_REGISTRY_STORAGE"] == "s3://kb-packs"
    dep = svc["registry"]["depends_on"]
    assert dep["postgres"]["condition"] == "service_healthy"
    assert dep["minio-init"]["condition"] == "service_completed_successfully"
    assert "8080:8080" in svc["registry"]["ports"]
    assert "kb-packs" in " ".join(svc["minio-init"]["entrypoint"] if isinstance(svc["minio-init"]["entrypoint"], list)
                                  else [svc["minio-init"]["entrypoint"]])


def test_dockerfile():
    df = (D / "Dockerfile").read_text()
    for needle in ("python:3.12-slim", "--extra registry", "USER kb", "HEALTHCHECK", "kb-registry migrate",
                   "kb-registry serve --host 0.0.0.0 --port 8080"):
        assert needle in df, needle


def test_env_example_has_every_variable_compose_uses():
    import re

    used = set(re.findall(r"\$\{([A-Z_]+)", (D / "docker-compose.yml").read_text()))
    example = {l.split("=")[0] for l in (D / ".env.example").read_text().splitlines() if "=" in l and not l.startswith("#")}
    assert used <= example, used - example
