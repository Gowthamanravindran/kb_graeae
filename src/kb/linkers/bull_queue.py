"""Link Bull queue producers (`xQueue.add(topic, ...)`) to consumers (`xQueue.process(topic, ...)`).

Topic key = last segment of the first argument (`TaskTopics.TASK_REPEAT` -> `TASK_REPEAT`).
Consumers registered with a variable topic cannot be matched and are recorded as unresolved;
consumers with an inline handler or a concurrency number are recorded as `<all-jobs>`.
A consumer is the job's handler function when it is passed by name (`q.process(TOPIC, userSignUpJob)`): the
function of that name declared nearest to the registering file. Projects often register every job in one
file; pointing at that file would make it look like one place handles all jobs. Inline handlers stay with
the registering file.
Only identifiers and string/member keys are ever stored, never argument source text.
"""

from __future__ import annotations

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, register_linker


ALL_JOBS = "<all-jobs>"


def _is_queue(receiver: str) -> bool:
    return receiver.split(".")[-1].lower().endswith("queue")


@register_linker
class BullQueueLinker:
    type = "bull-queue"

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        repo = ctx.config.in_
        root = ctx.checkouts[repo].root
        g = ctx.graph
        producers: dict[str, set[str]] = {}
        consumers: dict[str, set[tuple[str, str]]] = {}  # topic -> {(registering file, handler name or "")}
        defs: dict[str, list[tuple[str, int]]] = {}  # top-level name -> [(file, line)]
        dynamic: list[tuple[str, str]] = []
        first_seen: dict[str, tuple[str, int]] = {}

        for path in ts.iter_source_files(root, list(ctx.ignore)):
            tree = ts.parse(path)
            if tree is None:
                continue
            rel = path.relative_to(root).as_posix()
            for name, line in ts.top_level_names(tree):
                defs.setdefault(name, []).append((rel, line))
            for method, bucket in (("add", producers), ("process", consumers)):
                for c in ts.calls(tree, method):
                    if not _is_queue(c.receiver):
                        continue
                    if c.first_arg_kind in ("string", "member"):
                        key = c.first_arg.split(".")[-1]
                        if method == "process":
                            handler = c.last_arg.split(".")[-1] if c.n_args >= 2 and c.last_arg else ""
                            consumers.setdefault(key, set()).add((rel, handler))
                        else:
                            bucket.setdefault(key, set()).add(rel)
                        first_seen.setdefault(key, (rel, c.line))
                    elif method == "process":
                        # identifier = topic held in a variable; anything else (inline handler,
                        # concurrency number) means "all jobs on this queue". Never store the text.
                        dynamic.append((rel, c.first_arg if c.first_arg_kind == "identifier" else ALL_JOBS))

        edges: list[CodeEdge] = []

        def topic(key: str) -> str:
            rel, line = first_seen[key]
            return ensure_node(g, CodeNode(id=f"{repo}:queue_topic:{key}", repo=repo, kind="queue_topic", name=key,
                                           file=rel, line=line))

        def handler_node(rel: str, handler: str) -> str:
            found = [d for d in defs.get(handler, ()) if d[0] != rel] if handler else []
            if not found:
                return ensure_file_node(g, repo, rel)

            def closeness(d):  # shared leading folders with the registering file, then the shorter path
                a, b = d[0].split("/")[:-1], rel.split("/")[:-1]
                shared = next((i for i, (x, y) in enumerate(zip(a, b)) if x != y), min(len(a), len(b)))
                return (-shared, len(d[0]), d[0])
            hrel, line = min(found, key=closeness)
            ensure_file_node(g, repo, hrel)
            return ensure_node(g, CodeNode(id=f"{repo}:{hrel}#{handler}", repo=repo, kind="function", name=handler,
                                           file=hrel, line=line))

        for key in sorted(set(producers) | set(consumers)):
            tid = topic(key)
            for rel in sorted(producers.get(key, ())):
                edges.append(CodeEdge(src=ensure_file_node(g, repo, rel), dst=tid, kind="enqueues", origin="linker", label=key))
            cons = sorted(consumers.get(key, ()))
            for rel, handler in cons:
                edges.append(CodeEdge(src=tid, dst=handler_node(rel, handler), kind="consumed_by", origin="linker", label=key))
            if not cons:
                edges.append(CodeEdge(src=tid, dst=f"unresolved:queue:{key}", kind="consumed_by", origin="linker",
                                      resolved=False, label=key))
        for rel, ident in dynamic:
            edges.append(CodeEdge(src=ensure_file_node(g, repo, rel), dst=f"unresolved:queue:{ident}", kind="consumed_by",
                                  origin="linker", resolved=False, label=ident))
        return edges
