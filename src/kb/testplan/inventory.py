"""The change inventory: every changed decision point (branch, validation, permission check, error, field…)
in the functions a PR touches. Each item becomes one or more case slots, so this is what makes
"every possibility of the change" checkable."""

from __future__ import annotations

import difflib
import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path

from kb.ingest.checkout import Checkout
from kb.testplan.delta import TS_EXTS, GraphDelta, find_span, tree_for
from kb.testplan.diff import Hunk
from kb.testplan.models import InventoryItem

PERMISSION = re.compile(r"\b(role|roles|permission|permissions|isAdmin|can[A-Z]\w*|authori[sz]e\w*|guard\w*)\b")
VALIDATES = re.compile(r"\b(args|input|req\.body|values|body)\.|\b(validate\w*|isValid\w*)\s*\(")
_CRUD = r"(find\w*|create\w*|update\w*|upsert\w*|delete\w*|remove|save|insert\w*|count\w*|aggregate|groupBy)"
QUERY_CALLS = (
    re.compile(r"^(this\.)?\w*Model\." + _CRUD + "$"),                         # Mongoose models
    re.compile(r"^(this\.)?\w*prisma\.\w+\." + _CRUD + "$", re.I),               # Prisma: prisma.task.findMany
    re.compile(r"^(this\.)?\w*(Repository|Repo|repository|repo)\." + _CRUD + "$"),  # TypeORM / repository classes
    re.compile(r"^getRepository\(.*\)\." + _CRUD + "$"),
    re.compile(r"^[A-Z]\w*\.(find\w*|updateOne|updateMany|deleteOne|deleteMany|insertMany|countDocuments|aggregate)$"),
)
_BUILTINS = {"Object", "Array", "Promise", "Math", "JSON", "Reflect", "Number", "String", "Date", "Symbol", "Map", "Set"}
JSX = {"jsx_element", "jsx_self_closing_element", "jsx_fragment"}
ALIGN = 0.6


@dataclass
class Decision:
    kind: str
    text: str
    outcomes: list[str]
    line: int
    norm: str = field(default="")

    def __post_init__(self):
        self.norm = self.norm or " ".join(self.text.split())


def _t(n) -> str:
    return n.text.decode("utf-8", "replace")


def _throws(stmt) -> bool:
    """The block runs straight into a throw or an error return (one level deep)."""
    if stmt is None:
        return False
    body = [stmt] if stmt.type != "statement_block" else list(stmt.named_children)
    for s in body:
        if s.type == "throw_statement":
            return True
        if s.type == "return_statement" and re.search(r"new \w*Error\(|\.status\(\s*[45]\d\d", _t(s)):
            return True
    return False


def _is_jsx(n) -> bool:
    while n is not None and n.type == "parenthesized_expression":
        n = n.named_children[0] if n.named_children else None
    return n is not None and n.type in JSX


def _is_query(ftext: str) -> bool:
    if ftext.split(".", 1)[0] in _BUILTINS:
        return False
    return any(p.match(ftext) for p in QUERY_CALLS)


def _walk(node):
    yield node
    for c in node.children:
        yield from _walk(c)


