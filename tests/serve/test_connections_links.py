"""Graph view: REST calls and broker messages are drawn between the code that sends and the code that handles."""

from types import SimpleNamespace as NS

from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.serve.views import connections_view


def test_rest_and_broker_links_are_drawn_between_modules():
    g = CodeGraph()
    for i, repo, kind, name, file in (
            ("web:src/Users.tsx", "web", "file", "src/Users.tsx", "src/Users.tsx"),
            ("api:src/users.ts", "api", "file", "src/users.ts", "src/users.ts"),
            ("api:src/users.ts#getUser", "api", "function", "getUser", "src/users.ts"),
            ("api:http:GET /users/:id", "api", "http_route", "GET /users/:id", "src/app.ts"),
            ("api:msg:kafka:order.created", "api", "message_topic", "order.created", "src/users.ts"),
            ("api:msg:sqs:emails", "api", "message_topic", "emails", "src/users.ts"),
            ("mail:src/on.ts", "mail", "file", "src/on.ts", "src/on.ts")):
        g.add_node(CodeNode(id=i, repo=repo, kind=kind, name=name, file=file, line=3))
    for s, d, k, ok in (("web:src/Users.tsx", "api:http:GET /users/:id", "calls_http", True),
                        ("api:http:GET /users/:id", "api:src/users.ts#getUser", "served_by", True),
                        ("api:src/users.ts", "api:msg:kafka:order.created", "publishes", True),
                        ("api:msg:kafka:order.created", "mail:src/on.ts", "consumed_by", True),
                        ("api:src/users.ts", "api:msg:sqs:emails", "publishes", True),
                        ("api:msg:sqs:emails", "unresolved:message:emails", "consumed_by", False),
                        ("web:src/Users.tsx", "unresolved:http:GET /nope", "calls_http", False)):
        g.add_edge(CodeEdge(src=s, dst=d, kind=k, origin="linker", resolved=ok))
    cfg = NS(repos=[NS(name="web", role="frontend"), NS(name="api", role="backend"), NS(name="mail", role="backend")])
    pack = NS(graph=g, modules=[NS(id="users", name="Users", members=["web:src/Users.tsx", "api:src/users.ts"]),
                                NS(id="mail", name="Mail", members=["mail:src/on.ts"])])
    v = connections_view(cfg, pack)
    edges = {(e["source"], e["target"], e["kind"]): e for e in v["edges"]}
    rest = edges[("web::users", "api::users", "http")]
    assert rest["items"] == [{"label": "GET /users/:id", "from": {"repo": "web", "file": "src/Users.tsx", "line": 3},
                              "to": {"repo": "api", "file": "src/users.ts", "line": 3}}]
    kafka = edges[("api::users", "mail::mail", "kafka")]
    assert kafka["items"][0]["label"] == "order.created"
    assert ("api::users", "mail::mail", "sqs") not in edges
    assert v["unresolved"] == {"message": 1, "http": 1}
    assert [l["id"] for l in v["lanes"]] == ["web", "api", "mail"]
