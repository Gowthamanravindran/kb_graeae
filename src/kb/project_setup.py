"""Add a project from repo links or folders: read each repo, suggest its name, role, branch, package and how
the repos connect, then write the project's kb.yaml under $KB_HOME/projects/<name>/."""

from __future__ import annotations

import fnmatch
import json
import re
import shutil
import subprocess
import uuid
from pathlib import Path

import yaml
from pydantic import BaseModel, Field, ValidationError

from kb.config import LinkerConfig, ProjectConfig
from kb.errors import KbError

FRONTEND = {"react", "react-dom", "next", "vue", "nuxt", "@angular/core", "svelte", "@sveltejs/kit", "preact"}
BACKEND = {"express", "koa", "fastify", "@nestjs/core", "@hapi/hapi", "apollo-server", "apollo-server-express",
           "@apollo/server", "graphql-compose", "mongoose", "typeorm", "prisma", "@prisma/client", "sequelize",
           "bull", "bullmq", "graphql-yoga", "mercurius"}
GRAPHQL_CLIENT = {"@apollo/client", "apollo-client", "react-apollo", "@apollo/react-hooks", "urql", "graphql-request",
                  "@urql/core", "relay-runtime"}
GRAPHQL_SERVER = {"apollo-server", "apollo-server-express", "@apollo/server", "graphql-compose", "@nestjs/graphql",
                  "graphql-yoga", "express-graphql", "type-graphql", "mercurius", "graphql-http"}
REST_SERVER = {"express", "koa", "@koa/router", "koa-router", "fastify", "@nestjs/core", "@hapi/hapi", "restify"}
REST_CLIENT = {"axios", "ky", "got", "superagent", "node-fetch", "cross-fetch", "isomorphic-fetch", "@angular/common"}
KAFKA = {"kafkajs", "@nestjs/microservices-kafka", "node-rdkafka", "@confluentinc/kafka-javascript"}
SQS = {"@aws-sdk/client-sqs", "sqs-consumer", "sqs-producer"}
RABBITMQ = {"amqplib", "amqp-connection-manager", "@golevelup/nestjs-rabbitmq"}
DEFAULT_IGNORE = ["**/*.svg", "**/__generated__/**"]
_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")


def is_url(source: str) -> bool:
    s = source.strip()
    return bool(re.match(r"^(https?|ssh|git|file)://", s) or re.match(r"^[\w.-]+@[\w.-]+:", s))


def _git(cwd: Path, *args: str) -> str:
    r = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, timeout=300,
                       env={**__import__("os").environ, "GIT_TERMINAL_PROMPT": "0"})
    if r.returncode != 0:
        raise KbError(r.stderr.strip().splitlines()[-1] if r.stderr.strip() else f"git {args[0]} failed")
    return r.stdout.strip()


class PathOption(BaseModel):
    path: str
    name: str
    role: str


class RepoInspection(BaseModel):
    source: str
    kind: str  # "url" | "local"
    name: str
    role: str = "other"
    is_git: bool = False
    default_branch: str | None = None
    branches: list[str] = Field(default_factory=list)
    path: str | None = None
    path_options: list[PathOption] = Field(default_factory=list)
    context_docs: list[str] = Field(default_factory=list)
    signals: set[str] = Field(default_factory=set)


class Inspection(BaseModel):
    repos: list[RepoInspection]
    linkers: list[LinkerConfig]


# ---------------------------------------------------------------- reading a repo

def _deps(pkg_dir: Path) -> set[str]:
    try:
        d = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return set()
    out: set[str] = set()
    for k in ("dependencies", "devDependencies", "peerDependencies"):
        if isinstance(d.get(k), dict):
            out |= set(d[k])
    return out


