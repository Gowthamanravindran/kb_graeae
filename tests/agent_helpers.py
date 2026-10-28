import json
from datetime import datetime, timezone
from pathlib import Path

from kb.agent.graph import AgentDeps
from kb.graph.model import CodeEdge, CodeGraph, CodeNode
from kb.ingest.checkout import Checkout
from kb.packs.index import build_index
from kb.packs.model import Citation, Flow, FlowStep, Manifest, Module, QAItem
from kb.packs.staleness import hash_span
from kb.packs.store import PackStore
from kb.tracing import NoopTracer

FIX = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 10, 28, tzinfo=timezone.utc)


def N(repo, file, name=None, kind="file", line=1):
    nid = f"{repo}:{file}" + (f"#{name}" if name else "")
    return CodeNode(id=nid, repo=repo, kind=kind, name=name or file, file=file, line=line)


def build_pack(tmp_path, fe_root=None, be_root=None):
    fe_root, be_root = fe_root or FIX / "mini-fe", be_root or FIX / "mini-be"
    fe, be, links = CodeGraph(), CodeGraph(), CodeGraph()
    for n in [N("mini-fe", "src/App.tsx"), N("mini-fe", "src/tasks/TaskCreation.tsx"),
              N("mini-fe", "src/tasks/TaskCreation.tsx", "TaskCreation", "component", 5),
              N("mini-fe", "src/tasks/task.graphql.ts")]:
        fe.add_node(n)
    fe.add_edge(CodeEdge(src="mini-fe:src/tasks/TaskCreation.tsx", dst="mini-fe:src/tasks/TaskCreation.tsx#TaskCreation",
                         kind="contains", origin="extracted"))
    for n in [N("mini-be", "src/module/tasks/task/resolvers.ts"),
              N("mini-be", "src/module/tasks/task/resolvers.ts", "scheduleRepeat", "function", 18),
              N("mini-be", "src/queue/task/main.ts"), N("mini-be", "src/audits/score.ts"),
              N("mini-be", "src/audits/score.ts", "computeAuditScore", "function", 1)]:
        be.add_node(n)
    be.add_edge(CodeEdge(src="mini-be:src/module/tasks/task/resolvers.ts",
                         dst="mini-be:src/module/tasks/task/resolvers.ts#scheduleRepeat", kind="contains", origin="extracted"))
    link_nodes = [N("mini-fe", "src/App.tsx", None, "route", 8).model_copy(update={"id": "mini-fe:route:/tasks/create-task", "name": "/tasks/create-task"}),
                  CodeNode(id="mini-fe:src/tasks/task.graphql.ts#op:AddTask", repo="mini-fe", kind="graphql_op", name="AddTask", file="src/tasks/task.graphql.ts", line=3),
                  CodeNode(id="mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6", repo="mini-be", kind="resolver", name="AddTask", file="src/module/tasks/task/resolvers.ts", line=6),
                  CodeNode(id="mini-be:queue_topic:TASK_REPEAT", repo="mini-be", kind="queue_topic", name="TASK_REPEAT", file="src/queue/task/main.ts", line=1)]
    edges = [
        CodeEdge(src="mini-fe:route:/tasks/create-task", dst="mini-fe:src/tasks/TaskCreation.tsx", kind="routes_to", origin="linker"),
        CodeEdge(src="mini-fe:src/tasks/TaskCreation.tsx", dst="mini-fe:src/tasks/task.graphql.ts#op:AddTask", kind="sends_graphql", origin="linker"),
        CodeEdge(src="mini-fe:src/tasks/task.graphql.ts#op:AddTask", dst="mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6", kind="handled_by", origin="linker"),
        CodeEdge(src="mini-be:src/module/tasks/task/resolvers.ts", dst="mini-be:queue_topic:TASK_REPEAT", kind="enqueues", origin="linker"),
        CodeEdge(src="mini-be:queue_topic:TASK_REPEAT", dst="unresolved:queue:TASK_REPEAT", kind="consumed_by", origin="linker", resolved=False),
    ]
    tasks_members = ["mini-fe:route:/tasks/create-task", "mini-fe:src/tasks/TaskCreation.tsx", "mini-fe:src/tasks/TaskCreation.tsx#TaskCreation",
                     "mini-fe:src/tasks/task.graphql.ts", "mini-fe:src/tasks/task.graphql.ts#op:AddTask",
                     "mini-be:src/module/tasks/task/resolvers.ts", "mini-be:src/module/tasks/task/resolvers.ts#scheduleRepeat",
                     "mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6", "mini-be:queue_topic:TASK_REPEAT", "mini-be:src/queue/task/main.ts"]
    modules = [
        Module(id="tasks", name="Tasks", summary="Admins create tasks once or on a repeating schedule; the server queues each repeat.",
               members=tasks_members, entry_points=["mini-fe:route:/tasks/create-task"]),
        Module(id="audits", name="Audits", summary="Inspectors score Locations against audit checklists.",
               members=["mini-be:src/audits/score.ts", "mini-be:src/audits/score.ts#computeAuditScore"],
               entry_points=["mini-be:src/audits/score.ts"]),
    ]
    store = PackStore(tmp_path / "home")
    m = Manifest(project="mini", branch="main", commits={"mini-fe": "x", "mini-be": "y"}, author="me", created_at=NOW, status="complete")
    store.write_base("mini", "main", m, {"mini-fe": fe, "mini-be": be}, edges, modules, "Overview", link_nodes=link_nodes)
    pack = store.load("mini", "main")
    build_index(pack.dir, pack)
    checkouts = {"mini-fe": Checkout("mini-fe", fe_root, "x"), "mini-be": Checkout("mini-be", be_root, "y")}
    return store, checkouts


