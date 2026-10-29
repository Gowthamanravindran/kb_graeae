"""Usage: python tests/e2e_support/make_pr_home.py <dir> — the stage 5a fixture repos (git history, a PR branch)
with their base analysed as `production`, in a KB_HOME at <dir>/home listed in desktop.json. Prints the home."""
import json
import sys
from pathlib import Path

root = Path(__file__).parents[1]
sys.path[:0] = [str(root), str(root / "testplan"), str(root / "serve")]

from fixture_repos import analyse_base, make_pr_fixture  # noqa: E402

d = Path(sys.argv[1])
fx = make_pr_fixture(d / "fx")
home = d / "home"
analyse_base(fx, home)
(home / "desktop.json").write_text(json.dumps({"projects": [{"config_path": str(fx.cfg_path)}]}))
print(home)
