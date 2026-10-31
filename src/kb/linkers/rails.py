"""Rails for the REST linker: the routes `config/routes.rb` declares, and the HTTP calls Ruby code makes.

Routes: `resources` / `resource` (with `only:` / `except:`, `path:`, `controller:`, `param:`, nesting, `member` and
`collection` blocks, `on:`), `namespace`, `scope` (path and `module:`), `controller … do`, verbs with `to:`,
`'path' => 'c#a'`, `controller:` / `action:`, `match … via:`, `root`, `concern` / `concerns:`, and
`draw(:name)` (config/routes/name.rb). Each route points at its controller action
(`app/controllers/api/v1/users_controller.rb`, `Api::V1::UsersController#show`); every Rails app in the repo
(any `config/routes.rb`) is read.

Calls: Faraday (`conn.get('/x')`, `Faraday.get`), HTTParty (`HTTParty.get`, `self.class.get`, bare `get` in a class
that includes HTTParty), RestClient (`RestClient.get`, `RestClient::Request.execute(method:, url:)`), http.rb
(`HTTP.get`), Typhoeus (`Typhoeus.get`, `Typhoeus::Request.new(url, method:)`), Excon, and Net::HTTP
(`Net::HTTP.get(URI(…))`, `Net::HTTP::Post.new(path)`). A path relative to a client's base URL (`conn.get('charges')`)
is matched like any other, but is never reported as unmatched (it may be any outside service).
"""

from __future__ import annotations

import re
from pathlib import Path

from kb.linkers import rb
from kb.linkers.web import ClientCall, Route, segments

VERBS = ("get", "post", "put", "patch", "delete")
REST_ACTIONS = {  # action -> (method, path after the collection, needs :id)
    "index": ("GET", "", False), "create": ("POST", "", False), "new": ("GET", "new", False),
    "edit": ("GET", "edit", True), "show": ("GET", "", True), "update": ("PATCH", "", True),
    "destroy": ("DELETE", "", True),
}
SINGULAR_ACTIONS = ("show", "create", "new", "edit", "update", "destroy")


def camelize(path: str) -> str:
    """`api/v1/users` -> `Api::V1::Users`"""
    return "::".join("".join(w.capitalize() for w in part.split("_")) for part in path.split("/") if part)


def singular(name: str) -> str:
    if name.endswith("ies"):
        return name[:-3] + "y"
    if name.endswith(("sses", "shes", "ches", "xes")):
        return name[:-2]
    return name[:-1] if name.endswith("s") and not name.endswith("ss") else name


def plural(name: str) -> str:
    if name.endswith("y") and name[-2:-1] not in "aeiou":
        return name[:-1] + "ies"
    return name if name.endswith("s") else name + ("es" if name.endswith(("sh", "ch", "x")) else "s")


def _names(n) -> list[str]:
    """`:a`, `'a'`, `[:a, :b]`, `%i[a b]` -> names"""
    if n is None:
        return []
    if n.type in ("array", "symbol_array", "string_array"):
        return [v for c in n.named_children if (v := rb.literal(c)) is not None]
    v = rb.literal(n)
    return [v] if v is not None else []


class _Scope:
    """`path` is the full URL prefix; `ns_path` the part from namespaces and scopes only (no parent resources),
    which is where shallow member routes go."""

    def __init__(self, path=(), module=(), controller=None, resource=None, ns_path=(), shallow=False):
        self.path, self.module, self.controller, self.resource = tuple(path), tuple(module), controller, resource
        self.ns_path, self.shallow = tuple(ns_path), shallow

    def child(self, path=(), module=(), controller=None, resource=None, nest=False, shallow=None):
        add = tuple(p for p in path if p)
        return _Scope(self.path + add, self.module + tuple(m for m in module if m),
                      controller if controller is not None else self.controller,
                      resource if resource is not None else self.resource,
                      self.ns_path if nest else self.ns_path + add,
                      self.shallow if shallow is None else shallow)

    @property
    def nested(self) -> bool:
        return self.path != self.ns_path


