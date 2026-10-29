import json
from collections import Counter

import pytest

from fakes import FakeProvider, RecordingTracer
from kb.errors import KbError
from kb.testplan.graph_impact import ChangeSetRequest, run_impact
from kb.testplan.store import PlanStore

from fixture_repos import HEAD, RESOLVERS, analyse_base, git, make_pr_fixture

NAMES = json.dumps({"names": {}})


def _req(fx, head=None):
    return ChangeSetRequest(project="mini", base=dict(fx.base), head=head if head is not None else dict(fx.head))


def _names(flow_names: dict | None = None):
    return FakeProvider(default="```json\n" + json.dumps({"names": flow_names or {}}) + "\n```")


@pytest.fixture
def env(tmp_path):
    fx = make_pr_fixture(tmp_path / "fx")
    cfg, store = analyse_base(fx, tmp_path / "home")
    return fx, cfg, store


def test_impact_fixture_end_to_end(env):
    fx, cfg, store = env
    rep = run_impact(cfg, _req(fx), store, _names(), RecordingTracer(), author="me")
    assert len(rep.flows) == 1 and len(rep.inventory) == 5
    ps = PlanStore(store)
    slots = ps.load_slots("mini", "production", rep.change_set_id)
    non_reg = Counter(s.priority for s in slots if s.rule != "regression")
    assert non_reg == Counter({"P0": 4, "P1": 3, "P2": 2, "P3": 1})
    cs = ps.load_change_set("mini", "production", rep.change_set_id)
    assert cs.base["mini-be"].branch == "develop" and cs.head["mini-be"].branch == HEAD
    assert ps.load_impact("mini", "production", rep.change_set_id).flows[0].est_claude_calls > 0


def test_flow_names_from_claude(env):
    fx, cfg, store = env
    rep = run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    fid = rep.flows[0].id
    rep = run_impact(cfg, _req(fx), store, _names({fid: "Admin creates a weekly task"}), RecordingTracer())
    assert rep.flows[0].name == "Admin creates a weekly task"


def test_name_flows_falls_back_without_claude(env):
    fx, cfg, store = env
    rep = run_impact(cfg, _req(fx), store, FakeProvider(unavailable="not signed in"), RecordingTracer())
    assert rep.flows[0].name == "Screen /tasks/create-task"


def test_impact_refuses_without_base_pack(tmp_path):
    fx = make_pr_fixture(tmp_path / "fx")
    from kb.config import load_config
    from kb.packs.store import PackStore

    with pytest.raises(KbError, match="Analyse mini-fe master · mini-be develop first"):
        run_impact(load_config(fx.cfg_path), _req(fx), PackStore(tmp_path / "home"), _names(), RecordingTracer())


def test_impact_refuses_plain_folders(env, tmp_path):
    fx, cfg, store = env
    plain = tmp_path / "plain"
    plain.mkdir()
    cfg.repos[0].local_path = plain
    with pytest.raises(KbError, match="PR analysis needs git repos for: mini-fe"):
        run_impact(cfg, _req(fx), store, _names(), RecordingTracer())


def test_blank_head_repo_contributes_no_diff_but_is_reachable(env):
    fx, cfg, store = env
    rep = run_impact(cfg, _req(fx, {"mini-fe": None, "mini-be": HEAD}), store, _names(), RecordingTracer())
    assert not any(i.repo == "mini-fe" for i in rep.inventory)
    assert rep.flows[0].entry_points == ["mini-fe:route:/tasks/create-task"]
    assert rep.head_commits["mini-fe"] == git(fx.web, "rev-parse", "master")


def test_reconcile_after_new_commit(env):
    fx, cfg, store = env
    ps = PlanStore(store)
    rep = run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    pid = rep.change_set_id
    from kb.testplan.models import TestCase

    def write(kinds):
        def fn(slots):
            inv = {i.id: i.kind for i in rep.inventory}
            for s in slots:
                if s.inventory_ids and inv[s.inventory_ids[0]] in kinds:
                    s.status, s.case_id = "written", f"TC-{s.id[:3]}"
                    ps.save_case("mini", "production", pid, TestCase(id=s.case_id, slot_id=s.id, title="t"))
        ps.update_slots("mini", "production", pid, fn)
    write({"validation", "permission", "branch"})
    # change only the validation's error message
    git(fx.be, "checkout", "-q", HEAD)
    f = fx.be / RESOLVERS
    f.write_text(f.read_text().replace("End date must be after the start date", "End date is before the start"))
    git(fx.be, "commit", "-qam", "message")
    git(fx.be, "checkout", "-q", "develop")
    rep2 = run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    assert rep2.change_set_id == pid
    kinds = {i.id: i.kind for i in rep2.inventory}
    st = {(kinds[s.inventory_ids[0]], s.outcome): s.status for s in ps.load_slots("mini", "production", pid) if s.inventory_ids and s.inventory_ids[0] in kinds}
    assert st[("validation", "invalid")] == "stale" and st[("validation", "valid")] == "stale"
    # a commit deleting the permission check: its slots become obsolete
    git(fx.be, "checkout", "-q", HEAD)
    f.write_text(f.read_text().replace("    if (!context.user.isAdmin) {\n      throw new Error('Only admins can add tasks');\n    }\n", ""))
    git(fx.be, "commit", "-qam", "no admin check")
    git(fx.be, "checkout", "-q", "develop")
    run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    obsolete = [s for s in ps.load_slots("mini", "production", pid) if s.status == "obsolete"]
    assert len(obsolete) == 2 and all(s.case_id for s in obsolete)
    assert {c.status for c in ps.cases("mini", "production", pid) if c.id in {s.case_id for s in obsolete}} == {"obsolete"}


