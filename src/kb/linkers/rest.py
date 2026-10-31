"""Link REST/HTTP calls in client repos (`from`) to the routes that serve them in server repos (`to`).

Server routes:
- Express / Koa-router / Fastify style: `app.get('/users/:id', handler)`, `router.post(...)`,
  `router.route('/users').get(handler)`, `fastify.route({method, url, handler})`, hapi `server.route([...])`;
- NestJS: `@Controller('users')` with `@Get(':id')`, `@Post()`, … on its methods.
Client calls: `fetch(url, {method})`, `axios(url | {url, method})`, `axios.get(url)` and the same on any client
object (`api.get`, `this.http.post`, `apiClient.delete`, …), with a string or template URL.

URLs are compared segment by segment from the end, so a router mounted under `/api` (`app.use('/api', r)`) or a
client with a base URL still meets: the route's segments must match the end of the call's path, `:id`, `{id}`
and `${…}` match any one segment, and at least one fixed segment must match. The route matching the most fixed
segments wins, then the one whose parameters meet the call's (`/users/${id}` -> `/users/:id`, not `/users/me`).
Calls to a path no route matches are recorded as unresolved; an absolute URL no route matches is taken to be an
outside service and skipped, as are URLs held in variables. In a repo that both serves and calls, a receiver that
could be either (`api.get(…)`) is a route only in a file that imports a server library.
Edges: client file -calls_http-> route node (`<repo>:http:<METHOD> <path>`, kind `http_route`) -served_by->
handler. Only paths and names are stored, never other source text.
"""

from __future__ import annotations

import re
from collections import Counter

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import rails, rb, ts
from kb.linkers.base import (LinkContext, SymbolIndex, ensure_file_node, ensure_node, handler_node, method_node,
                             register_linker)
from kb.linkers.web import ClientCall, Route, _join, score, segments  # noqa: F401  (re-exported for callers)

METHODS = ("get", "post", "put", "patch", "delete", "head", "options", "all")
NEST = {"Get": "GET", "Post": "POST", "Put": "PUT", "Patch": "PATCH", "Delete": "DELETE", "Head": "HEAD",
        "Options": "OPTIONS", "All": "ALL"}
