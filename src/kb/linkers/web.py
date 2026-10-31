"""Language-neutral records for REST linking: routes a server declares and calls a client makes, and how a call's
path is matched to a route's. Each language's reader (`rest.py` for JS/TS, `rails.py` for Ruby) produces these."""

from __future__ import annotations

import re
from dataclasses import dataclass

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


def score(route: list[str], call: list[str]) -> tuple[int, int] | None:
    """When the route lines up with the end of the call's path: (fixed segments that match, parameters that meet
    parameters), compared in that order, so `/users/${id}` prefers `/users/:id` to `/users/me`. None if it can't."""
    if not route or len(route) > len(call):
        return None
    fixed = params = 0
    for r, c in zip(reversed(route), reversed(call)):
        if r == "*" or c == "*":
            params += r == c
            continue
        if r != c:
            return None
        fixed += 1
    return (fixed, params) if fixed else None


def _join(*parts: str) -> str:
    return "/" + "/".join(p.strip("/") for p in parts if p and p.strip("/"))


@dataclass
class Route:
    method: str
    path: str
    file: tuple[str, str]
    line: int
    handler: str = ""                                             # a function name, resolved to its nearest declaration
    ref: tuple[tuple[str, str], str, str, int] | None = None      # or a method: (file, class, method, line)

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