def make_deps(store, checkouts, provider, tracer=None):
    pack = store.load("mini", "main")
    return AgentDeps(pack=pack, pack_dir=pack.dir, checkouts=checkouts, provider=provider, tracer=tracer or NoopTracer(),
                     store=store, author="me", context="Location: a franchise.")


def save_flow(store, checkouts, question="How does scheduling work in Tasks?", stale=False):
    root = checkouts["mini-be"].root
    f = "src/module/tasks/task/resolvers.ts"
    h = hash_span(root, f, 6, 9)
    c = Citation(repo="mini-be", file=f, line_start=6, line_end=9, content_hash="changed" if stale else h)
    flow = Flow(id="f1", question=question, module_ids=["tasks"],
                steps=[FlowStep(side="backend", text="AddTask saves the task", citation=c, verified=True)],
                mermaid="flowchart LR\n  s1[\"AddTask saves the task\"]", created_at=NOW)
    store.save_flow("mini", "main", flow)
    store.save_qa("mini", "main", QAItem(id="q1", question=question, answer="Saved answer.", flow_id="f1", citations=[c],
                                         author="me", created_at=NOW))
    build_index(store.pack_dir("mini", "main"), store.load("mini", "main"))


def trace_reply(steps, answer="Admins pick repeat; the server queues a delayed job."):
    return "```json\n" + json.dumps({"steps": steps, "answer": answer}) + "\n```"


GOOD_STEPS = [
    {"side": "ui", "text": "Admin publishes the task form", "repo": "mini-fe", "file": "src/tasks/TaskCreation.tsx", "line_start": 5, "line_end": 8},
    {"side": "backend", "text": "AddTask saves the task", "repo": "mini-be", "file": "src/module/tasks/task/resolvers.ts", "line_start": 6, "line_end": 15},
    {"side": "backend", "text": "A delayed TASK_REPEAT job is queued", "repo": "mini-be", "file": "src/module/tasks/task/resolvers.ts", "line_start": 18, "line_end": 20},
]
LEARN = "```json\n" + json.dumps({"suggestions": ["How are repeats ended?", "Who is notified?", "How are timezones applied?"],
                                  "module_notes": [{"module_id": "tasks", "improved_summary": "Better tasks summary."}]}) + "\n```"
