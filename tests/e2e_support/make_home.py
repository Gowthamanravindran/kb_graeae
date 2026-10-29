"""Usage: python tests/e2e_support/make_home.py <dir> — a KB_HOME at <dir>/home with the mini pack and desktop.json."""
import json
import sys
from pathlib import Path

root = Path(__file__).parents[1]
sys.path[:0] = [str(root), str(root / "serve"), str(root / "registry"), str(root / "sync")]

from agent_helpers import build_pack  # noqa: E402
from serve_helpers import write_cfg  # noqa: E402

d = Path(sys.argv[1])
d.mkdir(parents=True, exist_ok=True)
build_pack(d)
registry = sys.argv[2] if len(sys.argv) > 2 else None
cfg = write_cfg(d, registry=registry)
(d / "home" / "desktop.json").write_text(json.dumps({"projects": [{"config_path": str(cfg)}]}))
print(d / "home")
