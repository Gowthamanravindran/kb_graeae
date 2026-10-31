"""tree-sitter helpers for Ruby sources (.rb .rake). Same contract as `ts.py`: arguments are tree-sitter nodes read
through `literal` / `const_path` / `env_name`, never stored as source text."""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import tree_sitter as tsl
import tree_sitter_ruby as trb

from kb.globs import matches_any

log = logging.getLogger(__name__)
EXTS = (".rb", ".rake")
SKIP_DIRS = {".git", "vendor", "node_modules", "tmp", "log", "coverage", "public"}


@lru_cache(maxsize=1)
def _parser() -> tsl.Parser:
    return tsl.Parser(tsl.Language(trb.language()))


def iter_source_files(root: Path, ignore: list[str]) -> Iterator[Path]:
    root = Path(root)
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS)
        for f in sorted(filenames):
            if f.endswith(EXTS):
                p = Path(dirpath) / f
                if not matches_any(p.relative_to(root).as_posix(), list(ignore)):
                    yield p


def parse(path: Path) -> tsl.Tree | None:
    try:
        return _parser().parse(Path(path).read_bytes())
    except OSError as e:
        log.warning("cannot read %s: %s", path, e)
        return None


def parse_text(source: str) -> tsl.Tree:
    return _parser().parse(source.encode("utf-8"))


def _text(n) -> str:
    return n.text.decode("utf-8", "replace")


def _walk(node) -> Iterator:
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(reversed(n.children))


# ---------------------------------------------------------------- values

def literal(n) -> str | None:
    """A string or symbol's text (`'a'`, `"a/#{x}"` -> `a/*`, `:a`, `a:` keys); None for anything else."""
    if n is None:
        return None
    if n.type in ("string", "bare_string", "delimited_symbol", "bare_symbol"):
        return "".join("*" if c.type == "interpolation" else _text(c) for c in n.children
                       if c.type in ("string_content", "interpolation", "escape_sequence"))
    if n.type == "simple_symbol":
        return _text(n)[1:]
    if n.type == "hash_key_symbol":
        return _text(n)
    return None


def const_path(n) -> str:
    """`Topics::ORDERS` / `SyncJob` -> its name; "" for anything that isn't a constant."""
    if n is None:
        return ""
    if n.type == "constant":
        return _text(n)
    if n.type == "scope_resolution":
        scope, name = n.child_by_field_name("scope"), n.child_by_field_name("name")
        left = const_path(scope) if scope is not None else ""
        return f"{left}::{_text(name)}" if left else (_text(name) if name is not None else "")
    return ""


def env_name(n) -> str:
    """`ENV['X']`, `ENV.fetch('X', …)`, `ENV.fetch('X') { … }` -> X."""
    if n is None:
        return ""
    if n.type == "element_reference" and n.named_children and _text(n.named_children[0]) == "ENV":
        return literal(n.named_children[1]) or "" if len(n.named_children) > 1 else ""
    if n.type == "call":
        r, m = n.child_by_field_name("receiver"), n.child_by_field_name("method")
        args = n.child_by_field_name("arguments")
        if r is not None and _text(r) == "ENV" and m is not None and _text(m) == "fetch" and args is not None \
                and args.named_children:
            return literal(args.named_children[0]) or ""
    return ""


# ---------------------------------------------------------------- calls

@dataclass
class Call:
    name: str
    receiver: str               # text of the receiver ("" for a bare call); never stored
    recv_node: object
    args: list                  # positional argument nodes
    kwargs: dict                # keyword (`to:`) and rocket (`'a' => …`) arguments: key text -> value node
    rockets: list               # [(key node, value node)] for `'ping' => 'health#ping'`
    block: object               # do/brace block body node, or None
    line: int
    node: object = None


def _call(n) -> Call | None:
    m = n.child_by_field_name("method")
    if m is None:
        return None
    r = n.child_by_field_name("receiver")
    args_node = n.child_by_field_name("arguments")
    args, kwargs, rockets = [], {}, []
    for a in args_node.named_children if args_node is not None else []:
        if a.type == "pair":
            k, v = a.child_by_field_name("key"), a.child_by_field_name("value")
            if k is None or v is None:
                continue
            if k.type == "hash_key_symbol":
                kwargs[_text(k)] = v
            elif k.type == "simple_symbol":
                kwargs[_text(k)[1:]] = v
            else:
                rockets.append((k, v))
        elif a.type == "hash":
            for p in a.named_children:
                if p.type == "pair" and (k := p.child_by_field_name("key")) is not None \
                        and (v := p.child_by_field_name("value")) is not None:
                    key = literal(k) if k.type != "hash_key_symbol" else _text(k)
                    if key is not None:
                        kwargs[key] = v
        elif a.type not in ("comment", "block_argument"):
            args.append(a)
    block = n.child_by_field_name("block")
    body = block.child_by_field_name("body") if block is not None else None
    if body is None and block is not None:
        body = next((c for c in block.named_children if c.type in ("body_statement", "block_body")), None)
    return Call(_text(m), _text(r) if r is not None else "", r, args, kwargs, rockets, body, n.start_point[0] + 1, n)


