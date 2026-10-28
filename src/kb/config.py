"""kb.yaml: which repos make up a project, how they link, and where to send traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from kb.errors import ConfigError


class RepoConfig(BaseModel):
    name: str
    role: str
    url: str | None = None
    local_path: Path | None = None
    path: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "RepoConfig":
        if (self.url is None) == (self.local_path is None):
            raise ValueError(f"repo '{self.name}' needs exactly one of 'url' or 'local_path'")
        return self


class LinkerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    from_: str | None = Field(default=None, alias="from")
    to: str | None = None
    in_: str | None = Field(default=None, alias="in")
    options: dict[str, Any] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    project: str
    repos: list[RepoConfig]
    linkers: list[LinkerConfig] = Field(default_factory=list)
    context_docs: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    registry: str | None = None
    langfuse: str | None = None

    @model_validator(mode="after")
    def _references(self) -> "ProjectConfig":
        names = {r.name for r in self.repos}
        for lk in self.linkers:
            for ref in (lk.from_, lk.to, lk.in_):
                if ref is not None and ref not in names:
                    raise ValueError(f"linker '{lk.type}' refers to unknown repo '{ref}'")
        for entry in self.context_docs:
            repo, sep, _ = entry.partition(":")
            if not sep or repo not in names:
                raise ValueError(f"context_docs entry '{entry}' must be '<repo>:<glob>' with a known repo")
        return self

    def repo(self, name: str) -> RepoConfig:
        return next(r for r in self.repos if r.name == name)

    def context_doc_globs(self) -> list[tuple[str, str]]:
        return [tuple(e.split(":", 1)) for e in self.context_docs]  # type: ignore[misc]


def load_config(path: Path) -> ProjectConfig:
    path = Path(path)
    try:
        data = yaml.safe_load(path.read_text())
    except (OSError, yaml.YAMLError) as e:
        raise ConfigError(f"cannot read {path}: {e}") from e
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a mapping")
    try:
        cfg = ProjectConfig.model_validate(data)
    except ValidationError as e:
        msgs = "; ".join(
            f"{'.'.join(str(p) for p in err['loc']) or 'config'}: {err['msg']}" for err in e.errors()
        )
        raise ConfigError(f"invalid {path.name}: {msgs}") from e
    for r in cfg.repos:
        if r.local_path is not None and not r.local_path.is_absolute():
            r.local_path = (path.parent / r.local_path).resolve()
    return cfg
