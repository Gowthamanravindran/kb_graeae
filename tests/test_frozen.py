import sys

import pytest

from kb.errors import KbError
from kb.ingest import graphify as gi


def test_graphify_command_order(monkeypatch):
    monkeypatch.setenv("KB_GRAPHIFY_CMD", "/opt/g --x")
    assert gi.graphify_command() == ["/opt/g", "--x"]
    monkeypatch.delenv("KB_GRAPHIFY_CMD")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert gi.graphify_command() == [sys.executable, "--run-graphify"]
    monkeypatch.setattr(sys, "frozen", False)
    monkeypatch.setattr(gi.shutil, "which", lambda name: "/usr/bin/graphify")
    assert gi.graphify_command() == ["graphify"]
    monkeypatch.setattr(gi.shutil, "which", lambda name: None)
    assert gi.graphify_command() == [sys.executable, "-m", "graphify"]
    monkeypatch.setattr(gi.importlib.util, "find_spec", lambda name: None)
    with pytest.raises(KbError, match="graphify not found"):
        gi.graphify_command()


def test_frozen_dispatches_graphify(monkeypatch):
    import graphify.__main__ as gm

    from kb import frozen

    seen = {}

    def fake_main():
        seen["argv"] = list(sys.argv)
        return 0
    monkeypatch.setattr(gm, "main", fake_main)
    assert frozen.main(["--run-graphify", "--help"]) == 0
    assert seen["argv"] == ["graphify", "--help"]


def test_frozen_runs_serve(monkeypatch):
    from kb import frozen

    seen = {}
    monkeypatch.setattr(frozen, "_run_kb", lambda args: seen.setdefault("args", args) and 0)
    frozen.main(["--port", "0"])
    assert seen["args"] == ["serve", "--port", "0"]
