from kb.agent import nodes
from kb.agent.graph import build_agent
from kb.packs.model import FlowStep

from agent_helpers import GOOD_STEPS, LEARN, build_pack, make_deps, save_flow, trace_reply
from fakes import FakeProvider


def run(deps, q, seed=None):
    return build_agent(deps).invoke({"question": q, "seed_qa_id": seed})


def test_understand_finds_tasks_module(tmp_path):
    store, cos = build_pack(tmp_path)
    out = nodes.understand({"question": "How does scheduling of repeating tasks work?"}, make_deps(store, cos, FakeProvider()))
    assert out["module_ids"][0] == "tasks" and out["kind"] == "how" and not out.get("not_found")


def test_understand_kinds(tmp_path):
    store, cos = build_pack(tmp_path)
    d = make_deps(store, cos, FakeProvider())
    assert nodes.understand({"question": "Where is the audit score computed?"}, d)["kind"] == "where"
    assert nodes.understand({"question": "What breaks if I change the tasks queue?"}, d)["kind"] == "impact"
    assert nodes.understand({"question": "Show me a diagram of tasks"}, d)["kind"] == "diagram"


def test_no_match_returns_not_found_and_saves_nothing(tmp_path):
    store, cos = build_pack(tmp_path)
    p = FakeProvider()
    out = run(make_deps(store, cos, p), "How does billing reconciliation work?")
    assert out["not_found"] and p.requests == []
    assert out["answer"].startswith("I couldn't find code for this in mini@main")
    assert "billing" in out["answer"]
    assert store.load("mini", "main").qa == []


def test_reuses_fresh_flow_without_llm(tmp_path):
    store, cos = build_pack(tmp_path)
    save_flow(store, cos)
    p = FakeProvider()
    out = run(make_deps(store, cos, p), "How does scheduling work in tasks?")
    assert out["reused_flow_id"] == "f1" and p.requests == []
    assert out["answer"] == "Saved answer." and out["steps"][0].text == "AddTask saves the task"


def test_stale_flow_not_reused(tmp_path):
    store, cos = build_pack(tmp_path)
    save_flow(store, cos, stale=True)
    out = nodes.understand({"question": "How does scheduling work in tasks?"}, make_deps(store, cos, FakeProvider()))
    assert not out.get("reused_flow_id")


def test_different_question_same_module_not_reused(tmp_path):
    store, cos = build_pack(tmp_path)
    save_flow(store, cos)
    out = nodes.understand({"question": "Who gets notified when a repeating task is due?"}, make_deps(store, cos, FakeProvider()))
    assert not out.get("reused_flow_id") and "tasks" in out["module_ids"]


def test_collect_follows_linker_edges_to_resolver_and_queue(tmp_path):
    store, cos = build_pack(tmp_path)
    out = nodes.collect({"question": "scheduling", "module_ids": ["tasks"]}, make_deps(store, cos, FakeProvider()))
    sl = out["shortlist"]
    assert sl[0] == "mini-fe:route:/tasks/create-task"
    for nid in ["mini-fe:src/tasks/task.graphql.ts#op:AddTask",
                "mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6", "mini-be:queue_topic:TASK_REPEAT"]:
        assert nid in sl
    assert "mini-be:src/audits/score.ts" not in sl and len(sl) <= 40


def test_trace_prompt_has_shortlist_tools_and_seed(tmp_path):
    store, cos = build_pack(tmp_path)
    save_flow(store, cos)
    p = FakeProvider([trace_reply(GOOD_STEPS)])
    d = make_deps(store, cos, p)
    out = nodes.trace({"question": "How do repeats work?", "module_ids": ["tasks"], "seed_qa_id": "q1",
                       "shortlist": ["mini-be:src/module/tasks/task/resolvers.ts#resolver:AddTask@6",
                                     "mini-be:queue_topic:TASK_REPEAT"]}, d)
    req = p.requests[0]
    assert req.allow_tools and req.cwd == cos["mini-fe"].root and req.extra_dirs == [cos["mini-be"].root]
    assert "resolvers.ts:6" in req.prompt and "Saved answer." in req.prompt and "Location: a franchise." in req.prompt
    assert "TASK_REPEAT" in req.prompt  # unresolved consumer is mentioned
    assert len(out["draft_steps"]) == 3 and out["answer"].startswith("Admins")