def calls(tree_or_node) -> list[Call]:
    root = tree_or_node.root_node if hasattr(tree_or_node, "root_node") else tree_or_node
    out = []
    for n in _walk(root):
        if n.type == "call":
            c = _call(n)
            if c is not None:
                out.append(c)
    return out


def statements(body) -> list[Call]:
    """The calls directly in a block body (`resources :x do … end` children), in order; bare words like `member`
    with no arguments come back as calls with no arguments."""
    out = []
    for n in body.named_children if body is not None else []:
        if n.type == "call":
            c = _call(n)
            if c is not None:
                out.append(c)
        elif n.type == "identifier":
            out.append(Call(_text(n), "", None, [], {}, [], None, n.start_point[0] + 1, n))
    return out


# ---------------------------------------------------------------- classes, methods, constants

@dataclass
class RbClass:
    name: str                   # full name with its modules: Api::V1::UsersController
    line: int
    superclass: str
    methods: dict[str, int] = field(default_factory=dict)   # instance method -> line
    calls: list = field(default_factory=list)               # calls made in the class body itself (`include X`)
    node: object = None

    @property
    def short(self) -> str:
        return self.name.rsplit("::", 1)[-1]


def classes(tree) -> list[RbClass]:
    out: list[RbClass] = []

    def visit(node, prefix: str):
        for c in node.named_children:
            if c.type in ("class", "module"):
                nm = const_path(c.child_by_field_name("name"))
                full = f"{prefix}::{nm}" if prefix and nm else nm or prefix
                body = c.child_by_field_name("body") or next(
                    (x for x in c.named_children if x.type == "body_statement"), None)
                if c.type == "class":
                    sup = c.child_by_field_name("superclass")
                    k = RbClass(full, c.start_point[0] + 1,
                                const_path(sup.named_children[0]) if sup is not None and sup.named_children else "",
                                node=c)
                    for s in body.named_children if body is not None else []:
                        if s.type == "method" and (mn := s.child_by_field_name("name")) is not None:
                            k.methods[_text(mn)] = s.start_point[0] + 1
                        elif s.type in ("call", "identifier"):
                            k.calls += statements_of(s)
                    out.append(k)
                if body is not None:
                    visit(body, full)
            elif c.type in ("body_statement", "begin", "if", "unless"):
                visit(c, prefix)

    visit(tree.root_node, "")
    return out


def statements_of(n) -> list[Call]:
    if n.type == "call":
        c = _call(n)
        return [c] if c is not None else []
    return [Call(_text(n), "", None, [], {}, [], None, n.start_point[0] + 1, n)]


def constants(tree) -> dict[str, str]:
    """`Topics::ORDERS` -> 'orders' for string/symbol constants (with their modules); ENV-backed constants map
    to the setting's name, so `ORDERS = ENV['ORDERS_TOPIC']` keys as ORDERS_TOPIC."""
    out: dict[str, str] = {}

    def visit(node, prefix: str):
        for c in node.named_children:
            if c.type in ("class", "module"):
                nm = const_path(c.child_by_field_name("name"))
                full = f"{prefix}::{nm}" if prefix and nm else nm or prefix
                body = c.child_by_field_name("body") or next(
                    (x for x in c.named_children if x.type == "body_statement"), None)
                if body is not None:
                    visit(body, full)
            elif c.type == "assignment":
                left, right = c.child_by_field_name("left"), c.child_by_field_name("right")
                if left is not None and left.type == "constant" and right is not None:
                    v = literal(right)
                    if v is None and right.type == "call" and _text(right).endswith(".freeze"):
                        v = literal(right.child_by_field_name("receiver"))
                    v = v if v is not None and "*" not in v else (env_name(right) or None)
                    if v:
                        name = _text(left)
                        out[f"{prefix}::{name}" if prefix else name] = v
            elif c.type in ("body_statement",):
                visit(c, prefix)

    visit(tree.root_node, "")
    return out


def method_spans(tree) -> list[tuple[str, str, int, int]]:
    """(class name, method name, start line, end line) for every method, `''` class for top-level ones."""
    out = []

    def visit(node, cls: str):
        for c in node.named_children:
            if c.type in ("class", "module"):
                nm = const_path(c.child_by_field_name("name"))
                visit(c, f"{cls}::{nm}" if cls and nm else nm or cls)
            elif c.type in ("method", "singleton_method"):
                mn = c.child_by_field_name("name")
                if mn is not None:
                    out.append((cls, _text(mn), c.start_point[0] + 1, c.end_point[0] + 1))
            elif c.named_children:
                visit(c, cls)

    visit(tree.root_node, "")
    return out
