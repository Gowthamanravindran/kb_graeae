"""tree-sitter helpers for JavaScript/TypeScript sources (.ts .tsx .js .jsx)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Iterator, Literal

import tree_sitter as tsl
import tree_sitter_typescript as tst

from kb.globs import matches_any

log = logging.getLogger(__name__)
EXTS = (".ts", ".tsx", ".js", ".jsx")
SKIP_DIRS = {"node_modules", ".git", "dist", "build"}


@dataclass
class TemplateHit:
    text: str
    line: int
    var_name: str | None


@dataclass
class CallHit:
    receiver: str
    first_arg: str
    first_arg_kind: Literal["string", "member", "identifier", "other"]
    line: int
    last_arg: str = ""  # the last argument's name when it is an identifier or `a.b` (never other source text)
    n_args: int = 0


@dataclass
class ObjHit:
    props: dict[str, str]
    line: int
    var_name: str | None


@dataclass
class JsxHit:
    attrs: dict[str, str]
    line: int


@lru_cache(maxsize=2)
def _parser(tsx: bool) -> tsl.Parser:
    lang = tst.language_tsx() if tsx else tst.language_typescript()
    return tsl.Parser(tsl.Language(lang))


def iter_source_files(root: Path, ignore: list[str]) -> Iterator[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(EXTS) and not f.endswith(".d.ts"):
                p = Path(dirpath) / f
                if not matches_any(p.relative_to(root).as_posix(), list(ignore)):
                    yield p


def parse(path: Path) -> tsl.Tree | None:
    try:
        data = Path(path).read_bytes()
    except OSError as e:
        log.warning("cannot read %s: %s", path, e)
        return None
    # JSX can appear in .js files; .ts uses the plain grammar so `<T>x` casts parse.
    return _parser(not str(path).endswith(".ts")).parse(data)


def _text(n) -> str:
    return n.text.decode("utf-8", "replace")


def _walk(node) -> Iterator:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


def _var_name(node) -> str | None:
    p = node.parent
    while p is not None and p.type in ("parenthesized_expression", "as_expression", "await_expression"):
        p = p.parent
    if p is not None and p.type == "variable_declarator":
        name = p.child_by_field_name("name")
        return _text(name) if name is not None else None
    return None


def _string_value(n) -> str:
    return "".join(_text(c) for c in n.children if c.type == "string_fragment")


def tagged_templates(tree: tsl.Tree, tag: str) -> list[TemplateHit]:
    out = []
    for n in _walk(tree.root_node):
        if n.type == "call_expression":
            fn, arg = n.child_by_field_name("function"), n.child_by_field_name("arguments")
            if fn is not None and arg is not None and arg.type == "template_string" and _text(fn) == tag:
                body = _text(arg)[1:-1]
                out.append(TemplateHit(body, n.start_point[0] + 1, _var_name(n)))
    return out


def calls(tree: tsl.Tree, method: str) -> list[CallHit]:
    out = []
    for n in _walk(tree.root_node):
        if n.type != "call_expression":
            continue
        fn = n.child_by_field_name("function")
        if fn is None or fn.type != "member_expression":
            continue
        prop = fn.child_by_field_name("property")
        if prop is None or _text(prop) != method:
            continue
        args = n.child_by_field_name("arguments")
        named = [c for c in args.named_children] if args is not None else []
        if not named:
            continue
        a = named[0]
        if a.type == "string":
            first, kind = _string_value(a), "string"
        elif a.type == "member_expression":
            first, kind = _text(a), "member"
        elif a.type == "identifier":
            first, kind = _text(a), "identifier"
        else:
            first, kind = _text(a), "other"
        obj = fn.child_by_field_name("object")
        last = named[-1]
        last_name = _text(last) if last.type in ("identifier", "member_expression") else ""
        out.append(CallHit(_text(obj) if obj is not None else "", first, kind, n.start_point[0] + 1, last_name, len(named)))
    return out


_DECLS = {"function_declaration", "generator_function_declaration", "lexical_declaration", "variable_declaration",
          "class_declaration"}


def top_level_names(tree: tsl.Tree) -> list[tuple[str, int]]:
    """Names declared at the top of a file (`function f`, `const f = …`, `export …`), with their line.
    Destructured names (`const { f } = mod`) are only re-exports and are skipped."""
    out = []
    for node in tree.root_node.children:
        decl = node
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration") or next((c for c in node.children if c.type in _DECLS), None)
            if decl is None:
                continue
        if decl.type not in _DECLS:
            continue
        if decl.type in ("lexical_declaration", "variable_declaration"):
            for c in decl.named_children:
                n = c.child_by_field_name("name") if c.type == "variable_declarator" else None
                if n is not None and n.type == "identifier":
                    out.append((_text(n), node.start_point[0] + 1))
        elif (n := decl.child_by_field_name("name")) is not None:
            out.append((_text(n), node.start_point[0] + 1))
    return out


def object_literals_with(tree: tsl.Tree, key: str) -> list[ObjHit]:
    out = []
    for n in _walk(tree.root_node):
        if n.type != "object":
            continue
        props: dict[str, str] = {}
        for c in n.named_children:
            if c.type == "pair":
                k, v = c.child_by_field_name("key"), c.child_by_field_name("value")
                if k is not None and v is not None:
                    props[_text(k).strip("'\"")] = _text(v)
            elif c.type == "method_definition":
                name = c.child_by_field_name("name")
                if name is not None:
                    props[_text(name)] = _text(c)
            elif c.type == "shorthand_property_identifier":
                props[_text(c)] = _text(c)
        if key in props:
            out.append(ObjHit(props, n.start_point[0] + 1, _var_name(n)))
    return out


def jsx_elements(tree: tsl.Tree, name: str) -> list[JsxHit]:
    out = []
    for n in _walk(tree.root_node):
        if n.type == "jsx_self_closing_element":
            opening = n
        elif n.type == "jsx_opening_element":
            opening = n
        else:
            continue
        nm = opening.child_by_field_name("name")
        if nm is None or _text(nm) != name:
            continue
        attrs: dict[str, str] = {}
        for a in opening.named_children:
            if a.type != "jsx_attribute" or not a.named_children:
                continue
            key = _text(a.named_children[0])
            val = a.named_children[1] if len(a.named_children) > 1 else None
            if val is None:
                attrs[key] = "true"
            elif val.type == "jsx_expression":
                attrs[key] = _text(val)[1:-1].strip()
            else:
                attrs[key] = _text(val)
        out.append(JsxHit(attrs, n.start_point[0] + 1))
    return out


def string_constants(tree: tsl.Tree) -> dict[str, str]:
    out = {}
    for n in _walk(tree.root_node):
        if n.type == "variable_declarator":
            name, val = n.child_by_field_name("name"), n.child_by_field_name("value")
            if name is not None and val is not None and val.type == "string" and name.type == "identifier":
                out[_text(name)] = _string_value(val)
    return out


# ---------------------------------------------------------------- generic calls (REST and message brokers)

@dataclass
class Call:
    """Any call or `new X(...)`. `args` are tree-sitter nodes: read them through `literal`/`props`, never store
    their source text."""
    name: str       # `fetch`, `get`, `SendMessageCommand`
    receiver: str   # `axios`, `this.http`; "" for a plain call
    args: list
    line: int
    is_new: bool = False
    receiver_node: object = None


def all_calls(tree: tsl.Tree) -> list[Call]:
    out = []
    for n in _walk(tree.root_node):
        if n.type not in ("call_expression", "new_expression"):
            continue
        fn = n.child_by_field_name("function") if n.type == "call_expression" else n.child_by_field_name("constructor")
        args = n.child_by_field_name("arguments")
        if fn is None:
            continue
        named = [c for c in args.named_children if c.type != "comment"] if args is not None else []
        if fn.type == "member_expression":
            prop, obj = fn.child_by_field_name("property"), fn.child_by_field_name("object")
            if prop is None:
                continue
            while obj is not None and obj.type == "parenthesized_expression" and obj.named_children:
                obj = obj.named_children[0]   # `(api).get(…)`
            out.append(Call(_text(prop), _text(obj) if obj is not None else "", named, n.start_point[0] + 1,
                            n.type == "new_expression", obj))
        elif fn.type == "identifier":
            out.append(Call(_text(fn), "", named, n.start_point[0] + 1, n.type == "new_expression"))
    return out


def literal(n) -> str | None:
    """A string or template literal's text, `${…}` written as `*`. None for anything else."""
    if n is None:
        return None
    if n.type == "string":
        return _string_value(n)
    if n.type == "template_string":
        return "".join("*" if c.type == "template_substitution" else _text(c)
                       for c in n.children if c.type in ("string_fragment", "template_substitution", "escape_sequence"))
    return None


