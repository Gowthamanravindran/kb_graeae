import pytest

from kb.errors import ConfigError
from kb.linkers.base import get_linker
from kb.linkers.bull_queue import BullQueueLinker
from kb.linkers.react_router import ReactRouterLinker

from linker_helpers import ctx, run

QCFG = {"type": "bull-queue", "in": "mini-be"}


def test_producer_without_consumer_is_unresolved():
    c = ctx(QCFG, repos=("mini-be",))
    edges = run(BullQueueLinker(), c)
    enq = [(e.src, c.graph.node(e.dst).name) for e in edges if e.kind == "enqueues"]
    assert ("mini-be:src/module/tasks/task/resolvers.ts", "TASK_REPEAT") in enq
    assert ("mini-be:src/queue/task/main.ts", "TASK_REPEAT") in enq
    un = [e for e in edges if e.kind == "consumed_by" and not e.resolved and e.dst == "unresolved:queue:TASK_REPEAT"]
    assert len(un) == 1


def test_string_topic_links():
    c = ctx(QCFG, repos=("mini-be",))
    edges = run(BullQueueLinker(), c)
    topic = "mini-be:queue_topic:SIGN_UP"
    assert any(e.kind == "enqueues" and e.src == "mini-be:src/queue/user.ts" and e.dst == topic for e in edges)
    assert any(e.kind == "consumed_by" and e.src == topic and e.dst == "mini-be:src/queue/index.ts" and e.resolved for e in edges)


def test_dynamic_topic_consumer_unresolved():
    c = ctx(QCFG, repos=("mini-be",))
    edges = run(BullQueueLinker(), c)
    assert any(e.src == "mini-be:src/queue/index.ts" and e.dst == "unresolved:queue:topic" and not e.resolved for e in edges)


def test_route_constant_resolved_to_component():
    c = ctx({"type": "react-router", "in": "mini-fe"}, repos=("mini-fe",))
    edges = run(ReactRouterLinker(), c)
    ok = [e for e in edges if e.resolved]
    assert len(ok) == 1
    route = c.graph.node(ok[0].src)
    assert (route.kind, route.name, route.file, route.line) == ("route", "/tasks/create-task", "src/App.tsx", 8)
    assert ok[0].dst == "mini-fe:src/tasks/TaskCreation.tsx"


def test_unimported_route_component_unresolved():
    c = ctx({"type": "react-router", "in": "mini-fe"}, repos=("mini-fe",))
    edges = run(ReactRouterLinker(), c)
    assert [e.dst for e in edges if not e.resolved] == ["unresolved:route:NotImported"]


def test_lazy_import_and_element_prop(tmp_path):
    (tmp_path / "pages").mkdir()
    (tmp_path / "pages" / "Home.tsx").write_text("export default () => null;\n")
    (tmp_path / "App.tsx").write_text(
        "const Home = React.lazy(() => import('./pages/Home'));\n"
        "export const A = () => <Route path='/' element={<Home />} />;\n"
    )
    c = ctx({"type": "react-router", "in": "w"}, repos=("w",), roots={"w": tmp_path})
    [e] = run(ReactRouterLinker(), c)
    assert e.resolved and e.dst == "w:pages/Home.tsx"


def test_get_linker_known_and_unknown():
    assert get_linker("graphql").type == "graphql"
    with pytest.raises(ConfigError):
        get_linker("kafka")
