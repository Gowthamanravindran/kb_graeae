"""A scripted Claude for test-planning tests: answers by prompt type, so slot ids need not be known upfront."""

from __future__ import annotations

import json
import re

from fakes import FakeProvider

STEPS = [
    {"side": "ui", "text": "Admin publishes the task form", "repo": "mini-fe", "file": "src/tasks/TaskCreation.tsx", "line_start": 5, "line_end": 8},
    {"side": "backend", "text": "AddTask saves the task", "repo": "mini-be", "file": "src/module/tasks/task/resolvers.ts", "line_start": 6, "line_end": 12},
]


def j(obj) -> str:
    return "```json\n" + json.dumps(obj) + "\n```"


class DesignFake(FakeProvider):
    def __init__(self, fail_on: str | None = None, extras: int = 0, **kw):
        super().__init__(**kw)
        self.fail_on, self.extras = fail_on, extras

    def run(self, req):
        from kb.llm.base import LLMResult

        self.check()
        self.requests.append(req)  # list.append is atomic; flows run in parallel threads
        p = req.prompt
        if self.fail_on and self.fail_on in p:
            raise RuntimeError(f"scripted failure for {self.fail_on}")
        text = self.reply(p)
        return LLMResult(text=text, steps=[], input_tokens=10, output_tokens=5, cost_usd=0.0, duration_ms=1)

    def reply(self, p: str) -> str:
        if p.startswith("Name each user-facing flow"):
            return j({"names": {}})
        if "Write exactly one test case for each slot" in p:
            ids = re.findall(r"^- slot (\w+) \|", p, re.M)
            return j({"cases": [{"slot_id": i, "title": f"Case for {i}", "preconditions": ["Signed in as an admin"],
                                         "steps": ["Open Tasks → Create task", "Save"], "test_data": ["Title: Fire exit check"],
                                         "expected": ["The task is saved"]} for i in ids],
                              "extras": [{"title": f"Extra {k}", "steps": ["Do"], "expected": ["Done"]} for k in range(self.extras)]})
        if "Write one test scenario for each of these types" in p:
            types = p.split("Write one test scenario for each of these types: ", 1)[1].split(".", 1)[0].split(", ")
            return j({"scenarios": [{"type": t, "title": f"Scenario {t}", "description": "", "inventory_ids": [],
                                             "step_refs": [0]} for t in types]})
        return j({"steps": STEPS, "answer": "Admins create tasks; the server saves them."})  # a trace
