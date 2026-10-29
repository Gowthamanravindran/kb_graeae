import csv
import io

from typer.testing import CliRunner

from kb.packs.store import PackStore
from kb.testplan.export import COLUMNS, export_csv
from kb.testplan.models import BranchRef, CaseSlot, ChangeSet, ImpactFlow, ImpactReport, Scenario, TestCase
from kb.testplan.store import PlanStore

from agent_helpers import build_pack
from design_fake import DesignFake
from fixture_repos import analyse_base, make_pr_fixture


def _plan(tmp_path):
    store, _ = build_pack(tmp_path)
    ps = PlanStore(store)
    ref = {"mini-fe": BranchRef(branch="main", commit="a")}
    ps.save_change_set("main", ChangeSet(id="p1", project="mini", base=ref, head=ref, base_pack="main"))
    ps.save_impact("mini", "main", ImpactReport(change_set_id="p1", head_commits={}, flows=[
        ImpactFlow(id="f1", name="Admin creates a task", entry_points=[], changed_functions=[], inventory_ids=[])]))
    ps.save_scenario("mini", "main", "p1", Scenario(id="sc", flow_id="f1", type="happy_path", title="Create a weekly task"))
    ps.save_slots("mini", "main", "p1", [CaseSlot(id="s1", flow_id="f1", scenario_type="happy_path", priority="P0", status="written", case_id="TC-001"),
                                         CaseSlot(id="s2", flow_id="f1", scenario_type="branches_edges", priority="P2", status="written", case_id="TC-002")])
    ps.save_case("mini", "main", "p1", TestCase(id="TC-001", slot_id="s1", scenario_id="sc", flow_id="f1", title="Creates weekly",
                                                priority="P0", preconditions=["Signed in"], steps=["Open Tasks", "Save"],
                                                test_data=["Title: X"], expected=["Saved"], author="me"))
    ps.save_case("mini", "main", "p1", TestCase(id="TC-002", slot_id="s2", flow_id="f1", title="Edge", priority="P2",
                                                steps=["a"], expected=["b"], author="me"))
    ps.save_case("mini", "main", "p1", TestCase(id="TC-003", suggested=True, status="suggested", title="Extra", steps=["a"], expected=["b"]))
    return ps


def test_csv_columns_bom_and_lists(tmp_path):
    ps = _plan(tmp_path)
    data = export_csv(ps, "mini", "main", "p1")
    assert data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig"))))
    assert rows[0] == COLUMNS == ["ID", "Flow", "Scenario", "Type", "Priority", "Title", "Preconditions", "Steps",
                                  "Test data", "Expected", "Status", "Author", "Stale"]
    assert [r[0] for r in rows[1:]] == ["TC-001", "TC-002"]  # extras excluded
    assert rows[1][7] == "1. Open Tasks\n2. Save" and rows[1][1] == "Admin creates a task" and rows[1][2] == "Create a weekly task"


def test_csv_filters(tmp_path):
    ps = _plan(tmp_path)
    rows = list(csv.reader(io.StringIO(export_csv(ps, "mini", "main", "p1", priorities=["P2"]).decode("utf-8-sig"))))
    assert [r[0] for r in rows[1:]] == ["TC-002"]


def test_cli_pr_then_generate_then_export(tmp_path, monkeypatch):
    from kb.cli import app

    fx = make_pr_fixture(tmp_path / "fx")
    analyse_base(fx, tmp_path / "home")
    monkeypatch.setenv("KB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr("kb.cli.get_provider", lambda: DesignFake())
    r = CliRunner().invoke(app, ["testplan", "pr", str(fx.cfg_path), "--base", "mini-fe=master", "--base", "mini-be=develop",
                                 "--head", "mini-fe=feature/repeat-end", "--head", "mini-be=feature/repeat-end"])
    assert r.exit_code == 0, r.output
    pid = next(line.split()[-1] for line in r.stdout.splitlines() if line.startswith("plan "))
    assert "P0 4" in r.stdout
    r = CliRunner().invoke(app, ["testplan", "generate", str(fx.cfg_path), pid, "--p0", "2"])
    assert r.exit_code == 0, r.output
    assert "wrote 2 cases" in r.stdout
    out = tmp_path / "cases.csv"
    r = CliRunner().invoke(app, ["testplan", "export", str(fx.cfg_path), pid, "-o", str(out)])
    assert r.exit_code == 0, r.output
    rows = list(csv.reader(io.StringIO(out.read_bytes().decode("utf-8-sig"))))
    assert len(rows) == 3
    r = CliRunner().invoke(app, ["testplan", "status", str(fx.cfg_path), pid])
    assert "written 2" in r.stdout