def test_pr_link_fills_branches(env, monkeypatch):
    fx, cfg, store = env
    from kb.testplan import github

    monkeypatch.setattr(github, "_get", lambda url, token: {
        "title": "Repeat end date", "body": "Fixes #12", "base": {"ref": "develop"}, "head": {"ref": HEAD},
        "base_repo": None})
    req = ChangeSetRequest(project="mini", base={"mini-fe": "master", "mini-be": ""}, head={"mini-fe": HEAD, "mini-be": None},
                           pr_links={"mini-be": "https://github.com/acme/mini-be/pull/7"})
    rep = run_impact(cfg, req, store, _names(), RecordingTracer())
    cs = PlanStore(store).load_change_set("mini", "production", rep.change_set_id)
    assert cs.head["mini-be"].branch == HEAD and cs.prs["mini-be"].title == "Repeat end date"
    assert cs.prs["mini-be"].issues == ["#12"]


def test_pr_link_private_without_token(monkeypatch):
    from kb.testplan import github

    def boom(url, token):
        raise github.HttpError(404)
    monkeypatch.setattr(github, "_get", boom)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    with pytest.raises(KbError, match="Add GITHUB_TOKEN to ~/.kb/kb.env, or pick the branches yourself"):
        github.pr_info("https://github.com/acme/x/pull/1")


def test_reconcile_does_not_lose_a_concurrent_write(env, monkeypatch):
    import threading

    fx, cfg, store = env
    ps = PlanStore(store)
    rep = run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    pid = rep.change_set_id
    target = ps.load_slots("mini", "production", pid)[0].id
    real_cases = PlanStore.cases
    started = {}

    def cases_with_writer(self, *a):
        if "t" not in started:  # while reconcile is running, generation writes a case for one slot
            def write(slots):
                for s in slots:
                    if s.id == target:
                        s.status, s.case_id = "written", "TC-777"
            started["t"] = threading.Thread(target=lambda: ps.update_slots("mini", "production", pid, write))
            started["t"].start()
            started["t"].join(0.5)
        return real_cases(self, *a)
    monkeypatch.setattr(PlanStore, "cases", cases_with_writer)
    run_impact(cfg, _req(fx), store, _names(), RecordingTracer())
    started["t"].join(5)
    s = next(s for s in ps.load_slots("mini", "production", pid) if s.id == target)
    assert (s.status, s.case_id) == ("written", "TC-777")


def test_pr_base_wins_over_default_branch(env, monkeypatch):
    fx, cfg, store = env
    from kb.testplan import github

    cfg.repo("mini-be").default_branch = "master"  # kb.yaml's default isn't this PR's base
    monkeypatch.setattr(github, "_get", lambda url, token: {
        "title": "t", "body": "", "base": {"ref": "develop", "repo": {"full_name": "acme/mini-be"}},
        "head": {"ref": HEAD, "repo": {"full_name": "acme/mini-be"}}})
    req = ChangeSetRequest(project="mini", base={"mini-fe": "master"}, head={"mini-fe": None, "mini-be": None},
                           pr_links={"mini-be": "https://github.com/acme/mini-be/pull/7"})
    rep = run_impact(cfg, req, store, _names(), RecordingTracer())
    cs = PlanStore(store).load_change_set("mini", "production", rep.change_set_id)
    assert cs.base["mini-be"].branch == "develop"


def test_fork_pr_uses_the_pull_ref(monkeypatch):
    from kb.testplan import github

    monkeypatch.setattr(github, "_get", lambda url, token: {
        "title": "t", "body": "", "base": {"ref": "develop", "repo": {"full_name": "acme/mini-be"}},
        "head": {"ref": "my-fix", "repo": {"full_name": "someone/mini-be"}}})
    pr = github.pr_info("https://github.com/acme/mini-be/pull/7")
    assert pr.head_ref == "pull/7/head" and pr.base_ref == "develop"
    monkeypatch.setattr(github, "_get", lambda url, token: {
        "title": "t", "body": "", "base": {"ref": "develop", "repo": {"full_name": "acme/mini-be"}},
        "head": {"ref": "my-fix", "repo": None}})  # the fork was deleted
    assert github.pr_info("https://github.com/acme/mini-be/pull/7").head_ref == "pull/7/head"