def _clean(p: str) -> list[str]:
    """Path pieces, without optional `(…)` parts and the format suffix."""
    p = re.sub(r"\([^)]*\)", "", p or "")
    return [s for s in p.split("/") if s]


class RoutesReader:
    def __init__(self, app: Path, repo: str, repo_root: Path):
        self.app, self.repo, self.repo_root = app, repo, repo_root
        self.out: list[Route] = []
        self.concerns: dict[str, object] = {}
        self.file = ""

    def rel(self, p: Path) -> str:
        return p.relative_to(self.repo_root).as_posix()

    def read(self) -> list[Route]:
        self.read_file(self.app / "config" / "routes.rb", _Scope())
        return self.out

    def read_file(self, path: Path, scope: _Scope) -> None:
        tree = rb.parse(path)
        if tree is None:
            return
        prev, self.file = self.file, self.rel(path)
        for c in rb.calls(tree):
            if c.name == "draw" and c.block is not None:   # Rails.application.routes.draw do … end
                self.body(c.block, scope)
                break
        else:
            self.body(tree.root_node, scope)   # config/routes/<name>.rb: the statements themselves
        self.file = prev

    # ------------------------------------------------------------ statements

    def body(self, block, scope: _Scope) -> None:
        for c in rb.statements(block):
            self.statement(c, scope)

    def statement(self, c: rb.Call, scope: _Scope) -> None:
        n = c.name
        if c.receiver:
            return
        if n == "namespace" and c.args:
            name = rb.literal(c.args[0]) or ""
            path = rb.literal(c.kwargs.get("path")) if "path" in c.kwargs else name
            mod = rb.literal(c.kwargs.get("module")) if "module" in c.kwargs else name
            self.body(c.block, scope.child(path=_clean(path or ""), module=(mod,)))
        elif n == "scope":
            path = rb.literal(c.args[0]) if c.args else rb.literal(c.kwargs.get("path"))
            mod = rb.literal(c.kwargs.get("module"))
            self.body(c.block, scope.child(path=_clean(path or ""), module=(mod,) if mod else ()))
        elif n in ("constraints", "defaults", "with_options"):
            self.body(c.block, scope)
        elif n == "shallow":
            self.body(c.block, scope.child(shallow=True))
        elif n == "controller" and c.args:
            self.body(c.block, scope.child(controller=rb.literal(c.args[0])))
        elif n == "concern" and c.args and c.block is not None:
            self.concerns[rb.literal(c.args[0]) or ""] = c.block
        elif n == "draw" and c.args:
            name = rb.literal(c.args[0])
            if name:
                self.read_file(self.app / "config" / "routes" / f"{name}.rb", scope)
        elif n in ("resources", "resource"):
            for name in [v for a in c.args if (v := rb.literal(a)) is not None]:
                self.resource(c, name, n == "resource", scope)
        elif n in ("member", "collection") and scope.resource is not None:
            base, _, _ = scope.resource
            self.body(c.block, scope.child(path=self._member(scope) if n == "member" else (), resource=(base, n, None)))
        elif n in VERBS + ("match", "root"):
            self.verb(c, scope)

    def _member(self, scope: _Scope) -> tuple[str, ...]:
        _, _, param = scope.resource
        return (f":{param or 'id'}",)

    def resource(self, c: rb.Call, name: str, singular_res: bool, scope: _Scope) -> None:
        only = set(_names(c.kwargs.get("only"))) if "only" in c.kwargs else None
        except_ = set(_names(c.kwargs.get("except")))
        actions = [a for a in (SINGULAR_ACTIONS if singular_res else REST_ACTIONS)
                   if (only is None or a in only) and a not in except_]
        path = rb.literal(c.kwargs.get("path")) or name
        ctrl = rb.literal(c.kwargs.get("controller")) or (plural(name) if singular_res else name)
        mod = rb.literal(c.kwargs.get("module"))
        param = rb.literal(c.kwargs.get("param")) or "id"
        shallow = scope.shallow or (c.kwargs.get("shallow") is not None and rb._text(c.kwargs["shallow"]) == "true")
        inner = scope.child(path=_clean(path), module=(mod,) if mod else (), controller=ctrl, nest=True)
        # shallow nesting: member routes (`/comments/:id`) drop the parent resources, keeping namespaces
        member_base = _Scope(scope.ns_path + tuple(_clean(path)), inner.module, ctrl, None,
                             scope.ns_path + tuple(_clean(path))) if scope.shallow and scope.nested else inner
        for a in actions:
            meth, tail, needs_id = REST_ACTIONS[a]
            base = member_base if needs_id and not singular_res else inner
            segs = list(base.path) + ([f":{param}"] if needs_id and not singular_res else []) + ([tail] if tail else [])
            self.add([meth] + (["PUT"] if a == "update" else []), segs, base, ctrl, a, c.line)
        res = inner.child(resource=(name, "nested", None if singular_res else param), shallow=shallow)
        nested = res if singular_res else res.child(path=(f":{singular(name)}_{param}",), nest=True)
        member = member_base.child(path=() if singular_res else (f":{param}",), resource=(name, "member", param),
                                   shallow=shallow, nest=True)
        ctx = (name, param, res, nested, member)
        for concern in _names(c.kwargs.get("concerns")):
            self.concern(concern, ctx)
        if c.block is not None:
            self.resource_body(rb.statements(c.block), ctx)

    def concern(self, name: str, ctx) -> None:
        if name in self.concerns:
            self.resource_body(rb.statements(self.concerns[name]), ctx)

    def resource_body(self, stmts: list[rb.Call], ctx) -> None:
        """Statements inside `resources :x do … end` or a concern applied to it."""
        name, param, res, nested, member = ctx
        for s in stmts:
            if s.receiver:
                continue
            if s.name == "member":
                self.body(s.block, member)
            elif s.name == "collection":
                self.body(s.block, res.child(resource=(name, "collection", param)))
            elif s.name == "concerns":
                for a in s.args:
                    for concern in _names(a):
                        self.concern(concern, ctx)
            elif s.name in VERBS + ("match",):
                on = rb.literal(s.kwargs.get("on"))
                if on == "member":
                    self.verb(s, member)
                elif on == "collection":
                    self.verb(s, res.child(resource=(name, "collection", param)))
                else:
                    self.verb(s, nested.child(resource=(name, "nested", param)))
            else:
                self.statement(s, nested)

    def verb(self, c: rb.Call, scope: _Scope) -> None:
        if c.name == "root":
            target = rb.literal(c.args[0]) if c.args else rb.literal(c.kwargs.get("to"))
            ctrl, _, action = (target or "").partition("#")
            if ctrl and action:
                self.add(["GET"], list(scope.path), scope, ctrl, action, c.line)
            return
        methods = [c.name.upper()] if c.name != "match" else \
            [m.upper() for m in _names(c.kwargs.get("via")) if m != "all"] or ["ALL"]
        path, target = None, rb.literal(c.kwargs.get("to"))
        if c.rockets:   # get 'ping' => 'health#ping'
            k, v = c.rockets[0]
            path, target = rb.literal(k), target or rb.literal(v)
        elif c.args:
            path = rb.literal(c.args[0])
        if path is None:
            return
        ctrl = rb.literal(c.kwargs.get("controller")) or scope.controller
        action = rb.literal(c.kwargs.get("action"))
        if target and "#" in target:
            ctrl, _, action = target.partition("#")
        elif target and scope.controller:      # `get 'x', to: :action` inside `controller :users do`
            action = target
        if action is None and c.args and c.args[0].type == "simple_symbol":
            action = path                         # `post :ban` inside member/collection: action ban, path /ban
        if action is None and ctrl is None and "/" in path.strip("/"):
            ctrl, _, action = path.strip("/").rpartition("/")   # `get 'photos/search'` -> photos#search
        elif action is None:
            action = path.strip("/").split("/")[-1] or None
        if not ctrl or not action or "*" in action:
            return
        self.add(methods, list(scope.path) + _clean(path), scope, ctrl, action, c.line)

    def add(self, methods: list[str], segs: list[str], scope: _Scope, ctrl: str, action: str, line: int) -> None:
        module = "/".join(m for m in scope.module if m)
        full = ctrl if ctrl.startswith("/") or not module else f"{module}/{ctrl}"
        full = full.lstrip("/")
        cfile = self.app / "app" / "controllers" / f"{full}_controller.rb"
        ref = None
        if cfile.exists():
            ref = ((self.repo, self.rel(cfile)), f"{camelize(full)}Controller", action, 1)
        path = "/" + "/".join(segs)
        for m in methods:
            self.out.append(Route(m, path, (self.repo, self.file), line, ref=ref))