def test_verify_flags_missing_file_and_out_of_range_line(tmp_path):
    store, cos = build_pack(tmp_path)
    drafts = GOOD_STEPS + [
        {"side": "backend", "text": "ghost", "repo": "mini-be", "file": "src/nope.ts", "line_start": 1, "line_end": 2},
        {"side": "backend", "text": "too far", "repo": "mini-be", "file": "src/queue/user.ts", "line_start": 5, "line_end": 999},
        {"side": "ui", "text": "abs path", "repo": "mini-fe", "file": str(cos["mini-fe"].root / "src/App.tsx"), "line_start": 8, "line_end": 8},
        {"side": "backend", "text": "wrong repo", "repo": "mini-fe", "file": "src/queue/user.ts", "line_start": 1, "line_end": 3},
    ]
    steps = nodes.verify({"draft_steps": drafts}, make_deps(store, cos, FakeProvider()))["steps"]
    assert [s.verified for s in steps] == [True, True, True, False, False, True, True]
    assert steps[3].citation is None
    assert steps[5].citation.file == "src/App.tsx"
    assert steps[6].citation.repo == "mini-be"
    assert len(steps[0].citation.content_hash) == 16


def test_render_marks_unverified_in_mermaid(tmp_path):
    store, cos = build_pack(tmp_path)
    steps = [FlowStep(side="backend", text="Server saves it", verified=True),
             FlowStep(side="ui", text='Admin clicks "Publish"', verified=True),
             FlowStep(side="backend", text="Job fires", verified=False)]
    out = nodes.render({"steps": steps, "answer": "A."}, make_deps(store, cos, FakeProvider()))
    m = out["mermaid"]
    assert m.startswith("flowchart LR")
    assert m.index("Admin clicks") < m.index("Server saves it")
    assert "Job fires (unverified)" in m and '"Publish"' not in m


def test_learn_saves_private_items_and_suggestions(tmp_path):
    store, cos = build_pack(tmp_path)
    p = FakeProvider([trace_reply(GOOD_STEPS), LEARN])
    out = run(make_deps(store, cos, p), "How does scheduling of repeating tasks work?")
    pack = store.load("mini", "main")
    [qa] = pack.qa
    assert qa.visibility == "private" and qa.id == out["saved_qa_id"] and len(qa.citations) == 3
    assert pack.flows[0].visibility == "private" and pack.flows[0].mermaid == out["mermaid"]
    assert [s.question for s in pack.suggestions] == out["suggestions"] and len(out["suggestions"]) == 3
    assert pack.module_notes == {"tasks": "Better tasks summary."}


def test_learn_survives_bad_suggestion_json(tmp_path):
    store, cos = build_pack(tmp_path)
    p = FakeProvider([trace_reply(GOOD_STEPS), "nope", "nope"])
    out = run(make_deps(store, cos, p), "How does scheduling of repeating tasks work?")
    assert out["saved_qa_id"] and out["suggestions"] == []


def test_no_source_mode(tmp_path):
    store, _ = build_pack(tmp_path)
    p = FakeProvider([trace_reply([{"side": "backend", "text": "Server saves it", "repo": "mini-be",
                                    "file": "src/module/tasks/task/resolvers.ts", "line_start": 6, "line_end": 9}]), LEARN])
    out = run(make_deps(store, {}, p), "How does scheduling of repeating tasks work?")
    assert not p.requests[0].allow_tools and "no source" in p.requests[0].prompt.lower()
    assert out["answer"].startswith("No source verification —")
    assert all(not s.verified for s in out["steps"])


