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
    # the job is handled by userSignUpJob (queue/user.ts), not by index.ts where it is registered
    assert any(e.kind == "consumed_by" and e.src == topic and e.dst == "mini-be:src/queue/user.ts#userSignUpJob" and e.resolved
               for e in edges)
    handler = c.graph.node("mini-be:src/queue/user.ts#userSignUpJob")
    assert (handler.name, handler.file, handler.line) == ("userSignUpJob", "src/queue/user.ts", 1)


def test_central_registry_resolves_each_handler(tmp_path):
    root = tmp_path / "be"
    (root / "src" / "queue" / "user").mkdir(parents=True)
    (root / "src" / "queue" / "payments").mkdir()
    (root / "src" / "legacy").mkdir()
    (root / "src" / "queue" / "index.ts").write_text(
        "import consumer from './consumer'\nconst { userSignUpJob, syncSubscriptions } = consumer\n"
        "export function start(JobsQueue) {\n  JobsQueue.process(qTopics.SIGN_UP, userSignUpJob)\n"
        "  JobsQueue.process(qTopics.SYNC_SUBS, 5, consumer.syncSubscriptions)\n"
        "  JobsQueue.process('INLINE', async (job) => job.data)\n}\n")
    (root / "src" / "queue" / "user" / "index.ts").write_text("const userSignUpJob = async (job, done) => { done() }\nexport { userSignUpJob }\n")
    (root / "src" / "legacy" / "old.ts").write_text("export const userSignUpJob = () => 1\n")  # same name, further away
    (root / "src" / "queue" / "payments" / "syncSubscriptions.ts").write_text("export async function syncSubscriptions(job) { return 1 }\n")
    c = ctx(QCFG, repos=("mini-be",), roots={"mini-be": root})
    edges = run(BullQueueLinker(), c)
    cons = {c.graph.node(e.src).name: e.dst for e in edges if e.kind == "consumed_by" and e.resolved}
    assert cons == {"SIGN_UP": "mini-be:src/queue/user/index.ts#userSignUpJob",
                    "SYNC_SUBS": "mini-be:src/queue/payments/syncSubscriptions.ts#syncSubscriptions",
                    "INLINE": "mini-be:src/queue/index.ts"}  # an inline handler stays with the registering file


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


def test_inline_handler_and_concurrency_never_copy_source(tmp_path):
    (tmp_path / "q.ts").write_text(
        "emailQueue.process(async (job) => { const s = 'SECRET_INLINE_MARKER'; return s; });\n"
        "jobsQueue.process(5, handler);\n")
    c = ctx({"type": "bull-queue", "in": "w"}, repos=("w",), roots={"w": tmp_path})
    edges = run(BullQueueLinker(), c)
    dump = repr([e.model_dump() for e in edges]) + repr([n.model_dump() for n in c.graph.nodes()])
    assert "SECRET_INLINE_MARKER" not in dump and "=>" not in dump
    assert {e.dst for e in edges} == {"unresolved:queue:<all-jobs>"}
