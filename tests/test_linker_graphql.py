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