def test_verify_rejects_paths_escaping_the_checkout_and_keeps_dotfiles(tmp_path):
    from kb.ingest.checkout import Checkout

    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / ".eslintrc.js").write_text("module.exports = {};\n")
    (tmp_path / "outside").mkdir()
    (tmp_path / "outside" / "secret.txt").write_text("s\n")
    store, _ = build_pack(tmp_path)
    d = make_deps(store, {"r": Checkout("r", root, "x")}, FakeProvider())
    steps = nodes.verify({"draft_steps": [
        {"side": "ui", "text": "escape", "repo": "r", "file": "src/../../outside/secret.txt", "line_start": 1, "line_end": 1},
        {"side": "ui", "text": "dot", "repo": "r", "file": ".eslintrc.js", "line_start": 1, "line_end": 1},
        {"side": "ui", "text": "dot2", "repo": "r", "file": "./.eslintrc.js", "line_start": 1, "line_end": 1},
    ]}, d)["steps"]
    assert [s.verified for s in steps] == [False, True, True]
    assert steps[2].citation.file == ".eslintrc.js"


def test_learn_failure_still_returns_answer_and_indexes(tmp_path):
    from kb.errors import LLMUnavailable
    from kb.packs.index import search

    class Flaky(FakeProvider):
        def run(self, req):
            if self.requests:
                raise LLMUnavailable("not signed in")
            return super().run(req)

    store, cos = build_pack(tmp_path)
    out = run(make_deps(store, cos, Flaky([trace_reply(GOOD_STEPS)])), "How does scheduling of repeating tasks work?")
    assert out["answer"].startswith("Admins") and out["saved_qa_id"] and out["suggestions"] == []
    assert any(h.kind == "qa" for h in search(store.pack_dir("mini", "main"), "scheduling repeating"))


def test_code_is_stripped_from_saved_prose(tmp_path):
    steps = [dict(GOOD_STEPS[0], text="Calls `submitTask({ variables: { input } })` on publish")] + GOOD_STEPS[1:]
    answer = "It works like this:\n```ts\nconst SECRET_CODE_MARKER = 1;\n```\nThe server saves it."
    store, cos = build_pack(tmp_path)
    out = run(make_deps(store, cos, FakeProvider([trace_reply(steps, answer), LEARN])), "How does scheduling of repeating tasks work?")
    pack = store.load("mini", "main")
    blob = pack.qa[0].model_dump_json() + pack.flows[0].model_dump_json()
    assert "SECRET_CODE_MARKER" not in blob and "variables:" not in blob
    assert "The server saves it." in out["answer"]


def test_trace_prompt_forbids_quoting_code(tmp_path):
    store, cos = build_pack(tmp_path)
    p = FakeProvider([trace_reply(GOOD_STEPS)])
    nodes.trace({"question": "q", "module_ids": ["tasks"], "shortlist": []}, make_deps(store, cos, p))
    assert "do not quote source code" in (p.requests[0].system + p.requests[0].prompt).lower()


def test_incomplete_pack_not_found_mentions_resume(tmp_path):
    store, cos = build_pack(tmp_path)
    store.update_manifest("mini", "main", status="incomplete")
    out = nodes.render({"question": "billing?", "not_found": True}, make_deps(store, cos, FakeProvider()))
    assert "kb analyze --resume" in out["answer"]


def test_collect_without_modules_uses_node_hits(tmp_path):
    store, cos = build_pack(tmp_path)
    d = make_deps(store, cos, FakeProvider())
    d.pack.modules = []
    sl = nodes.collect({"question": "scheduleRepeat", "module_ids": []}, d)["shortlist"]
    assert "mini-be:src/module/tasks/task/resolvers.ts#scheduleRepeat" in sl


def test_no_reuse_flag_forces_fresh_trace(tmp_path):
    store, cos = build_pack(tmp_path)
    save_flow(store, cos)
    out = nodes.understand({"question": "How does scheduling work in tasks?", "no_reuse": True}, make_deps(store, cos, FakeProvider()))
    assert not out.get("reused_flow_id")


def test_trace_prompt_asks_to_cite_graphql_operation_for_requests(tmp_path):
    store, cos = build_pack(tmp_path)
    p = FakeProvider([trace_reply(GOOD_STEPS)])
    nodes.trace({"question": "q", "module_ids": ["tasks"], "shortlist": ["mini-fe:src/tasks/task.graphql.ts#op:AddTask"]},
                make_deps(store, cos, p))
    pr = p.requests[0].prompt
    assert "graphql_op" in pr and "cite the operation's definition" in pr
