import os
import subprocess
import sys

from typer.testing import CliRunner

from kb.envfile import load_env_file


def test_parses_and_never_overrides(tmp_path, monkeypatch):
    f = tmp_path / "kb.env"
    f.write_text('# Langfuse\nexport LANGFUSE_PUBLIC_KEY="pk-lf-1"\nLANGFUSE_SECRET_KEY=sk-lf-2 \n\nLANGFUSE_HOST=\'http://localhost:3000\'\nbad line\nKB_X=a=b # not a comment\n')
    monkeypatch.delenv("LANGFUSE_PUBLIC_KEY", raising=False)
    monkeypatch.delenv("LANGFUSE_HOST", raising=False)
    monkeypatch.delenv("KB_X", raising=False)
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "from-shell")
    loaded = load_env_file(f)
    assert os.environ["LANGFUSE_PUBLIC_KEY"] == "pk-lf-1"
    assert os.environ["LANGFUSE_HOST"] == "http://localhost:3000"
    assert os.environ["LANGFUSE_SECRET_KEY"] == "from-shell"  # the real environment wins
    assert os.environ["KB_X"] == "a=b # not a comment"
    assert set(loaded) == {"LANGFUSE_PUBLIC_KEY", "LANGFUSE_HOST", "KB_X"}


def test_missing_file_is_fine(tmp_path):
    assert load_env_file(tmp_path / "nope.env") == []


def test_cli_loads_kb_env_from_kb_home(tmp_path, monkeypatch):
    from kb.cli import app

    (tmp_path / "kb.env").write_text("KB_ENVFILE_PROBE=yes\n")
    monkeypatch.setenv("KB_HOME", str(tmp_path))
    monkeypatch.delenv("KB_ENVFILE_PROBE", raising=False)
    assert CliRunner().invoke(app, ["version"]).exit_code == 0
    assert os.environ.get("KB_ENVFILE_PROBE") == "yes"


def test_serve_loads_kb_env_from_its_kb_home(tmp_path):
    from serve_helpers import TOKEN

    home = tmp_path / "home"
    home.mkdir()
    (home / "kb.env").write_text("LANGFUSE_PUBLIC_KEY=pk\nLANGFUSE_SECRET_KEY=sk\nLANGFUSE_HOST=http://localhost:3000\n")
    env = {k: v for k, v in os.environ.items() if not k.startswith("LANGFUSE_") and k != "KB_HOME"}
    env["KB_SERVE_TOKEN"] = TOKEN
    p = subprocess.Popen([sys.executable, "-m", "kb.cli", "serve", "--port", "0", "--kb-home", str(home)], env=env,
                         stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    try:
        import json

        import httpx

        port = json.loads(p.stdout.readline().split(" ", 1)[1])["port"]
        d = httpx.get(f"http://127.0.0.1:{port}/api/status", headers={"X-KB-Token": TOKEN}, timeout=10).json()
        assert d["langfuse"] == {"configured": True, "host": "http://localhost:3000"}
    finally:
        p.terminate()
        p.wait(10)
