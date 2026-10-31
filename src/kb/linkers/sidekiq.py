"""Link Ruby background jobs: Sidekiq (`perform_async` / `perform_in` / `perform_at` / `perform_bulk`,
`Sidekiq::Client.push('class' => …)`) and ActiveJob (`perform_later`), also through `.set(…)`, to the job class's
`perform`, across every repo in `in` (a job queued by one app and run by a worker service links up).

Job classes are those that include `Sidekiq::Job` / `Sidekiq::Worker`, inherit from a `…Job` class
(`ApplicationJob`, `ActiveJob::Base`), or are named `…Job` / `…Worker`, and define `perform`. A job is keyed by
its class name without modules; a class in the caller's repo wins over one elsewhere. `perform_now` runs inline,
so it is not a queued job. Edges are the Bull linker's: caller file -enqueues-> `<repo>:queue_topic:<Job>`
-consumed_by-> `perform`.
"""

from __future__ import annotations

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import rb
from kb.linkers.base import LinkContext, SymbolIndex, ensure_file_node, ensure_node, method_node, register_linker

ENQUEUE = {"perform_async", "perform_in", "perform_at", "perform_later", "perform_bulk"}


def _is_job(k: rb.RbClass) -> bool:
    if "perform" not in k.methods:
        return False
    includes = {rb.const_path(a) for c in k.calls if c.name == "include" for a in c.args}
    return bool(includes & {"Sidekiq::Job", "Sidekiq::Worker"}) or k.superclass.endswith("Job") or \
        k.short.endswith(("Job", "Worker"))


def _job_name(c: rb.Call) -> str:
    """The job class a call enqueues: `X.perform_async`, `X.set(…).perform_later`, `Sidekiq::Client.push(…)`."""
    if c.name in ENQUEUE:
        r = c.recv_node
        while r is not None and r.type == "call" and (m := r.child_by_field_name("method")) is not None \
                and rb._text(m) in ("set", "delay"):
            r = r.child_by_field_name("receiver")
        return rb.const_path(r).rsplit("::", 1)[-1] if r is not None else ""
    if c.name in ("push", "push_bulk") and c.receiver == "Sidekiq::Client":
        v = c.kwargs.get("class")
        if v is None:
            v = next((val for k, val in c.rockets if rb.literal(k) == "class"), None)
        return (rb.literal(v) or rb.const_path(v)).rsplit("::", 1)[-1] if v is not None else ""
    return ""


@register_linker
class SidekiqLinker:
    type = "sidekiq"

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        g = ctx.graph
        index = SymbolIndex(g)
        jobs: dict[str, list[tuple[tuple[str, str], rb.RbClass]]] = {}
        calls: list[tuple[tuple[str, str], str, int]] = []
        for repo in ctx.config.ins():
            root = ctx.checkouts[repo].root
            for path in rb.iter_source_files(root, list(ctx.ignore)):
                tree = rb.parse(path)
                if tree is None:
                    continue
                rel = (repo, path.relative_to(root).as_posix())
                for k in rb.classes(tree):
                    if _is_job(k):
                        jobs.setdefault(k.short, []).append((rel, k))
                for c in rb.calls(tree):
                    if (name := _job_name(c)):
                        calls.append((rel, name, c.line))
        edges: list[CodeEdge] = []
        topics: dict[str, str] = {}
        done: set[tuple[str, str]] = set()
        for rel, name, line in calls:
            defs = jobs.get(name, [])
            mine = [d for d in defs if d[0][0] == rel[0]] or defs
            if name not in topics:
                (trepo, tfile), tline = (mine[0][0], mine[0][1].line) if mine else (rel, line)
                topics[name] = ensure_node(g, CodeNode(id=f"{trepo}:queue_topic:{name}", repo=trepo, kind="queue_topic",
                                                       name=name, file=tfile, line=tline))
                for f, k in mine:
                    dst = method_node(g, f, k.name, "perform", k.methods["perform"], index=index)
                    edges.append(CodeEdge(src=topics[name], dst=dst, kind="consumed_by", origin="linker", label=name))
                if not mine:
                    edges.append(CodeEdge(src=topics[name], dst=f"unresolved:queue:{name}", kind="consumed_by",
                                          origin="linker", resolved=False, label=name))
            src = ensure_file_node(g, *rel)
            if (src, name) not in done:
                done.add((src, name))
                edges.append(CodeEdge(src=src, dst=topics[name], kind="enqueues", origin="linker", label=name))
        return edges
