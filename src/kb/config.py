"""kb.yaml: which repos make up a project, how they link, and where to send traces."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from kb.errors import ConfigError


class RepoConfig(BaseModel):
    name: str
    role: str
    url: str | None = None
    local_path: Path | None = None
    path: str | None = None
    default_branch: str | None = None

    @model_validator(mode="after")
    def _one_source(self) -> "RepoConfig":
        if (self.url is None) == (self.local_path is None):
            raise ValueError(f"repo '{self.name}' needs exactly one of 'url' or 'local_path'")
        return self


class LinkerConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    type: str
    # one repo or a list: several apps calling several services, or queues shared by several services
    from_: str | list[str] | None = Field(default=None, alias="from")
    to: str | list[str] | None = None
    in_: str | list[str] | None = Field(default=None, alias="in")
    options: dict[str, Any] = Field(default_factory=dict)

    @staticmethod
    def _list(v: str | list[str] | None) -> list[str]:
        return [] if v is None else [v] if isinstance(v, str) else list(v)

    def froms(self) -> list[str]:
        return self._list(self.from_)

    def tos(self) -> list[str]:
        return self._list(self.to)

    def ins(self) -> list[str]:
        return self._list(self.in_)


class ProjectConfig(BaseModel):
    project: str
    repos: list[RepoConfig]
    linkers: list[LinkerConfig] = Field(default_factory=list)
    context_docs: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=list)
    registry: str | None = None
    langfuse: str | None = None

    @field_validator("registry")
    @classmethod
    def _registry_url(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from urllib.parse import urlparse

        u = urlparse(v.strip())
        if u.scheme not in ("http", "https") or not u.netloc:
            raise ValueError(f"registry must be an http(s) URL like https://kb.example.com, got '{v}'")
        return v.strip().rstrip("/")

    @model_validator(mode="after")
    def _references(self) -> "ProjectConfig":
        names = {r.name for r in self.repos}
        for lk in self.linkers:
            for ref in lk.froms() + lk.tos() + lk.ins():
                if ref not in names:
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


BranchSet = dict[str, str]


def resolve_branch_set(cfg: "ProjectConfig", branch_args: list[str], label: str | None = None) -> tuple[str, BranchSet]:
    """`--branch main` → the same name in every repo (label "main"). `--branch web=master --branch be=develop`
    → a branch per repo (a repo left out uses its default_branch) stored under `label`."""
    names = [r.name for r in cfg.repos]
    plain = [a for a in branch_args if "=" not in a]
    pairs = [a.split("=", 1) for a in branch_args if "=" in a]
    if plain and not pairs and len(plain) == 1:
        return label or plain[0], {n: plain[0] for n in names}
    if plain:
        raise ConfigError("use either one --branch NAME or --branch repo=branch for each repo, not both")
    chosen: BranchSet = {}
    for repo, br in pairs:
        if repo not in names:
            raise ConfigError(f"unknown repo '{repo}' (repos: {', '.join(names)})")
        chosen[repo] = br.strip()
    for r in cfg.repos:
        if r.name not in chosen:
            if not r.default_branch:
                raise ConfigError(f"no branch for {r.name}: add --branch {r.name}=BRANCH or default_branch in kb.yaml")
            chosen[r.name] = r.default_branch
    if len(set(chosen.values())) == 1 and not label:
        return next(iter(chosen.values())), chosen
    if not label:
        raise ConfigError("give --label for different branches per repo (for example --label production)")
    return label, chosen
