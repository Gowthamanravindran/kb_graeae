import pytest

from kb.agent import nodes
from kb.agent.graph import AgentDeps, build_agent
from kb.tracing import NoopTracer

NAMES = ["understand", "collect", "trace", "verify", "render", "learn"]


def deps():
    return AgentDeps(pack=None, pack_dir=None, checkouts={}, provider=None, tracer=NoopTracer(), store=None, author="me")


@pytest.mark.parametrize("case,expected", [
    ({}, NAMES),
    ({"reused_flow_id": "f1"}, ["understand", "render"]),
    ({"not_found": True}, ["understand", "render"]),
])
def test_graph_routes(monkeypatch, case, expected):
    visited = []

    def stub(name):
        def fn(state, deps):
            visited.append(name)
            return case if name == "understand" else {}
        return fn

    for n in NAMES:
        monkeypatch.setattr(nodes, n, stub(n), raising=False)
    build_agent(deps()).invoke({"question": "q", "seed_qa_id": None})
    assert visited == expected
