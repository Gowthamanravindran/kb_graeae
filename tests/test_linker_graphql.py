from kb.linkers.graphql import GraphqlLinker

from linker_helpers import ctx, run

CFG = {"type": "graphql", "from": "mini-fe", "to": "mini-be", "options": {"backend_style": "graphql-compose"}}


def test_addtask_links_frontend_to_resolver():
    c = ctx(CFG)
    edges = run(GraphqlLinker(), c)
    [e] = [e for e in edges if e.kind == "handled_by" and e.resolved]
    op, res = c.graph.node(e.src), c.graph.node(e.dst)
    assert (op.kind, op.name, op.repo, op.file) == ("graphql_op", "AddTask", "mini-fe", "src/tasks/task.graphql.ts")
    assert (res.kind, res.name, res.repo, res.file, res.line) == ("resolver", "AddTask", "mini-be", "src/module/tasks/task/resolvers.ts", 6)
    assert e.origin == "linker"


def test_component_sends_op():
    c = ctx(CFG)
    edges = run(GraphqlLinker(), c)
    sends = [(e.src, c.graph.node(e.dst).name) for e in edges if e.kind == "sends_graphql"]
    assert ("mini-fe:src/tasks/TaskCreation.tsx", "AddTask") in sends


def test_missing_backend_field_is_unresolved():
    c = ctx(CFG)
    edges = run(GraphqlLinker(), c)
    un = [e for e in edges if not e.resolved]
    assert [e.dst for e in un] == ["unresolved:graphql:TaskById"]
    assert c.graph.node(un[0].src).name == "TaskById"


def test_sdl_style(tmp_path):
    fe, be = tmp_path / "fe", tmp_path / "be"
    fe.mkdir(), be.mkdir()
    (fe / "q.ts").write_text("export const q = gql`query Me { me { id } }`;\n")
    (be / "schema.graphql").write_text("type User { id: ID }\n\ntype Query {\n  me: User\n}\n")
    c = ctx({"type": "graphql", "from": "fe", "to": "be"}, repos=("fe", "be"), roots={"fe": fe, "be": be})
    edges = run(GraphqlLinker(), c)
    [e] = [e for e in edges if e.kind == "handled_by"]
    res = c.graph.node(e.dst)
    assert e.resolved and (res.file, res.line, res.name) == ("schema.graphql", 4, "me")


def test_invalid_graphql_template_skipped(tmp_path):
    fe, be = tmp_path / "fe", tmp_path / "be"
    fe.mkdir(), be.mkdir()
    (fe / "q.ts").write_text("export const q = gql`query { ${frag} broken`;\n")
    c = ctx({"type": "graphql", "from": "fe", "to": "be"}, repos=("fe", "be"), roots={"fe": fe, "be": be})
    assert run(GraphqlLinker(), c) == []


def _svc(tmp_path, name, files):
    d = tmp_path / name
    for rel, text in files.items():
        (d / rel).parent.mkdir(parents=True, exist_ok=True)
        (d / rel).write_text(text)
    return d


def test_many_apps_to_many_services(tmp_path):
    web = _svc(tmp_path, "web", {"q.ts": "export const a = gql`query Me { me { id } }`;\nexport const b = gql`mutation Pay { pay { ok } }`;\n"})
    mobile = _svc(tmp_path, "mobile", {"q.ts": "export const m = gql`query Me2 { me { id } }`;\n"})
    users = _svc(tmp_path, "users", {"schema.graphql": "type Query {\n  me: User\n}\n"})
    pay = _svc(tmp_path, "payments", {"schema.graphql": "type Mutation {\n  pay: Result\n}\n"})
    roots = {"web": web, "mobile": mobile, "users": users, "payments": pay}
    c = ctx({"type": "graphql", "from": ["web", "mobile"], "to": ["users", "payments"]}, repos=tuple(roots), roots=roots)
    edges = run(GraphqlLinker(), c)
    handled = sorted((c.graph.node(e.src).repo, c.graph.node(e.src).name, c.graph.node(e.dst).repo)
                     for e in edges if e.kind == "handled_by" and e.resolved)
    assert handled == [("mobile", "Me2", "users"), ("web", "Me", "users"), ("web", "Pay", "payments")]
    # an operation one service handles is not "unmatched" just because the other service doesn't have it
    assert [e for e in edges if not e.resolved] == []


def test_backend_style_per_service(tmp_path):
    web = _svc(tmp_path, "web", {"q.ts": "export const a = gql`query Me { me { id } }`;\nexport const b = gql`mutation Pay { pay { ok } }`;\n"})
    users = _svc(tmp_path, "users", {"schema.graphql": "type Query {\n  me: User\n}\n"})
    pay = _svc(tmp_path, "payments", {"r.ts": "export const pay = { name: 'pay', type: 'R', resolve: () => 1 };\n"})
    roots = {"web": web, "users": users, "payments": pay}
    c = ctx({"type": "graphql", "from": "web", "to": ["users", "payments"],
             "options": {"backend_style": {"users": "sdl", "payments": "graphql-compose"}}}, repos=tuple(roots), roots=roots)
    edges = run(GraphqlLinker(), c)
    assert sorted(c.graph.node(e.dst).repo for e in edges if e.kind == "handled_by" and e.resolved) == ["payments", "users"]
