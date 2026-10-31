"""Decision points in Ruby (Rails controllers, models, jobs, services) for the change inventory. Same kinds and
outcomes as the JS/TS reader: branch, permission, validation, error, query, field."""

from __future__ import annotations

import re

from kb.linkers import rb

PERMISSION = re.compile(r"(admin|owner|role|permi|authori[sz]|can\?|cannot\?|allowed|policy|signed_in|current_user"
                        r"|access|member\?|staff)", re.I)
VALIDATES = re.compile(r"(valid|blank\?|present\?|empty\?|nil\?|params\[|match\?|=~|errors)", re.I)
AUTH_FILTER = re.compile(r"(authenticat|authoriz|require|ensure|check|verify|admin|permission|login|signed_in|access)",
                         re.I)
DENY_STATUS = {"forbidden": "permission", "unauthorized": "permission", "401": "permission", "403": "permission",
               "unprocessable_entity": "validation", "unprocessable_content": "validation", "bad_request": "validation",
               "422": "validation", "400": "validation"}
AR_METHODS = {"where", "find", "find_by", "find_by!", "find_each", "find_in_batches", "create", "create!",
              "update_all", "delete_all", "destroy_all", "find_or_create_by", "find_or_create_by!",
              "find_or_initialize_by", "first_or_create", "exists?", "count", "pluck", "insert_all", "upsert_all",
              "upsert", "insert", "includes", "joins", "sum", "average", "minimum", "maximum", "all", "none"}
CHAIN_QUERY = {"where", "find", "find_by", "find_by!", "order", "pluck", "includes", "joins", "exists?", "find_each"}
NOT_RECORDS = {"params", "session", "cookies", "headers", "opts", "options", "attrs", "attributes", "hash", "h",
               "data", "payload", "body", "env", "request", "response", "flash", "args", "kwargs", "config"}
PERSIST = {"save", "save!", "update", "update!", "destroy", "destroy!", "delete", "update_column", "update_columns",
           "update_attribute", "increment!", "decrement!", "toggle!", "touch", "reload"}
NOT_MODELS = {"Rails", "Time", "Date", "DateTime", "JSON", "ENV", "Sidekiq", "Karafka", "Faraday", "HTTParty",
              "RestClient", "File", "Dir", "Struct", "Hash", "Array", "String", "Integer", "Float", "Kernel", "URI",
              "Net", "Logger", "I18n", "SecureRandom", "Digest", "Base64", "OpenSSL", "Redis", "Thread", "Process"}


def _t(n) -> str:
    return n.text.decode("utf-8", "replace")


def _norm(s: str) -> str:
    return " ".join(s.split())


def _status(c: rb.Call) -> str | None:
    """The response status of `head :forbidden` / `render …, status: 403`, as text."""
    if c.receiver:
        return None
    if c.name == "head" and c.args:
        return rb.literal(c.args[0]) or (_t(c.args[0]) if c.args[0].type == "integer" else None)
    if c.name == "render" and "status" in c.kwargs:
        v = c.kwargs["status"]
        return rb.literal(v) or (_t(v) if v.type == "integer" else None)
    return None


def _is_raise(n) -> bool:
    return (n.type == "call" and n.child_by_field_name("receiver") is None
            and (m := n.child_by_field_name("method")) is not None and _t(m) in ("raise", "fail")) or \
        (n.type == "identifier" and _t(n) in ("raise", "fail"))


def _denial(body, cond: str) -> str | None:
    """What a guard's body does when it stops the request: 'permission', 'validation' or None (an ordinary branch).
    A 401/403 is a permission check, a 400/422 a validation; a raise is classified by its condition."""
    if body is None:
        return None
    for n in rb._walk(body):
        if n.type == "call" and (c := rb._call(n)) is not None and (s := _status(c)) is not None:
            if s in DENY_STATUS:
                return DENY_STATUS[s]
        if _is_raise(n):
            return "permission" if PERMISSION.search(cond) else "validation" if VALIDATES.search(cond) else None
    return None


def _chain(n):
    """(root receiver node, method names) of `A.where(…).order(…)`."""
    names = []
    while n is not None and n.type == "call":
        m = n.child_by_field_name("method")
        names.append(_t(m) if m is not None else "")
        n = n.child_by_field_name("receiver")
    return n, names