def routes(root: Path, repo: str, ignore: list[str]) -> list[Route]:
    """Routes of every Rails app in the repo (each folder with a config/routes.rb)."""
    out: list[Route] = []
    for p in rb.iter_source_files(root, ignore):
        if p.name == "routes.rb" and p.parent.name == "config":
            out += RoutesReader(p.parent.parent, repo, Path(root)).read()
    return out


# ---------------------------------------------------------------- HTTP calls

_CLIENT_RECV = re.compile(r"(conn|connection|client|http|faraday|api|request|session|agent)$")
_CLIENT_CONSTS = {"Faraday", "HTTParty", "RestClient", "HTTP", "Typhoeus", "Excon"}
_NET_HTTP = re.compile(r"^Net::HTTP::(Get|Post|Put|Patch|Delete)$")


def _url(n) -> str | None:
    """A literal URL or path, also inside `URI(…)` / `URI.parse(…)` / `URI.join(…)`."""
    if n is None:
        return None
    v = rb.literal(n)
    if v is not None:
        return v
    if n.type == "call":
        m, r = n.child_by_field_name("method"), n.child_by_field_name("receiver")
        args = n.child_by_field_name("arguments")
        name = rb._text(m) if m is not None else ""
        if (name == "URI" and r is None) or (r is not None and rb._text(r) == "URI" and name in ("parse", "join")):
            vals = [rb.literal(a) for a in (args.named_children if args is not None else [])]
            vals = [v for v in vals if v is not None]
            return "/".join(v.strip("/") for v in vals) if vals else None
    return None