def props(n) -> dict:
    """An object literal's properties: name -> value node (shorthand `{ topic }` maps to the identifier)."""
    out = {}
    if n is None or n.type != "object":
        return out
    for c in n.named_children:
        if c.type == "pair":
            k, v = c.child_by_field_name("key"), c.child_by_field_name("value")
            if k is not None and v is not None:
                out[_text(k).strip("'\"")] = v
        elif c.type == "shorthand_property_identifier":
            out[_text(c)] = c
    return out


def name_of(n) -> str:
    """The name an identifier or `a.b.c` refers to (its last part); "" for anything else."""
    if n is None:
        return ""
    if n.type in ("identifier", "shorthand_property_identifier"):
        return _text(n)
    if n.type == "member_expression":
        p = n.child_by_field_name("property")
        return _text(p) if p is not None else ""
    return ""


def is_env(n) -> bool:
    """`process.env.X`"""
    if n is None or n.type != "member_expression":
        return False
    obj = n.child_by_field_name("object")
    return obj is not None and _text(obj) == "process.env"


def qualified_strings(tree: tsl.Tree) -> dict[str, str]:
    """`Obj.KEY` -> value for string members of declared objects and enums: `const Topics = { A: 'a' }`,
    `enum Topics { A = 'a' }` (topic and queue constant maps)."""
    out: dict[str, str] = {}
    for n in _walk(tree.root_node):
        if n.type == "variable_declarator":
            name, val = n.child_by_field_name("name"), n.child_by_field_name("value")
            while val is not None and val.type in ("as_expression", "satisfies_expression", "parenthesized_expression"):
                val = val.named_children[0] if val.named_children else None
            if name is None or val is None or name.type != "identifier" or val.type != "object":
                continue
            for c in val.named_children:
                k, v = c.child_by_field_name("key"), c.child_by_field_name("value")
                if c.type == "pair" and k is not None and v is not None and v.type == "string":
                    out[f"{_text(name)}.{_text(k).strip(chr(39) + chr(34))}"] = _string_value(v)
        elif n.type == "enum_declaration":
            name, body = n.child_by_field_name("name"), n.child_by_field_name("body")
            for c in body.named_children if body is not None else []:
                k, v = c.child_by_field_name("name"), c.child_by_field_name("value")
                if c.type == "enum_assignment" and k is not None and v is not None and v.type == "string":
                    out[f"{_text(name)}.{_text(k).strip(chr(39) + chr(34))}"] = _string_value(v)
    return out