def extract(source: str, start: int, end: int):
    from kb.testplan.inventory import Decision

    out: list[Decision] = []
    skip: list[tuple[int, int]] = []   # bodies of guards already recorded as permission/validation checks

    def skipped(n) -> bool:
        return any(a <= n.start_byte < b for a, b in skip)

    def add(kind, text, outcomes, line):
        out.append(Decision(kind, _norm(text)[:200], outcomes, line))

    tree = rb.parse_text(source)
    for n in rb._walk(tree.root_node):
        if not n.is_named:
            continue
        line = n.start_point[0] + 1
        if not (start <= line <= end):
            continue
        t = n.type
        if t in ("if", "unless", "elsif", "if_modifier", "unless_modifier"):
            cond_n = n.child_by_field_name("condition")
            cond = _t(cond_n) if cond_n is not None else ""
            body = n.child_by_field_name("consequence") if t in ("if", "unless", "elsif") else n.child_by_field_name("body")
            kind = _denial(body, cond)
            if kind == "permission":
                add("permission", cond, ["allowed", "denied"], line)
            elif kind == "validation":
                add("validation", cond, ["valid", "invalid"], line)
            else:
                add("branch", cond, ["true", "false"], line)
                continue
            skip.append((body.start_byte, body.end_byte))
        elif t == "conditional":
            cond_n = n.child_by_field_name("condition")
            add("branch", _t(cond_n) if cond_n is not None else _t(n), ["true", "false"], line)
        elif t == "case":
            val = n.child_by_field_name("value")
            whens = [_t(p) for w in n.named_children if w.type == "when"
                     for p in w.named_children if p.type == "pattern"]
            add("branch", f"case {_t(val) if val is not None else ''}".strip(), whens + ["else"], line)
        elif t == "rescue":
            exc = n.child_by_field_name("exceptions")
            add("error", f"rescue {_t(exc) if exc is not None else 'StandardError'}", ["error"], line)
        elif _is_raise(n) and not skipped(n):
            add("error", _t(n), ["error"], line)
        elif t == "call" and (c := rb._call(n)) is not None:
            parent = n.parent
            if parent is not None and parent.type == "call" and parent.child_by_field_name("receiver") == n:
                continue   # only the whole chain counts
            if not c.receiver and c.name in ("authorize", "authorize!", "authorize_resource", "load_and_authorize_resource"):
                add("permission", _t(n), ["allowed", "denied"], line)
            elif not c.receiver and c.name in ("before_action", "before_filter", "prepend_before_action") and c.args \
                    and AUTH_FILTER.search(rb.literal(c.args[0]) or ""):
                add("permission", _t(n), ["allowed", "denied"], line)
            elif not c.receiver and (c.name in ("validates", "validate", "validates_with", "validates_associated")
                                     or re.match(r"^validates_\w+_of$", c.name)):
                add("validation", _t(n), ["valid", "invalid"], line)
            elif (s := _status(c)) is not None and not skipped(n) and (s in DENY_STATUS or re.match(r"^(4\d\d|not_found|conflict|gone|payment_required|too_many_requests)$", s)):
                add("error", _t(n), ["error"], line)
            elif c.name == "permit" and "params" in c.receiver:
                for a in c.args:
                    if (v := rb.literal(a)) is not None:
                        add("field", v, ["sent"], a.start_point[0] + 1)
                for k in c.kwargs:
                    add("field", k, ["sent"], line)
            else:
                root, names = _chain(n)
                rname = _t(root) if root is not None else ""
                model = root is not None and root.type in ("constant", "scope_resolution") and \
                    rb.const_path(root).split("::")[0] not in NOT_MODELS and not rb.const_path(root).isupper() and \
                    re.search(r"[a-z]", rb.const_path(root).rsplit("::", 1)[-1]) is not None
                record = root is not None and root.type in ("instance_variable", "identifier", "self", "call") and \
                    rname.lstrip("@") not in NOT_RECORDS
                if model and AR_METHODS & set(names):          # Order.where(…), not STATUSES.find / CSV.parse.count
                    add("query", _t(n), ["runs"], line)
                elif record and len(names) > 1 and CHAIN_QUERY & set(names[1:] + names[:1]):
                    add("query", _t(n), ["runs"], line)     # current_user.orders.where(…), @orders.where(…)
                elif record and len(names) == 1 and CHAIN_QUERY & set(names) and root.type == "instance_variable":
                    add("query", _t(n), ["runs"], line)
                elif c.name in PERSIST and record and not (c.name in ("delete", "update") and c.args and
                                                          c.args[0].type in ("simple_symbol", "string", "hash")) \
                        and not (c.name == "update" and c.kwargs and not c.args):
                    add("query", _t(n), ["runs"], line)       # @order.destroy!, not opts.update(b: 2)
    return out
