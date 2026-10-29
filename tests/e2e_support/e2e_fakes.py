"""Fake Claude for desktop E2E runs (KB_SERVE_PROVIDER=e2e_fakes:make_provider). Never used in production."""
import json
import sys
from pathlib import Path

sys.path[:0] = [str(Path(__file__).parents[1]), str(Path(__file__).parents[1] / "testplan")]

from agent_helpers import GOOD_STEPS, LEARN, trace_reply  # noqa: E402
from fakes import FakeProvider, step  # noqa: E402

DRAFT = "```json\n" + json.dumps({"name": "Tasks", "summary": "People create and schedule repeating tasks.", "entry_points": []}) + "\n```"


class ScriptedFake(FakeProvider):
    def run(self, req):
        self.check()
        p = req.prompt
        from design_fake import DesignFake

        if p.startswith("Name this group"):
            text = DRAFT
        elif (p.startswith("Name each user-facing flow") or "Write exactly one test case for each slot" in p
              or "Write one test scenario for each of these types" in p):
            text = DesignFake().reply(p)
        elif p.startswith("Question:"):
            text = trace_reply(GOOD_STEPS)
        elif p.startswith("A user asked"):
            text = LEARN
        else:
            text = "The system overview."
        from kb.llm.base import LLMResult

        return LLMResult(text=text, steps=[step("Read", "src/tasks/TaskCreation.tsx")], input_tokens=10, output_tokens=5,
                         cost_usd=0.0, duration_ms=1)


def make_provider():
    import os

    return ScriptedFake(unavailable=os.environ.get("KB_FAKE_UNAVAILABLE") or None)