_SERVER = re.compile(r"(^app$|^server$|^api$|^r$|router$|routes$|^fastify$|app$)")
_SERVER_LIBS = re.compile(rb"""(from|require\()\s*['"](express|koa|@koa/router|koa-router|fastify|restify|@hapi/hapi)['"]""")
_INLINE = ("arrow_function", "function_expression", "function")
_CLIENT = re.compile(r"(axios|http|api|client|request|fetcher|ky|got|agent|instance)$")



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
            routes += rails.routes(ctx.checkouts[repo].root, repo, list(ctx.ignore))
        declared = {(r.file, r.line) for r in routes}   # a repo that both serves and calls: a route isn't a call
        for repo in ctx.config.froms():
            for rel, tree in self._files(ctx, repo):
                calls += [c for c in self.calls(tree, rel) if (c.file, c.line) not in declared]
            root = ctx.checkouts[repo].root
            for path in rb.iter_source_files(root, list(ctx.ignore)):
                if (tree := rb.parse(path)) is not None:
                    rel = (repo, path.relative_to(root).as_posix())
                    calls += [c for c in rails.http_calls(tree, rel) if (c.file, c.line) not in declared]
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
        server_file = bool(_SERVER_LIBS.search(tree.root_node.text or b""))
        for c in ts.all_calls(tree):
            if c.is_new or not c.args:
                continue
            last = c.receiver.split(".")[-1].lower()
            # `api` may be an Express router or an axios client: only a route in a file that uses a server library
            if _SERVER.search(last) and _CLIENT.search(last) and not server_file:
                continue
            rn = c.receiver_node
            if c.name in METHODS and rn is not None and rn.type == "call_expression":
                # router.route('/users').get(a).post(b): walk down the chain to `.route(path)`
                while rn is not None and rn.type == "call_expression":
                    fn = rn.child_by_field_name("function")
                    if fn is None or fn.type != "member_expression" or ts.name_of(fn) not in METHODS:
                        break
                    rn = fn.child_by_field_name("object")
                if rn is None or rn.type != "call_expression":
                    continue
                inner = rn.child_by_field_name("function")
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
                # fastify.route({method, url, handler}); hapi server.route({…} or [{…}, …]), method may be a list
                a = c.args[0]
                for obj in (a.named_children if a.type == "array" else [a]):
                    p = ts.props(obj)
                    path = ts.literal(p.get("url") or p.get("path"))
                    m = p.get("method")
                    methods = [ts.literal(x) for x in m.named_children] if m is not None and m.type == "array" \
                        else [ts.literal(m)]
                    if path and path.startswith("/"):
                        for meth in methods:
                            verb = "ALL" if meth in (None, "*") else meth.upper()
                            out.append(Route(verb, path, rel, obj.start_point[0] + 1, _name(p.get("handler"))))
        for m in ts.decorated_methods(tree):
            if "Controller" not in m.cls_decorators:
                continue
            base = m.cls_decorators["Controller"]
            prefix = ts.literal(base) or ts.literal(ts.props(base).get("path")) or "" if base is not None else ""
            for dec, verb in NEST.items():
                if dec in m.decorators:
                    sub = ts.literal(m.decorators[dec]) if m.decorators[dec] is not None else ""
                    if sub is not None:
                        out.append(Route(verb, _join(prefix, sub), rel, m.line, ref=(rel, m.cls, m.method, m.line)))
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
                    _CLIENT.search(c.receiver.split(".")[-1].lower().lstrip("$")) and c.args[-1].type not in _INLINE:
                url, method = ts.literal(c.args[0]), c.name   # an inline function last means a route, not a call
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
        index = SymbolIndex(g)
        edges: list[CodeEdge] = []
        nodes: dict[tuple[str, str], str] = {}
        seen: set[tuple[str, str]] = set()

        def route_node(r: Route) -> str:
            key = (r.file[0], r.name)
            if key not in nodes:
                nodes[key] = ensure_node(g, CodeNode(id=f"{r.file[0]}:http:{r.name}", repo=r.file[0], kind="http_route",
                                                     name=r.name, file=r.file[1], line=r.line))
            nid = nodes[key]
            dst = method_node(g, r.ref[0], *r.ref[1:], index=index) if r.ref else handler_node(g, defs, r.file, r.handler)
            if (nid, dst) not in seen:
                seen.add((nid, dst))
                edges.append(CodeEdge(src=nid, dst=dst, kind="served_by", origin="linker", label=r.name))
            return nid

        ids = [route_node(r) for r in routes]
        segs = [segments(r.path) for r in routes]
        # index each route by its rarest fixed segment and that segment's place from the end, so a call is only
        # scored against routes that could line up with it
        freq = Counter(s for ss in segs for s in ss if s != "*")
        index: dict[tuple[int, str], list[int]] = {}
        by_place: dict[int, list[int]] = {}
        for i, ss in enumerate(segs):
            fixed = [(freq[s], k, s) for k, s in enumerate(reversed(ss), 1) if s != "*"]
            if fixed:
                _, k, s = min(fixed)
                index.setdefault((k, s), []).append(i)
                by_place.setdefault(k, []).append(i)
        sent: set[tuple[str, str]] = set()
        for c in calls:
            cs = segments(c.path)
            cand: set[int] = set()
            for k in range(1, len(cs) + 1):
                cand.update(by_place.get(k, ()) if cs[-k] == "*" else index.get((k, cs[-k]), ()))
            scored = [(sc, i) for i in cand if routes[i].method in (c.method, "ALL")
                      and (sc := score(segs[i], cs)) is not None]
            src = ensure_file_node(g, *c.file)
            if not scored:
                if c.external:
                    continue
                if (src, f"{c.method} {c.path}") not in sent:
                    sent.add((src, f"{c.method} {c.path}"))
                    edges.append(CodeEdge(src=src, dst=f"unresolved:http:{c.method} {c.path}", kind="calls_http",
                                          origin="linker", resolved=False, label=f"{c.method} {c.path}"))
                continue
            best = max(sc for sc, _ in scored)
            for sc, i in scored:
                if sc == best and (src, ids[i]) not in sent:
                    sent.add((src, ids[i]))
                    edges.append(CodeEdge(src=src, dst=ids[i], kind="calls_http", origin="linker", label=routes[i].name))
        return edges


def _name(n) -> str:
    return ts.name_of(n) if n is not None and n.type in ("identifier", "member_expression") else ""