@dataclass
class DecoratedMethod:
    cls: str
    cls_decorators: dict[str, object]   # decorator name -> first argument node (or None)
    method: str
    decorators: dict[str, object]
    line: int


def _decorator(d) -> tuple[str, object] | None:
    expr = next((c for c in d.named_children), None)
    if expr is None:
        return None
    if expr.type == "identifier":
        return _text(expr), None
    if expr.type == "call_expression":
        fn, args = expr.child_by_field_name("function"), expr.child_by_field_name("arguments")
        named = [c for c in args.named_children] if args is not None else []
        return (_text(fn).split(".")[-1] if fn is not None else ""), (named[0] if named else None)
    return None


def decorated_methods(tree: tsl.Tree) -> list[DecoratedMethod]:
    """Class methods with decorators (NestJS `@Get(':id')`, `@EventPattern('x')`), with their class's decorators."""
    out = []
    for n in _walk(tree.root_node):
        if n.type != "class_declaration":
            continue
        name = n.child_by_field_name("name")
        holder = n.parent if n.parent is not None and n.parent.type == "export_statement" else n
        cls_decs = dict(filter(None, (_decorator(d) for d in list(holder.children) + list(n.children) if d.type == "decorator")))
        body = n.child_by_field_name("body")
        pending: dict[str, object] = {}
        for c in body.named_children if body is not None else []:
            if c.type == "decorator":
                if (d := _decorator(c)) is not None:
                    pending[d[0]] = d[1]
            elif c.type == "method_definition":
                mname = c.child_by_field_name("name")
                if pending and mname is not None:
                    out.append(DecoratedMethod(_text(name) if name is not None else "", cls_decs, _text(mname), pending,
                                               c.start_point[0] + 1))
                pending = {}
            else:
                pending = {}
    return out