def http_calls(tree, rel: tuple[str, str]) -> list[ClientCall]:
    text = tree.root_node.text or b""
    httparty = b"include HTTParty" in text
    out = []
    for c in rb.calls(tree):
        url, method = None, None
        recv_last = c.receiver.split(".")[-1].split("::")[-1] if c.receiver else ""
        if c.name in VERBS + ("head",):
            if c.receiver in _CLIENT_CONSTS or c.receiver == "self.class" or \
                    (c.receiver and _CLIENT_RECV.search(recv_last.lower())) or (not c.receiver and httparty):
                url, method = _url(c.args[0]) if c.args else rb.literal(c.kwargs.get("url")), c.name
        elif c.name in ("execute", "new") and c.receiver in ("RestClient::Request", "Typhoeus::Request"):
            url = _url(c.args[0]) if c.args else _url(c.kwargs.get("url"))
            url = url or _url(c.kwargs.get("url"))
            method = rb.literal(c.kwargs.get("method")) or "get"
        elif c.name == "new" and (m := _NET_HTTP.match(c.receiver)):
            url, method = _url(c.args[0]) if c.args else None, m.group(1)
        elif c.receiver == "Net::HTTP" and c.name in ("get", "get_response", "post", "post_form"):
            url, method = (_url(c.args[0]) if c.args else None), ("post" if c.name.startswith("post") else "get")
        if url is None or method is None or " " in url:
            continue
        absolute = bool(re.match(r"^https?://", url))
        if not (url.startswith(("/", "*")) or absolute or re.match(r"^[\w.-]+(/|$)", url)):
            continue
        segs = segments(url)
        if segs and any(s != "*" for s in segs):
            relative = not (url.startswith(("/", "*")))   # absolute, or relative to a base URL
            out.append(ClientCall(method.upper(), "/" + "/".join(segs), rel, c.line, relative))
    return out
