from typer.testing import CliRunner

from kb.cli import app


def test_version_prints_version():
    r = CliRunner().invoke(app, ["version"])
    assert r.exit_code == 0
    assert "0.1.0" in r.stdout