def _signals(deps: set[str]) -> set[str]:
    s = set()
    if deps & FRONTEND:
        s.add("frontend")
    if deps & BACKEND:
        s.add("backend")
    if deps & GRAPHQL_CLIENT:
        s.add("graphql_client")
    if deps & GRAPHQL_SERVER:
        s.add("graphql_server")
    if "graphql-compose" in deps:
        s.add("graphql_compose")
    if deps & {"react-router", "react-router-dom"}:
        s.add("react_router")
    if deps & {"bull", "bullmq"}:
        s.add("bull")
    for name, libs in (("rest_server", REST_SERVER), ("rest_client", REST_CLIENT), ("kafka", KAFKA), ("sqs", SQS),
                       ("rabbitmq", RABBITMQ)):
        if deps & libs:
            s.add(name)
    return s


def _role(signals: set[str]) -> str:
    if signals & {"frontend", "graphql_client"} and not signals & {"backend", "graphql_server"}:
        return "frontend"  # a shared package of GraphQL queries is client code too
    if "backend" in signals:
        return "backend"
    return "other"


def _workspace_globs(root: Path) -> list[str]:
    try:
        d = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    ws = d.get("workspaces")
    globs = ws.get("packages", []) if isinstance(ws, dict) else ws if isinstance(ws, list) else []
    try:  # pnpm
        globs += (yaml.safe_load((root / "pnpm-workspace.yaml").read_text()) or {}).get("packages", [])
    except (OSError, yaml.YAMLError, AttributeError):
        pass
    return [g for g in globs if isinstance(g, str) and not g.startswith("!")]


def _packages(root: Path) -> list[Path]:
    out = []
    for g in _workspace_globs(root):
        g = g.rstrip("/")
        for p in sorted(root.glob(g)):
            rel = p.relative_to(root).as_posix()
            if p.is_dir() and (p / "package.json").exists() and "node_modules" not in p.parts and fnmatch.fnmatch(rel, g):
                out.append(p)
    return sorted(set(out))


_SRC = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".vue", ".svelte")


def _source_files(pkg: Path, cap: int = 20000) -> int:
    n = 0
    for p in pkg.rglob("*"):
        if "node_modules" in p.parts or any(part.startswith(".") for part in p.relative_to(pkg).parts):
            continue
        if p.suffix in _SRC:
            n += 1
            if n >= cap:
                break
    return n


def _clean_name(name: str) -> str:
    name = re.sub(r"\.git$", "", name.split("/")[-1])
    name = re.sub(r"-(main|master)$", "", name)
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", name).strip("-.") or "repo"
    return name[:64]


APP_SIGNALS = {"graphql_client", "graphql_server", "react_router", "bull", "backend", "rest_server", "kafka", "sqs",
               "rabbitmq"}


def _read(root: Path, source: str, kind: str, fallback_name: str) -> list[RepoInspection]:
    """One entry per app. A monorepo gives one entry per package that is an app (calls or serves GraphQL,
    has routes or queues, runs a server), biggest first; component kits and libraries without any of that are
    left out but stay selectable. Anything else is one entry for the whole repo."""
    root_signals = _signals(_deps(root))
    options, apps = [], []
    for p in _packages(root):
        sig = _signals(_deps(p))
        rel = p.relative_to(root).as_posix()
        options.append(PathOption(path=rel, name=_clean_name(p.name), role=_role(sig)))
        if sig & APP_SIGNALS:
            apps.append((_source_files(p), rel, sig))
    docs = []
    if (root / "CONTEXT.md").exists():
        docs.append("CONTEXT.md")
    if (root / "docs" / "adr").is_dir():
        docs.append("docs/adr/**")
    if not apps:
        return [RepoInspection(source=source, kind=kind, name=fallback_name, role=_role(root_signals), path=None,
                               path_options=options, context_docs=docs, signals=root_signals)]
    out = []
    for i, (_, rel, sig) in enumerate(sorted(apps, key=lambda a: (-a[0], a[1]))):
        out.append(RepoInspection(source=source, kind=kind, name=_clean_name(Path(rel).name), role=_role(sig), path=rel,
                                  path_options=options, context_docs=docs if i == 0 else [], signals=sig))
    return out


