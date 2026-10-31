"""Link React Router routes to the page components they render.

Handles `<Route path=... component={X}>` and `element={<X />}`; path identifiers are resolved
through string constants anywhere in the repo; X is resolved through the routing file's imports
(static or React.lazy). Anything unresolvable is recorded as unresolved.
"""

from __future__ import annotations

import re
from pathlib import Path

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, register_linker

_IMPORT = re.compile(r"import\s+(?:type\s+)?(?:(\w+)\s*,?\s*)?(?:\{([^}]*)\})?\s*from\s*['\"]([^'\"]+)['\"]")
_LAZY = re.compile(r"(\w+)\s*=\s*(?:React\.)?lazy\(\s*\(\)\s*=>\s*import\(\s*['\"]([^'\"]+)['\"]")
_ELEMENT = re.compile(r"^<\s*([A-Za-z_][\w.]*)")
EXT_TRY = ("", ".tsx", ".ts", ".jsx", ".js", "/index.tsx", "/index.ts", "/index.jsx", "/index.js")


def _imports(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for default, named, spec in _IMPORT.findall(text):
        if default:
            out[default] = spec
        for part in named.split(","):
            part = part.strip()
            if part:
                out[part.split(" as ")[-1].strip()] = spec
    for name, spec in _LAZY.findall(text):
        out[name] = spec
    return out


def _resolve(spec: str, from_file: Path, root: Path) -> Path | None:
    bases = [from_file.parent / spec] if spec.startswith(".") else [root / spec, root / "src" / spec]
    for b in bases:
        for ext in EXT_TRY:
            p = Path(str(b) + ext)
            if p.is_file():
                return p.resolve()
    return None


@register_linker
class ReactRouterLinker:
    type = "react-router"

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        return [e for repo in ctx.config.ins() for e in self._repo(ctx, repo)]

    def _repo(self, ctx: LinkContext, repo: str) -> list[CodeEdge]:
        root = ctx.checkouts[repo].root.resolve()
        g = ctx.graph
        files = list(ts.iter_source_files(root, list(ctx.ignore)))
        trees = {p: ts.parse(p) for p in files}
        consts: dict[str, str] = {}
        for t in trees.values():
            if t is not None:
                consts.update(ts.string_constants(t))

        edges: list[CodeEdge] = []
        for path, tree in trees.items():
            if tree is None:
                continue
            routes = ts.jsx_elements(tree, "Route")
            if not routes:
                continue
            rel = path.relative_to(root).as_posix()
            imports = _imports(path.read_text(errors="replace"))
            for r in routes:
                raw = r.attrs.get("path")
                if raw is None:
                    continue
                if raw[:1] in "'\"`":
                    route_path = raw.strip("'\"`")
                else:
                    route_path = consts.get(raw.split(".")[-1], raw)
                comp = r.attrs.get("component")
                if comp is None and "element" in r.attrs:
                    m = _ELEMENT.match(r.attrs["element"])
                    comp = m.group(1) if m else None
                if not comp:
                    continue
                rid = ensure_node(g, CodeNode(id=f"{repo}:route:{route_path}", repo=repo, kind="route", name=route_path,
                                              file=rel, line=r.line))
                spec = imports.get(comp.split(".")[0])
                target = _resolve(spec, path, root) if spec else None
                if target is not None and target.is_relative_to(root):
                    dst = ensure_file_node(g, repo, target.relative_to(root).as_posix())
                    edges.append(CodeEdge(src=rid, dst=dst, kind="routes_to", origin="linker", label=comp))
                else:
                    edges.append(CodeEdge(src=rid, dst=f"unresolved:route:{comp}", kind="routes_to", origin="linker",
                                          resolved=False, label=comp))
        return edges