def extract_decisions(source: str, path: str, start: int, end: int) -> list[Decision]:
    """Decision points whose first line is within [start, end] (1-based) of a JS/TS file."""
    if not path.endswith(TS_EXTS):
        return []
    root = tree_for(source, path).root_node
    out: list[Decision] = []
    skip_throws: set[int] = set()  # throws that belong to a permission/validation check
    for n in _walk(root):
        line = n.start_point[0] + 1
        if not (start <= line <= end):
            continue
        if n.type == "if_statement":
            cond_node = n.child_by_field_name("condition")
            cond = _t(cond_node).strip()[1:-1].strip() if cond_node is not None else ""
            cons = n.child_by_field_name("consequence")
            if _throws(cons) and PERMISSION.search(cond):
                out.append(Decision("permission", cond, ["allowed", "denied"], line))
            elif _throws(cons) and VALIDATES.search(cond):
                out.append(Decision("validation", cond, ["valid", "invalid"], line))
            else:
                out.append(Decision("branch", cond, ["true", "false"], line))
                continue
            for c in _walk(cons):
                if c.type == "throw_statement":
                    skip_throws.add(c.start_byte)
        elif n.type == "switch_statement":
            val = n.child_by_field_name("value")
            body = n.child_by_field_name("body")
            cases = [_t(c.child_by_field_name("value")) for c in (body.named_children if body else [])
                     if c.type == "switch_case" and c.child_by_field_name("value") is not None]
            out.append(Decision("branch", f"switch {_t(val) if val else ''}".strip(), cases + ["default"], line))
        elif n.type == "ternary_expression":
            cond = n.child_by_field_name("condition")
            text = _t(cond) if cond else _t(n)
            jsx = _is_jsx(n.child_by_field_name("consequence")) or _is_jsx(n.child_by_field_name("alternative"))
            if jsx and PERMISSION.search(text):  # {canEdit ? <Edit/> : <View/>}: a UI permission gate
                out.append(Decision("permission", text, ["allowed", "denied"], line))
            else:
                out.append(Decision("branch", text, ["true", "false"], line))
        elif n.type == "binary_expression" and _t(n.child_by_field_name("operator") or n) == "&&" \
                and _is_jsx(n.child_by_field_name("right")):  # {isAdmin && <Delete/>}
            left = _t(n.child_by_field_name("left"))
            if PERMISSION.search(left):
                out.append(Decision("permission", left, ["allowed", "denied"], line))
            else:
                out.append(Decision("branch", left, ["shown", "hidden"], line))
        elif n.type == "catch_clause":
            body = n.child_by_field_name("body")
            first = _t(body.named_children[0]) if body is not None and body.named_children else ""
            param = n.child_by_field_name("parameter")
            text = f"catch ({_t(param) if param is not None else ''}) {first}".strip().rstrip(";")
            out.append(Decision("error", text[:160], ["error"], line))
        elif n.type == "throw_statement" and n.start_byte not in skip_throws:
            out.append(Decision("error", _t(n).rstrip(";"), ["error"], line))
        elif n.type == "call_expression":
            fn = n.child_by_field_name("function")
            ftext = _t(fn) if fn is not None else ""
            if re.search(r"\.status$", ftext) and re.match(r"\(\s*[45]\d\d", _t(n.child_by_field_name("arguments") or n)):
                out.append(Decision("error", _t(n), ["error"], line))
            elif _is_query(ftext):
                out.append(Decision("query", _t(n), ["runs"], line))
        elif n.type == "pair":
            key = n.child_by_field_name("key")
            if key is not None and _t(key).strip("'\"") == "variables":
                for k in _walk(n.child_by_field_name("value")):
                    if k.type in ("pair", "shorthand_property_identifier"):
                        kk = k.child_by_field_name("key") if k.type == "pair" else k
                        out.append(Decision("field", _t(kk).strip("'\""), ["sent"], k.start_point[0] + 1))
    return out  # pre-order walk: an `if` is seen before the throw inside it, so skip_throws is always ready


def _similar(a: str, b: str) -> float:
    """Text similarity; a condition that was extended (`isAdmin` → `isAdmin || isOwner`) still contains the
    old one, so containment counts as a match even when the ratio is low."""
    if a == b:
        return 1.0
    r = difflib.SequenceMatcher(None, a, b).ratio()
    short, long_ = sorted((a, b), key=len)
    if len(short) >= 3 and re.search(r"(?<![\w.])" + re.escape(short) + r"(?![\w])", long_):
        r = max(r, ALIGN)
    return r


def _align(base: list[Decision], head: list[Decision]):
    """Pairs (b, h) within the same kind by best text similarity; returns (pairs, base_only, head_only)."""
    pairs, used_b = [], set()
    head_only = []
    for h in head:
        best, score = None, 0.0
        for i, b in enumerate(base):
            if i in used_b or b.kind != h.kind:
                continue
            r = _similar(b.norm, h.norm)
            if r > score:
                best, score = i, r
        if best is not None and score >= ALIGN:
            used_b.add(best)
            pairs.append((base[best], h))
        else:
            head_only.append(h)
    return pairs, [b for i, b in enumerate(base) if i not in used_b], head_only


def _id(repo: str, file: str, function: str, kind: str, norm: str) -> str:
    return hashlib.sha1(f"{repo}|{file}|{function}|{kind}|{norm}".encode()).hexdigest()[:12]