def _local_branches(root: Path) -> tuple[str | None, list[str]]:
    try:
        current = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    except KbError:
        return None, []
    try:
        default = _git(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD").split("/", 1)[1]
    except KbError:
        default = current if current != "HEAD" else None
    names = _git(root, "for-each-ref", "--format=%(refname)", "refs/heads", "refs/remotes").splitlines()
    branches = []
    for n in names:
        b = re.sub(r"^refs/(heads|remotes/[^/]+)/", "", n)
        if b != "HEAD" and b not in branches:
            branches.append(b)
    return default, sorted(branches)


def _inspect_local(source: str) -> list[RepoInspection]:
    root = Path(source).expanduser()
    if not root.is_dir():
        raise KbError(f"no folder at {source}")
    root = root.resolve()
    entries = _read(root, str(root), "local", _clean_name(root.name))
    top = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=root, capture_output=True, text=True)
    is_git = top.returncode == 0 and Path(top.stdout.strip()).resolve() == root
    default, branches = _local_branches(root) if is_git else (None, [])
    for r in entries:
        r.is_git, r.default_branch, r.branches = is_git, default, list(branches)
    return entries


def _inspect_url(source: str, kb_home: Path) -> list[RepoInspection]:
    url = source.strip()
    scratch = Path(kb_home) / "work" / "_inspect" / uuid.uuid4().hex[:10]
    scratch.parent.mkdir(parents=True, exist_ok=True)
    try:
        try:
            heads = _git(scratch.parent, "ls-remote", "--symref", url, "HEAD", "refs/heads/*")
            _git(scratch.parent, "clone", "--quiet", "--depth", "1", url, str(scratch))
        except (KbError, subprocess.TimeoutExpired) as e:
            raise KbError(f"could not read {url}: {e}. For a private repo, check that `git clone {url}` works "
                          "in Terminal (your git sign-in is used).") from e
        entries = _read(scratch, url, "url", _clean_name(url.rstrip("/")))
        m = re.search(r"^ref: refs/heads/(\S+)\s+HEAD$", heads, re.M)
        branches = sorted({ln.split("refs/heads/", 1)[1] for ln in heads.splitlines() if "\trefs/heads/" in ln})
        for r in entries:
            r.is_git, r.default_branch, r.branches = True, (m.group(1) if m else None), list(branches)
        return entries
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def _one_or_many(names: list[str]) -> str | list[str]:
    return names[0] if len(names) == 1 else names


def suggest_linkers(repos: list[RepoInspection]) -> list[LinkerConfig]:
    """One link of each kind covering every repo it applies to: all apps that call GraphQL against all services
    that serve it, routes in every app that has them, REST calls from every app (and every service with an HTTP
    client) to every HTTP service, and queues and brokers matched across every service that uses them."""
    out: list[LinkerConfig] = []
    clients = [r for r in repos if "graphql_client" in r.signals]
    servers = [r for r in repos if "graphql_server" in r.signals and r not in clients]
    if clients and servers:
        styles = {r.name: "graphql-compose" if "graphql_compose" in r.signals else "sdl" for r in servers}
        style = next(iter(set(styles.values()))) if len(set(styles.values())) == 1 else styles
        out.append(LinkerConfig(type="graphql", from_=_one_or_many([r.name for r in clients]),
                                to=_one_or_many([r.name for r in servers]), options={"backend_style": style}))
    routed = [r.name for r in repos if "react_router" in r.signals]
    if routed:
        out.append(LinkerConfig(type="react-router", in_=_one_or_many(routed)))
    queued = [r.name for r in repos if "bull" in r.signals]
    if queued:
        out.append(LinkerConfig(type="bull-queue", in_=_one_or_many(queued)))
    rest_servers = [r.name for r in repos if "rest_server" in r.signals]
    # any frontend may call with plain fetch; a service only when it has an HTTP client library
    rest_clients = [r.name for r in repos if r.signals & {"rest_client", "frontend"} or r.role == "frontend"]
    if rest_servers and rest_clients:
        out.append(LinkerConfig(type="rest", from_=_one_or_many(rest_clients), to=_one_or_many(rest_servers)))
    for broker in ("kafka", "sqs", "rabbitmq"):
        users = [r.name for r in repos if broker in r.signals]
        if users:
            out.append(LinkerConfig(type=broker, in_=_one_or_many(users)))
    return out


