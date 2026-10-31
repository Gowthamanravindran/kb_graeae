"""Usage: python tests/e2e_support/make_team_homes.py <dir> <registry url> <maint token> <contrib token>
Two QAs on one project: the fixture repos with `production` analysed and pushed to the registry; QA A (maintainer)
in <dir>/home-a with a PR test plan ready, QA B (contributor) in <dir>/home-b with the analysis pulled.
Prints home-a, home-b and the plan id, one per line."""
import json
import shutil
import sys
from pathlib import Path

root = Path(__file__).parents[1]
sys.path[:0] = [str(root), str(root / "testplan"), str(root / "serve")]

from fakes import RecordingTracer  # noqa: E402
from design_fake import DesignFake  # noqa: E402
from fixture_repos import analyse_base, make_pr_fixture  # noqa: E402
from kb.config import load_config  # noqa: E402
from kb.packs.store import PackStore  # noqa: E402
from kb.sync.client import RegistryClient  # noqa: E402
from kb.sync.commands import push_pack  # noqa: E402
from kb.sync.credentials import save_token  # noqa: E402
from kb.testplan.graph_impact import ChangeSetRequest, run_impact  # noqa: E402

d, url, maint, contrib = Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
fx = make_pr_fixture(d / "fx")
cfg_path = d / "mini.kb.yaml"
cfg_path.write_text(fx.cfg_path.read_text() + f"registry: {url}\n")
home_a, home_b = d / "home-a", d / "home-b"
cfg_a, store_a = analyse_base(fx, home_a)
cfg = load_config(cfg_path)
push_pack(cfg, "production", store_a, RegistryClient(url, maint))
store_b = PackStore(home_b)
shutil.copytree(store_a.pack_dir("mini", "production"), store_b.pack_dir("mini", "production"),
                ignore=shutil.ignore_patterns("testplans"))
rep = run_impact(cfg, ChangeSetRequest(project="mini", base=dict(fx.base), head=dict(fx.head)), store_a, DesignFake(),
                 RecordingTracer())
for home, token in ((home_a, maint), (home_b, contrib)):
    save_token(home, url, token, "static")
    (home / "desktop.json").write_text(json.dumps({"projects": [{"config_path": str(cfg_path)}]}))
print(home_a)
print(home_b)
print(rep.change_set_id)
