"""Link frontend GraphQL operations to the backend resolvers that handle them.

Frontend: operations in gql`...` templates (Apollo style). Each root field of an operation
is matched by name to a backend resolver. Backend styles:
  - graphql-compose: object literals with a string `name` and a `resolve` key
    (e.g. `const AddTask = { name: 'AddTask', resolve: ... }`)
  - sdl: fields of `type Query/Mutation/Subscription` in .graphql files or gql templates.
"""

from __future__ import annotations

import re
from pathlib import Path

from graphql import GraphQLSyntaxError, parse
from graphql.language import ObjectTypeDefinitionNode, ObjectTypeExtensionNode, OperationDefinitionNode

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, register_linker

_INTERP = re.compile(r"\$\{[^}]*\}")
ROOT_TYPES = {"Query", "Mutation", "Subscription"}


def _parse_doc(text: str):
    try:
        return parse(_INTERP.sub("", text))
    except GraphQLSyntaxError:
        return None


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset)


@register_linker
class GraphqlLinker:
    type = "graphql"

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        # every client app against every service together: an operation is unmatched only when no service has it
        styles = ctx.config.options.get("backend_style", "sdl")
        resolvers: dict[str, list[str]] = {}
        for be_repo in ctx.config.tos():
            style = styles.get(be_repo, "sdl") if isinstance(styles, dict) else styles
            for field, ids in self._backend(ctx, be_repo, style).items():
                resolvers.setdefault(field, []).extend(ids)
        exported: dict[str, list[tuple[str, str]]] = {}  # query variable -> [(repo, op id)], across all client apps
        edges = [e for fe_repo in ctx.config.froms() for e in self._client(ctx, fe_repo, resolvers, exported)]
        return edges + self._shared_use(ctx, exported)

    def _shared_use(self, ctx: LinkContext, exported: dict[str, list[tuple[str, str]]]) -> list[CodeEdge]:
        """A screen in one app using a query written in another package (a shared `commons`) sends that query.
        Only an explicit import of the name counts, so the same variable name defined locally doesn't."""
        if not exported or len(ctx.config.froms()) < 2:
            return []
        g = ctx.graph
        names = re.compile(r"\bimport\s*(?:type\s+)?\{([^}]*)\}\s*from\s*['\"][^'\"]+['\"]", re.S)
        out = []
        for repo in ctx.config.froms():
            root = ctx.checkouts[repo].root
            for path in ts.iter_source_files(root, list(ctx.ignore)):
                text = path.read_text(errors="replace")
                imported = {n.strip().split(" as ")[0].strip() for m in names.finditer(text) for n in m.group(1).split(",")}
                for var in imported & set(exported):
                    for op_repo, op_id in exported[var]:
                        if op_repo == repo:
                            continue  # same app: linked by the usage scan already
                        rel = path.relative_to(root).as_posix()
                        out.append(CodeEdge(src=ensure_file_node(g, repo, rel), dst=op_id, kind="sends_graphql",
                                            origin="linker", label=var))
        return out

    def _client(self, ctx: LinkContext, fe_repo: str, resolvers: dict[str, list[str]],
                exported: dict[str, list[tuple[str, str]]] | None = None) -> list[CodeEdge]:
        edges: list[CodeEdge] = []
        g = ctx.graph
        root = ctx.checkouts[fe_repo].root
        var_to_ops: dict[str, list[str]] = {}
        files = list(ts.iter_source_files(root, list(ctx.ignore)))

        for path in files:
            tree = ts.parse(path)
            if tree is None:
                continue
            rel = path.relative_to(root).as_posix()
            for hit in ts.tagged_templates(tree, "gql"):
                doc = _parse_doc(hit.text)
                if doc is None:
                    continue
                for d in doc.definitions:
                    if not isinstance(d, OperationDefinitionNode):
                        continue
                    line = hit.line + _line_of(hit.text, d.loc.start) if d.loc else hit.line
                    name = d.name.value if d.name else f"anonymous@{line}"
                    op_id = ensure_node(
                        g, CodeNode(id=f"{fe_repo}:{rel}#op:{name}", repo=fe_repo, kind="graphql_op", name=name, file=rel, line=line)
                    )
                    ensure_file_node(g, fe_repo, rel)
                    if hit.var_name:
                        var_to_ops.setdefault(hit.var_name, []).append(op_id)
                        if exported is not None:
                            exported.setdefault(hit.var_name, []).append((fe_repo, op_id))
                    for sel in d.selection_set.selections:
                        field = getattr(getattr(sel, "name", None), "value", None)
                        if not field:
                            continue
                        targets = resolvers.get(field, [])
                        if targets:
                            edges += [CodeEdge(src=op_id, dst=t, kind="handled_by", origin="linker", label=field) for t in targets]
                        else:
                            edges.append(CodeEdge(src=op_id, dst=f"unresolved:graphql:{field}", kind="handled_by",
                                                  origin="linker", resolved=False, label=field))

        if var_to_ops:
            pattern = re.compile(r"\b(" + "|".join(map(re.escape, var_to_ops)) + r")\b")
            for path in files:
                text = path.read_text(errors="replace")
                rel = path.relative_to(root).as_posix()
                used = set(pattern.findall(text))
                for var in used:
                    for op_id in var_to_ops[var]:
                        if g.node(op_id).file == rel:
                            continue  # the defining file
                        src = ensure_file_node(g, fe_repo, rel)
                        edges.append(CodeEdge(src=src, dst=op_id, kind="sends_graphql", origin="linker", label=var))
        return edges

    def _backend(self, ctx: LinkContext, repo: str, style: str) -> dict[str, list[str]]:
        root = ctx.checkouts[repo].root
        g = ctx.graph
        out: dict[str, list[str]] = {}

        def add(field: str, rel: str, line: int) -> None:
            nid = ensure_node(g, CodeNode(id=f"{repo}:{rel}#resolver:{field}@{line}", repo=repo, kind="resolver",
                                          name=field, file=rel, line=line))
            ensure_file_node(g, repo, rel)
            out.setdefault(field, []).append(nid)

        def sdl(text: str, rel: str, base_line: int) -> None:
            doc = _parse_doc(text)
            if doc is None:
                return
            for d in doc.definitions:
                if isinstance(d, (ObjectTypeDefinitionNode, ObjectTypeExtensionNode)) and d.name.value in ROOT_TYPES:
                    for f in d.fields or []:
                        add(f.name.value, rel, base_line + _line_of(text, f.loc.start))

        if style == "sdl":
            for p in sorted(Path(root).rglob("*.graphql")) + sorted(Path(root).rglob("*.gql")):
                if "node_modules" not in p.parts:
                    sdl(p.read_text(errors="replace"), p.relative_to(root).as_posix(), 1)

        for path in ts.iter_source_files(root, list(ctx.ignore)):
            tree = ts.parse(path)
            if tree is None:
                continue
            rel = path.relative_to(root).as_posix()
            if style == "graphql-compose":
                for o in ts.object_literals_with(tree, "resolve"):
                    name = o.props.get("name", "")
                    if len(name) >= 2 and name[0] in "'\"`" and name[-1] == name[0]:
                        add(name[1:-1], rel, o.line)
            else:
                for hit in ts.tagged_templates(tree, "gql"):
                    sdl(hit.text, rel, hit.line)
        return out
