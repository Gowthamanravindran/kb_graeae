"""A frontend + backend repo pair with real git history: base branches master (FE) / develop (BE) and a PR
branch `feature/repeat-end` in both. The PR adds a permission check, a validation, changes a branch condition,
removes the notify behaviour, and sends a new GraphQL input field. Used by the stage 5a tests."""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

FIX = Path(__file__).parents[1] / "fixtures"
HEAD = "feature/repeat-end"
RESOLVERS = "src/module/tasks/task/resolvers.ts"
CREATION = "src/tasks/TaskCreation.tsx"

BASE_RESOLVE = """    const task = await TaskModel.create(args.input);
    if (task.repeat) {
      scheduleRepeat(task);
    }
    if (task.notify) {
      notifyAssignee(task);
    }
    return task;"""

HEAD_RESOLVE = """    if (!context.user.isAdmin) {
      throw new Error('Only admins can add tasks');
    }
    if (args.input.repeatEndsAt && args.input.repeatEndsAt < args.input.startsAt) {
      throw new Error('End date must be after the start date');
    }
    const task = await TaskModel.create(args.input);
    if (task.repeat === 'weekly') {
      scheduleRepeat(task, 7);
    }
    return task;"""


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@dataclass
class PrFixture:
    root: Path
    cfg_path: Path
    web: Path
    be: Path
    base: dict = field(default_factory=lambda: {"mini-fe": "master", "mini-be": "develop"})
    head_branch: str = HEAD

    @property
    def head(self) -> dict:
        return {"mini-fe": HEAD, "mini-be": HEAD}


def _init(d: Path, branch: str) -> None:
    git(d, "init", "-q", "-b", branch)
    git(d, "config", "user.email", "t@t")
    git(d, "config", "user.name", "t")


def _commit(d: Path, msg: str) -> None:
    git(d, "add", "-A")
    git(d, "commit", "-qm", msg)


def make_pr_fixture(tmp: Path) -> PrFixture:
    tmp = Path(tmp)
    web, be = tmp / "repos" / "mini-fe", tmp / "repos" / "mini-be"
    shutil.copytree(FIX / "mini-fe", web)
    shutil.copytree(FIX / "mini-be", be)

    r = be / RESOLVERS
    src = r.read_text()
    old_body = src[src.index("    const task = await TaskModel.create"):src.index("    return task;") + len("    return task;")]
    src = src.replace(old_body, BASE_RESOLVE)
    src = src.replace("export { AddTask };",
                      "function notifyAssignee(task) {\n  TaskQueue.add(TaskTopics.TASK_REPEAT, { notify: task._id });\n}\n\n"
                      "export { AddTask };")
    r.write_text(src)
    _init(web, "master")
    _commit(web, "base")
    _init(be, "develop")
    _commit(be, "base")

    git(web, "checkout", "-q", "-b", HEAD)
    c = web / CREATION
    c.write_text(c.read_text().replace("variables: { input: {} }", "variables: { input: { repeatEndsAt: null } }"))
    _commit(web, "send repeatEndsAt")
    git(web, "checkout", "-q", "master")

    git(be, "checkout", "-q", "-b", HEAD)
    s = r.read_text().replace(BASE_RESOLVE, HEAD_RESOLVE)
    s = s.replace("resolve: async ({ args }) =>", "resolve: async ({ args, context }) =>")
    s = s.replace("function scheduleRepeat(task) {\n  TaskQueue.add(TaskTopics.TASK_REPEAT, { taskId: task._id }, { delay: 1000 });",
                  "function scheduleRepeat(task, days) {\n  TaskQueue.add(TaskTopics.TASK_REPEAT, { taskId: task._id }, { delay: days * 86400000 });")
    s = s.replace("function notifyAssignee(task) {\n  TaskQueue.add(TaskTopics.TASK_REPEAT, { notify: task._id });\n}\n\n", "")
    r.write_text(s)
    _commit(be, "repeat end date, admin only")
    git(be, "checkout", "-q", "develop")

    cfg = tmp / "kb.yaml"
    cfg.write_text(f"""project: mini
repos:
  - {{name: mini-fe, role: frontend, local_path: {web}, default_branch: master}}
  - {{name: mini-be, role: backend, local_path: {be}, default_branch: develop}}
linkers:
  - {{type: graphql, from: mini-fe, to: mini-be, options: {{backend_style: graphql-compose}}}}
  - {{type: bull-queue, in: mini-be}}
  - {{type: react-router, in: mini-fe}}
""")
    return PrFixture(root=tmp, cfg_path=cfg, web=web, be=be)