def inspect_repos(sources: list[str], kb_home: Path, progress=None) -> Inspection:
    repos: list[RepoInspection] = []
    for i, s in enumerate(x for x in sources if x.strip()):
        if progress:
            progress(f"Reading {s.strip()}", 0.1 + 0.8 * i / max(len(sources), 1))
        for r in (_inspect_url(s, kb_home) if is_url(s) else _inspect_local(s.strip())):
            base, n = r.name, 2
            while any(x.name == r.name for x in repos):
                r.name, n = f"{base}-{n}", n + 1
            repos.append(r)
    if not repos:
        raise KbError("add at least one repo")
    return Inspection(repos=repos, linkers=suggest_linkers(repos))


# ---------------------------------------------------------------- writing kb.yaml

class NewRepo(BaseModel):
    name: str
    role: str
    source: str  # a git URL or a folder
    path: str | None = None
    default_branch: str | None = None

    @classmethod
    def from_inspection(cls, r: RepoInspection) -> "NewRepo":
        return cls(name=r.name, role=r.role, source=r.source, path=r.path, default_branch=r.default_branch)


class NewProject(BaseModel):
    project: str
    repos: list[NewRepo]
    linkers: list[LinkerConfig] = Field(default_factory=list)
    context_docs: list[str] = Field(default_factory=list)
    ignore: list[str] = Field(default_factory=lambda: list(DEFAULT_IGNORE))


def project_yaml(spec: NewProject) -> dict:
    if not _NAME.match(spec.project or ""):
        raise KbError("the project name can use letters, digits, '.', '_' and '-' (up to 64)")
    if not spec.repos:
        raise KbError("add at least one repo")
    seen = set()
    repos = []
    for r in spec.repos:
        if not _NAME.match(r.name or ""):
            raise KbError(f"repo name {r.name!r}: use letters, digits, '.', '_' and '-'")
        if r.name in seen:
            raise KbError(f"two repos named {r.name}")
        seen.add(r.name)
        d: dict = {"name": r.name, "role": r.role or "other"}
        if is_url(r.source):
            d["url"] = r.source.strip()
        else:
            p = Path(r.source.strip()).expanduser()
            if not p.is_dir():
                raise KbError(f"repo {r.name}: no folder at {r.source}")
            d["local_path"] = str(p.resolve())
        if r.path:
            d["path"] = r.path.strip("/")
        if r.default_branch:
            d["default_branch"] = r.default_branch.strip()
        repos.append(d)
    data: dict = {"project": spec.project, "repos": repos}
    if spec.linkers:
        data["linkers"] = [lk.model_dump(by_alias=True, exclude_none=True, exclude_defaults=False)
                           for lk in spec.linkers]
        for lk in data["linkers"]:
            if not lk.get("options"):
                lk.pop("options", None)
    if spec.context_docs:
        data["context_docs"] = list(spec.context_docs)
    if spec.ignore:
        data["ignore"] = list(spec.ignore)
    try:
        ProjectConfig.model_validate(data)
    except ValidationError as e:
        raise KbError(f"that project isn't valid: {e.errors()[0]['msg']}") from e
    return data


def write_project(kb_home: Path, spec: NewProject) -> Path:
    data = project_yaml(spec)
    path = Path(kb_home) / "projects" / spec.project / "kb.yaml"
    if path.exists():
        raise KbError(f"a project named {spec.project} already exists ({path})")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("# Written by KB's Add project. You can edit it; KB reads it on every run.\n"
                    + yaml.safe_dump(data, sort_keys=False, allow_unicode=True))
    return path
