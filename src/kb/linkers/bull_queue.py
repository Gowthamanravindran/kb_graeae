"""Link Bull queue producers (`xQueue.add(topic, ...)`) to consumers (`xQueue.process(topic, ...)`).

Topic key = last segment of the first argument (`TaskTopics.TASK_REPEAT` -> `TASK_REPEAT`).
Consumers registered with a variable topic cannot be matched and are recorded as unresolved.
"""

from __future__ import annotations

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, register_linker


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
        consumers: dict[str, set[str]] = {}
        dynamic: list[tuple[str, str]] = []
        first_seen: dict[str, tuple[str, int]] = {}

        for path in ts.iter_source_files(root, list(ctx.ignore)):
            tree = ts.parse(path)
            if tree is None:
                continue
            rel = path.relative_to(root).as_posix()
            for method, bucket in (("add", producers), ("process", consumers)):
                for c in ts.calls(tree, method):
                    if not _is_queue(c.receiver):
                        continue
                    if c.first_arg_kind in ("string", "member"):
                        key = c.first_arg.split(".")[-1]
                        bucket.setdefault(key, set()).add(rel)
                        first_seen.setdefault(key, (rel, c.line))
                    elif method == "process":
                        dynamic.append((rel, c.first_arg))

        edges: list[CodeEdge] = []

        def topic(key: str) -> str:
            rel, line = first_seen[key]
            return ensure_node(g, CodeNode(id=f"{repo}:queue_topic:{key}", repo=repo, kind="queue_topic", name=key,
                                           file=rel, line=line))

        for key in sorted(set(producers) | set(consumers)):
            tid = topic(key)
            for rel in sorted(producers.get(key, ())):
                edges.append(CodeEdge(src=ensure_file_node(g, repo, rel), dst=tid, kind="enqueues", origin="linker", label=key))
            cons = sorted(consumers.get(key, ()))
            for rel in cons:
                edges.append(CodeEdge(src=tid, dst=ensure_file_node(g, repo, rel), kind="consumed_by", origin="linker", label=key))
            if not cons:
                edges.append(CodeEdge(src=tid, dst=f"unresolved:queue:{key}", kind="consumed_by", origin="linker",
                                      resolved=False, label=key))
        for rel, ident in dynamic:
            edges.append(CodeEdge(src=ensure_file_node(g, repo, rel), dst=f"unresolved:queue:{ident}", kind="consumed_by",
                                  origin="linker", resolved=False, label=ident))
        return edges
