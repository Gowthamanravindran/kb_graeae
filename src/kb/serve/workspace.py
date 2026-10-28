"""The projects the desktop app knows about: $KB_HOME/desktop.json."""

from __future__ import annotations

import json
from pathlib import Path

from kb.config import ProjectConfig, load_config
from kb.errors import ConfigError, KbError


class Workspace:
    def __init__(self, kb_home: Path) -> None:
        self.path = Path(kb_home) / "desktop.json"

    def _paths(self) -> list[str]:
        if not self.path.exists():
            return []
        return [p["config_path"] for p in json.loads(self.path.read_text()).get("projects", [])]

    def _save(self, paths: list[str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"projects": [{"config_path": p} for p in paths]}, indent=1))

    def projects(self) -> list[dict]:
        out = []
        for p in self._paths():
            try:
                cfg = load_config(Path(p))
                out.append({"name": cfg.project, "config_path": p, "registry": cfg.registry,
                            "repos": [{"name": r.name, "role": r.role} for r in cfg.repos],
                            "linkers": [lk.type for lk in cfg.linkers]})
            except ConfigError as e:
                out.append({"name": Path(p).stem, "config_path": p, "registry": None, "error": str(e)})
        return out

    def add(self, config_path: Path) -> dict:
        path = str(Path(config_path).expanduser().resolve())
        cfg = load_config(Path(path))
        paths = self._paths()
        same_name = [p for p in self.projects() if p["name"] == cfg.project and p["config_path"] != path]
        if same_name:
            raise KbError(f"a project named {cfg.project} is already added from {same_name[0]['config_path']}")
        if path not in paths:
            self._save(paths + [path])
        return next(p for p in self.projects() if p["config_path"] == path)

    def remove(self, name: str) -> None:
        keep = [p["config_path"] for p in self.projects() if p["name"] != name]
        self._save(keep)

    def config(self, name: str) -> ProjectConfig:
        for p in self.projects():
            if p["name"] == name and "error" not in p:
                return load_config(Path(p["config_path"]))
        raise KbError(f"unknown project: {name}")
