import httpx
import pytest
import yaml
from typer.testing import CliRunner

from kb.cli import app
from kb.errors import KbError
from kb.sync.client import RegistryClient
from kb.sync.commands import flush_outbox, publish_items, pull_pack, push_pack
from kb.sync.outbox import Outbox

from agent_helpers import build_pack, save_flow
from test_push import URL, cfg


@pytest.fixture
def setup(tmp_path, client_for_user):
    a, cos = build_pack(tmp_path / "a")
    push_pack(cfg(), "main", a, client_for_user("maint@x"))
    save_flow(a, cos)  # QA q1 + flow f1 (private)
    from kb.packs.model import Suggestion

    a.add_suggestions("mini", "main", [Suggestion(id="s1", question="Next?", source_qa_id="q1")])
    return a


def offline_client(registry):
    def boom(request):
        raise httpx.ConnectError("down", request=request)
    return RegistryClient(URL, registry.tokens["contrib@x"], http=httpx.Client(transport=httpx.MockTransport(boom)))


def test_publish_sends_qa_flow_and_suggestions_and_marks_published(setup, client_for_user):
    out = publish_items(cfg(), "main", setup, client_for_user("contrib@x"), ["q1"], include_notes=False)
    assert sorted(out["added"]) == ["f1", "q1", "s1"]
    p = setup.load("mini", "main")
    assert p.qa[0].visibility == "published" and p.flows[0].visibility == "published"
    ids = {i["id"] for i in client_for_user("view@x").latest("mini", "main")["overlay"]}
    assert ids == {"q1", "f1", "s1"}


def test_publish_offline_queues_then_sync_sends(setup, registry, client_for_user):
    out = publish_items(cfg(), "main", setup, offline_client(registry), ["q1"], include_notes=False)
    assert out == {"queued": 3}
    assert setup.load("mini", "main").qa[0].visibility == "private"
    assert {i["id"] for i in Outbox(setup.kb_home).pending("mini", "main")} == {"q1", "f1", "s1"}
    assert flush_outbox("mini", "main", offline_client(registry), setup) == 0  # still down: kept
    assert flush_outbox("mini", "main", client_for_user("contrib@x"), setup) == 3
    assert Outbox(setup.kb_home).pending("mini", "main") == []
    assert setup.load("mini", "main").qa[0].visibility == "published"


def test_pull_flushes_outbox_first(setup, registry, client_for_user):
    publish_items(cfg(), "main", setup, offline_client(registry), ["q1"], include_notes=False)
    pull_pack("mini", "main", setup, client_for_user("contrib@x"))
    assert Outbox(setup.kb_home).pending("mini", "main") == []


def test_publish_as_viewer_explains_role(setup, tmp_path, monkeypatch, as_user):
    y = tmp_path / "kb.yaml"
    y.write_text(yaml.safe_dump(cfg().model_dump(mode="json", by_alias=True, exclude_none=True)))
    monkeypatch.setenv("KB_HOME", str(setup.kb_home))
    as_user("view@x")
    r = CliRunner().invoke(app, ["publish", str(y), "--branch", "main", "q1"])
    assert r.exit_code == 1 and "your role on mini is viewer; this needs contributor" in r.output


def test_publish_unknown_id(setup, client_for_user):
    with pytest.raises(KbError, match="nope"):
        publish_items(cfg(), "main", setup, client_for_user("contrib@x"), ["nope"], include_notes=False)


def test_notes_flag_publishes_module_notes(setup, client_for_user):
    setup.save_module_note("mini", "main", "tasks", "Sharper summary.")
    out = publish_items(cfg(), "main", setup, client_for_user("contrib@x"), [], include_notes=True)
    [nid] = out["added"]
    assert nid.startswith("note:tasks:")


def test_memory_list_shows_queued(setup, registry, tmp_path, monkeypatch):
    publish_items(cfg(), "main", setup, offline_client(registry), ["q1"], include_notes=False)
    y = tmp_path / "kb.yaml"
    y.write_text(yaml.safe_dump(cfg().model_dump(mode="json", by_alias=True, exclude_none=True)))
    monkeypatch.setenv("KB_HOME", str(setup.kb_home))
    r = CliRunner().invoke(app, ["memory", "list", str(y), "--branch", "main"])
    assert "queued" in r.stdout and "q1" in r.stdout


def test_cli_publish_offline_exits_0_and_says_queued(setup, registry, tmp_path, monkeypatch):
    y = tmp_path / "kb.yaml"
    y.write_text(yaml.safe_dump(cfg().model_dump(mode="json", by_alias=True, exclude_none=True)))
    monkeypatch.setenv("KB_HOME", str(setup.kb_home))
    monkeypatch.setattr("kb.sync.client.client_for", lambda *a, **k: offline_client(registry))
    r = CliRunner().invoke(app, ["publish", str(y), "--branch", "main", "q1"])
    assert r.exit_code == 0 and "queued 3 items" in r.stdout
