"""Link REST/HTTP calls in client repos (`from`) to the routes that serve them in server repos (`to`).

Server routes:
- Express / Koa-router / Fastify style: `app.get('/users/:id', handler)`, `router.post(...)`,
  `router.route('/users').get(handler)`, `fastify.route({method, url, handler})`;
- NestJS: `@Controller('users')` with `@Get(':id')`, `@Post()`, … on its methods.
Client calls: `fetch(url, {method})`, `axios(url | {url, method})`, `axios.get(url)` and the same on any client
object (`api.get`, `this.http.post`, `apiClient.delete`, …), with a string or template URL.

URLs are compared segment by segment from the end, so a router mounted under `/api` (`app.use('/api', r)`) or a
client with a base URL still meets: the route's segments must match the end of the call's path, `:id`, `{id}`
and `${…}` match any one segment, and at least one fixed segment must match. The route matching the most fixed
segments wins. Calls to a path no route matches are recorded as unresolved; an absolute URL no route matches
is taken to be an outside service and skipped, as are URLs held in variables.
Edges: client file -calls_http-> route node (`<repo>:http:<METHOD> <path>`, kind `http_route`) -served_by->
handler. Only paths and names are stored, never other source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, handler_node, method_node, register_linker

METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "all")
NEST = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD",
        "Options": "OPTIONS", "All": "ALL"}
_SERVER = re.compile(r"(^app$|^server$|^api$|^r$|router$|routes$|^fastify$|app$)")
_CLIENT = re.compile(r"(axios|http|api|client|request|fetcher|ky|got|agent|instance)$")
_PARAM = re.compile(r"^(:\w+\??|\{\w+\}|\*|\[\.{0,3}\w+\])$")


def segments(path: str) -> list[str]:
    """`https://h/api/users/${id}?x=1` -> ['api', 'users', '*']; params and substitutions become '*'."""
    path = re.sub(r"^[a-z]+://[^/]+", "", path.strip())
    path = re.split(r"[?#]", path, 1)[0]
    segs = [s for s in path.split("/") if s]
    out = ["*" if _PARAM.match(s) or "*" in s or "${" in s or s.startswith(":") else s for s in segs]
    while out and out[0] == "*":   # `${BASE}/users`: the base URL is not part of the route
        out.pop(0)
    return out


def score(route: list[str], call: list[str]) -> int:
    """Fixed segments that match when the route lines up with the end of the call's path; -1 if it can't."""
    if not route or len(route) > len(call):
        return -1
    fixed = 0
    for r, c in zip(reversed(route), reversed(call)):
        if r == "*" or c == "*":
            continue
        if r != c:
            return -1
        fixed += 1
    return fixed if fixed else -1


def _join(*parts: str) -> str:
    return "/" + "/".join(p.strip("/") for p in parts if p and p.strip("/"))


@dataclass
class Route:
    method: str
    path: str
    file: tuple[str, str]
    line: int
    handler: str = ""
    nest: tuple[str, str, int] | None = None

    @property
    def name(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class ClientCall:
    method: str
    path: str
    file: tuple[str, str]
    line: int
    external: bool = False   # an absolute URL: when no route matches, it's an outside service (Stripe, Google…)


@register_linker
class RestLinker:
    type = "rest"

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        routes: list[Route] = []
        calls: list[ClientCall] = []
        defs: dict = {}
        for repo in ctx.config.tos():
            for rel, tree in self._files(ctx, repo):
                for name, line in ts.top_level_names(tree):
                    defs.setdefault(name, []).append((rel, line))
                routes += self.routes(tree, rel)
        for repo in ctx.config.froms():
            for rel, tree in self._files(ctx, repo):
                calls += self.calls(tree, rel)
        return self.edges(ctx, routes, calls, defs)

    @staticmethod
    def _files(ctx, repo):
        root = ctx.checkouts[repo].root
        for path in ts.iter_source_files(root, list(ctx.ignore)):
            tree = ts.parse(path)
            if tree is not None:
                yield (repo, path.relative_to(root).as_posix()), tree

    # ------------------------------------------------------------ server side

    def routes(self, tree, rel) -> list[Route]:
        out = []
        for c in ts.all_calls(tree):
            if c.is_new or not c.args:
                continue
            last = c.receiver.split(".")[-1].lower()
            rn = c.receiver_node
            if c.name in METHODS and rn is not None and rn.type == "call_expression":
                inner = rn.child_by_field_name("function")   # router.route('/users').get(handler)
                iargs = rn.child_by_field_name("arguments")
                if inner is not None and inner.type == "member_expression" and ts.name_of(inner) == "route" \
                        and iargs is not None and iargs.named_children:
                    path = ts.literal(iargs.named_children[0])
                    if path and path.startswith("/"):
                        out.append(Route(c.name.upper(), path, rel, c.line, _name(c.args[-1])))
                continue
            if c.name in METHODS and _SERVER.search(last):
                path = ts.literal(c.args[0])
                if path is not None and path.startswith("/") and len(c.args) >= 2:
                    out.append(Route(c.name.upper(), path, rel, c.line, _name(c.args[-1])))
            elif c.name == "route" and _SERVER.search(last):
                p = ts.props(c.args[0])
                path, method = ts.literal(p.get("url") or p.get("path")), ts.literal(p.get("method"))
                if path and path.startswith("/"):
                    out.append(Route((method or "ALL").upper(), path, rel, c.line, _name(p.get("handler"))))
        for m in ts.decorated_methods(tree):
            if "Controller" not in m.cls_decorators:
                continue
            base = m.cls_decorators["Controller"]
            prefix = ts.literal(base) or ts.literal(ts.props(base).get("path")) or "" if base is not None else ""
            for dec, verb in NEST.items():
                if dec in m.decorators:
                    sub = ts.literal(m.decorators[dec]) if m.decorators[dec] is not None else ""
                    if sub is not None:
                        out.append(Route(verb, _join(prefix, sub), rel, m.line, nest=(m.cls, m.method, m.line)))
        return out

    # ------------------------------------------------------------ client side

    def calls(self, tree, rel) -> list[ClientCall]:
        out = []
        for c in ts.all_calls(tree):
            if c.is_new or not c.args:
                continue
            url, method = None, None
            if c.receiver == "" and c.name == "fetch":
                url = ts.literal(c.args[0])
                opts = ts.props(c.args[1]) if len(c.args) > 1 else {}
                method = ts.literal(opts.get("method")) or "GET"
            elif (c.receiver == "" and c.name == "axios") or (c.name == "request" and _CLIENT.search(c.receiver.lower())):
                p = ts.props(c.args[0])
                if p:
                    url, method = ts.literal(p.get("url")), ts.literal(p.get("method")) or "GET"
                elif c.name == "axios":
                    url = ts.literal(c.args[0])
                    method = ts.literal(ts.props(c.args[1]).get("method")) if len(c.args) > 1 else None
                    method = method or "GET"
            elif c.name in METHODS and c.name != "all" and c.receiver and \
                    _CLIENT.search(c.receiver.split(".")[-1].lower().lstrip("$")):
                url, method = ts.literal(c.args[0]), c.name
            if url is None or not (url.startswith("/") or url.startswith("*") or re.match(r"^https?://", url)):
                continue
            segs = segments(url)
            if segs and any(s != "*" for s in segs):
                out.append(ClientCall(method.upper(), "/" + "/".join(segs), rel, c.line,
                                      bool(re.match(r"^https?://", url))))
        return out

    # ------------------------------------------------------------ matching

    def edges(self, ctx, routes: list[Route], calls: list[ClientCall], defs) -> list[CodeEdge]:
        g = ctx.graph
        edges: list[CodeEdge] = []
        nodes: dict[tuple[str, str], str] = {}
        seen: set[tuple[str, str]] = set()

        def route_node(r: Route) -> str:
            key = (r.file[0], r.name)
            if key not in nodes:
                nodes[key] = ensure_node(g, CodeNode(id=f"{r.file[0]}:http:{r.name}", repo=r.file[0], kind="http_route",
                                                     name=r.name, file=r.file[1], line=r.line))
            nid = nodes[key]
            dst = method_node(g, r.file, *r.nest) if r.nest else handler_node(g, defs, r.file, r.handler)
            if (nid, dst) not in seen:
                seen.add((nid, dst))
                edges.append(CodeEdge(src=nid, dst=dst, kind="served_by", origin="linker", label=r.name))
            return nid

        for r in routes:
            route_node(r)
        segs = [(r, segments(r.path)) for r in routes]
        sent: set[tuple[str, str]] = set()
        for c in calls:
            cs = segments(c.path)
            scored = [(score(s, cs), r) for r, s in segs if r.method in (c.method, "ALL")]
            best = max((s for s, _ in scored), default=-1)
            src = ensure_file_node(g, *c.file)
            if best < 0:
                if c.external:
                    continue
                if (src, f"{c.method} {c.path}") not in sent:
                    sent.add((src, f"{c.method} {c.path}"))
                    edges.append(CodeEdge(src=src, dst=f"unresolved:http:{c.method} {c.path}", kind="calls_http",
                                          origin="linker", resolved=False, label=f"{c.method} {c.path}"))
                continue
            for s, r in scored:
                nid = route_node(r)
                if s == best and (src, nid) not in sent:
                    sent.add((src, nid))
                    edges.append(CodeEdge(src=src, dst=nid, kind="calls_http", origin="linker", label=r.name))
        return edges


def _name(n) -> str:
    return ts.name_of(n) if n is not None and n.type in ("identifier", "member_expression") else ""