def _summary(kind: str, d: Decision, was: Decision | None = None, removed_kind: str | None = None) -> str:
    text = d.norm if len(d.norm) <= 120 else d.norm[:117] + "…"
    if kind == "removed":
        what = {"branch": "branch", "permission": "permission check", "validation": "validation",
                "error": "error", "query": "data query"}.get(removed_kind or "", "behaviour")
        return f"Removed {what}: `{text}`"
    label = {"permission": "Permission check", "validation": "Validation", "branch": "Branch", "error": "Error",
             "query": "Data query"}.get(kind, kind.capitalize())
    if kind == "field":
        return f"Field `{text}` {'removed from' if removed_kind else 'sent in'} the request"
    return f"{label} `{text}`" + (f" (was `{was.norm}`)" if was is not None and was.norm != d.norm else "")


def _read(cos: dict[str, Checkout], repo: str, file: str) -> str | None:
    co = cos.get(repo)
    try:
        return (Path(co.root) / file).read_text(encoding="utf-8", errors="replace") if co else None
    except OSError:
        return None




def inventory(base_cos: dict[str, Checkout], head_cos: dict[str, Checkout], delta: GraphDelta,
              hunks: list[Hunk], renames: dict[tuple[str, str], str] | None = None) -> list[InventoryItem]:
    # (repo, base_file, head_file, function name or None for file-level, [hunk indices])
    targets: dict[tuple, list[int]] = {}
    for i, h in enumerate(hunks):
        owner = delta.hunk_owner.get(i, f"{h.repo}:{h.file}")
        name = owner.split("#", 1)[1] if "#" in owner else None  # keeps "@line" to tell same-named methods apart
        targets.setdefault((h.repo, h.old_file, h.file, name), []).append(i)
    for nid in delta.nodes_removed:
        repo, rest = nid.split(":", 1)
        file, name = rest.split("#", 1)
        targets.setdefault((repo, file, (renames or {}).get((repo, file), file), name), [])

    items: dict[str, InventoryItem] = {}

    def add(kind, change, repo, file, fn, d: Decision, was=None, removed_kind=None):
        iid = _id(repo, file, fn or "", kind if kind != "removed" else f"removed:{removed_kind}", d.norm)
        items.setdefault(iid, InventoryItem(id=iid, kind=kind, change=change, repo=repo, file=file,
                                            lines=(d.line, d.line), function=fn or "", outcomes=d.outcomes,
                                            summary=_summary(kind, d, was, removed_kind), text=d.norm))

    for (repo, bfile, hfile, full), idx in targets.items():
        name = full.split("@")[0] if full else None
        bsrc, hsrc = _read(base_cos, repo, bfile), _read(head_cos, repo, hfile)
        ts_file = hfile.endswith(TS_EXTS) or bfile.endswith(TS_EXTS)
        if not ts_file:
            if name:
                d = Decision("code", f"{name} changed", ["changed"], hunks[idx[0]].new_start if idx else 1)
                iid = _id(repo, hfile, name, "code", name)
                items.setdefault(iid, InventoryItem(id=iid, kind="code", change="changed", repo=repo, file=hfile,
                                                    lines=(d.line, d.line), function=name, outcomes=["changed"],
                                                    summary=f"Code changed in {name}", text=name))
            continue
        if name:
            hs, bs = find_span(hsrc, hfile, full), find_span(bsrc, bfile, full)
            head_d = extract_decisions(hsrc, hfile, *hs) if hs and hsrc else []
            base_d = extract_decisions(bsrc, bfile, *bs) if bs and bsrc else []
        else:
            head_d, base_d = [], []
            for i in idx:
                h = hunks[i]
                if hsrc and h.new_len:
                    head_d += extract_decisions(hsrc, hfile, h.new_start, h.new_start + h.new_len - 1)
                if bsrc and h.old_len:
                    base_d += extract_decisions(bsrc, bfile, h.old_start, h.old_start + h.old_len - 1)
        pairs, base_only, head_only = _align(base_d, head_d)
        for b, h in pairs:
            if b.norm != h.norm:
                add(h.kind, "changed", repo, hfile, name, h, was=b)
        for h in head_only:
            add(h.kind, "added", repo, hfile, name, h)
        for b in base_only:
            if b.kind == "field":
                add("field", "removed", repo, bfile, name, b, removed_kind="field")
            else:
                add("removed", "removed", repo, bfile, name, b, removed_kind=b.kind)
    return list(items.values())
