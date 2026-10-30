"""Link services that talk through a message broker: Kafka, Amazon SQS and RabbitMQ.

Each linker scans every repo in `in` for places that send a message and places that receive one, keyed by the
topic or queue name, and draws `publishes` (sending file -> topic) and `consumed_by` (topic -> handler) edges.
A topic node is `<repo>:msg:<broker>:<name>`, kind `message_topic`.

Names are matched after resolving constants across all `in` repos, so `producer.send({topic: 'order.created'})` in
one service meets `consumer.subscribe({topic: Topics.ORDER_CREATED})` in another when `Topics.ORDER_CREATED` is
`'order.created'`. `process.env.X` is keyed by the variable's name `X`, so two services reading the same setting
meet. A name held in a variable that can't be resolved is recorded as unresolved, by the variable's name.

Recognised:
- kafka: kafkajs `producer.send({topic})` / `sendBatch({topicMessages: [{topic}]})`, `consumer.subscribe({topic |
  topics})` with the handler given to `consumer.run({eachMessage | eachBatch})`; NestJS `@EventPattern('t')` /
  `@MessagePattern('t')` handlers and `client.emit('t', …)` / `client.send('t', …)`.
- sqs: AWS SDK v3 `new SendMessageCommand({QueueUrl})` (and Batch) / `new ReceiveMessageCommand({QueueUrl})`,
  v2 `sqs.sendMessage({QueueUrl})` / `receiveMessage`, and sqs-consumer / sqs-producer `Consumer.create({queueUrl,
  handleMessage})` / `Producer.create({queueUrl})`. A queue URL is keyed by its last path part (the queue name).
- rabbitmq: amqplib `channel.sendToQueue(q)`, `channel.publish(exchange, routingKey)` (followed through
  `bindQueue(q, exchange, pattern)` to the queues it reaches, with `*`/`#` patterns), `channel.consume(q, handler)`;
  NestJS patterns as for Kafka.
Only names are ever stored, never argument source text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from kb.graph.model import CodeEdge, CodeNode
from kb.linkers import ts
from kb.linkers.base import LinkContext, ensure_file_node, ensure_node, handler_node, method_node, register_linker

File = tuple[str, str]  # (repo, path)


@dataclass
class Hit:
    send: bool
    key: str | None          # topic / queue name; None when it can't be worked out
    ident: str               # what to call it when unresolved (a variable's name, never source text)
    file: File
    line: int
    handler: str = ""        # a handler function's name
    method: tuple[str, str, int] | None = None   # (class, method, line) for a decorated handler


@dataclass
class Scan:
    hits: list[Hit] = field(default_factory=list)
    defs: dict = field(default_factory=dict)
    strings: dict[str, set[str]] = field(default_factory=dict)  # name -> string values, across all `in` repos


class Keys:
    def __init__(self, strings: dict[str, set[str]], local: dict[str, str]):
        self.strings, self.local = strings, local

    def __call__(self, n) -> tuple[str | None, str]:
        """(name, ident). A literal is its own name; a constant resolves to its string; `process.env.X` is X."""
        if n is None:
            return None, "?"
        lit = ts.literal(n)
        if lit is not None:
            # a plain string may hold a real `*` (an AMQP pattern); only a template's `${…}` is unknown
            return (lit, lit) if n.type == "string" or "*" not in lit else (None, "template")
        nm = ts.name_of(n)
        if not nm:
            return None, "?"
        if ts.is_env(n):
            return nm, nm
        if nm in self.local:
            return self.local[nm], nm
        vals = self.strings.get(nm, set())
        if len(vals) == 1:
            return next(iter(vals)), nm
        if n.type == "member_expression":
            return nm, nm   # `Topics.ORDER_CREATED` whose value lives outside these repos: match on the name
        return None, nm


def _queue_name(url: str | None) -> str | None:
    if url is None:
        return None
    last = url.rstrip("/").rsplit("/", 1)[-1]
    return last if last and "*" not in last else None


NEST_PATTERNS = {"EventPattern", "MessagePattern"}


class BrokerLinker:
    type = ""
    broker = ""

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        files = []
        scan = Scan()
        for repo in ctx.config.ins():
            root = ctx.checkouts[repo].root
            for path in ts.iter_source_files(root, list(ctx.ignore)):
                tree = ts.parse(path)
                if tree is None:
                    continue
                rel = (repo, path.relative_to(root).as_posix())
                files.append((rel, tree))
                for name, line in ts.top_level_names(tree):
                    scan.defs.setdefault(name, []).append((rel, line))
                for k, v in ts.string_values(tree).items():
                    scan.strings.setdefault(k, set()).add(v)
        for rel, tree in files:
            self.scan(tree, rel, Keys(scan.strings, ts.string_constants(tree)), scan.hits)
        return self.edges(ctx, scan)

    def scan(self, tree, rel: File, keys: Keys, hits: list[Hit]) -> None:  # pragma: no cover - per broker
        raise NotImplementedError

    def targets(self, hit: Hit, scan: Scan) -> list[str]:
        """The topic/queue names a send reaches (RabbitMQ follows exchange bindings)."""
        return [hit.key] if hit.key else []

    def nest(self, tree, rel: File, keys: Keys, hits: list[Hit]) -> None:
        for m in ts.decorated_methods(tree):
            for dec in NEST_PATTERNS & set(m.decorators):
                key, ident = keys(m.decorators[dec])
                hits.append(Hit(False, key, ident, rel, m.line, method=(m.cls, m.method, m.line)))
        for c in ts.all_calls(tree):
            last = c.receiver.split(".")[-1].lower()
            if c.name in ("emit", "send") and c.args and ("client" in last or self.broker in last) \
                    and c.args[0].type in ("string", "member_expression"):
                key, ident = keys(c.args[0])
                hits.append(Hit(True, key, ident, rel, c.line))

    def edges(self, ctx: LinkContext, scan: Scan) -> list[CodeEdge]:
        g = ctx.graph
        first: dict[str, tuple[File, int]] = {}
        sends: dict[str, set[File]] = {}
        recvs: dict[str, list[Hit]] = {}
        edges: list[CodeEdge] = []
        for h in scan.hits:
            if h.send:
                names = self.targets(h, scan)
                for k in names:
                    sends.setdefault(k, set()).add(h.file)
                    first.setdefault(k, (h.file, h.line))
                if not names:
                    edges.append(CodeEdge(src=ensure_file_node(g, *h.file), dst=f"unresolved:message:{h.ident}",
                                          kind="publishes", origin="linker", resolved=False, label=h.ident))
            elif h.key:
                recvs.setdefault(h.key, []).append(h)
                first.setdefault(h.key, (h.file, h.line))
            else:
                edges.append(CodeEdge(src=ensure_file_node(g, *h.file), dst=f"unresolved:message:{h.ident}",
                                      kind="consumed_by", origin="linker", resolved=False, label=h.ident))

        def topic(key: str) -> str:
            (repo, rel), line = first[key]
            return ensure_node(g, CodeNode(id=f"{repo}:msg:{self.broker}:{key}", repo=repo, kind="message_topic",
                                           name=key, file=rel, line=line))

        for key in sorted(set(sends) | set(recvs)):
            tid = topic(key)
            for f in sorted(sends.get(key, ())):
                edges.append(CodeEdge(src=ensure_file_node(g, *f), dst=tid, kind="publishes", origin="linker", label=key))
            seen = set()
            for h in recvs.get(key, []):
                dst = method_node(g, h.file, *h.method) if h.method else handler_node(g, scan.defs, h.file, h.handler)
                if dst not in seen:
                    seen.add(dst)
                    edges.append(CodeEdge(src=tid, dst=dst, kind="consumed_by", origin="linker", label=key))
            if not recvs.get(key):
                edges.append(CodeEdge(src=tid, dst=f"unresolved:message:{key}", kind="consumed_by", origin="linker",
                                      resolved=False, label=key))
        return edges


def _handler_name(n) -> str:
    return ts.name_of(n) if n is not None and n.type in ("identifier", "member_expression") else ""


@register_linker
class KafkaLinker(BrokerLinker):
    type = "kafka"
    broker = "kafka"

    def scan(self, tree, rel, keys, hits):
        calls = ts.all_calls(tree)
        runs = []  # handlers given to consumer.run in this file
        for c in calls:
            if c.name == "run" and "consumer" in c.receiver.lower() and c.args:
                p = ts.props(c.args[0])
                runs += [h for k in ("eachMessage", "eachBatch") if (h := _handler_name(p.get(k)))]
        handler = runs[0] if len(set(runs)) == 1 else ""
        for c in calls:
            low = c.receiver.split(".")[-1].lower()
            if c.name in ("send", "sendBatch") and "producer" in low and c.args:
                p = ts.props(c.args[0])
                objs = [p] if "topic" in p else [ts.props(o) for o in (p.get("topicMessages").named_children
                                                                       if p.get("topicMessages") is not None else [])]
                for o in objs:
                    if "topic" in o:
                        key, ident = keys(o["topic"])
                        hits.append(Hit(True, key, ident, rel, c.line))
            elif c.name == "subscribe" and "consumer" in low and c.args:
                p = ts.props(c.args[0])
                vals = [p["topic"]] if "topic" in p else (p["topics"].named_children if "topics" in p
                                                          and p["topics"].type == "array" else [])
                for v in vals:
                    if v.type == "regex":
                        continue
                    key, ident = keys(v)
                    hits.append(Hit(False, key, ident, rel, c.line, handler=handler))
        self.nest(tree, rel, keys, hits)


SQS_SEND = {"SendMessageCommand", "SendMessageBatchCommand", "sendMessage", "sendMessageBatch"}
SQS_RECV = {"ReceiveMessageCommand", "receiveMessage"}


@register_linker
class SqsLinker(BrokerLinker):
    type = "sqs"
    broker = "sqs"

    def _key(self, keys, n):
        lit = ts.literal(n)
        if lit is not None:   # a URL, possibly built from a template: `${base}/orders` is the queue `orders`
            return (_queue_name(lit) if "/" in lit else (lit if "*" not in lit else None)), "template"
        key, ident = keys(n)
        return (_queue_name(key) if key and "/" in key else key), ident

    def scan(self, tree, rel, keys, hits):
        for c in ts.all_calls(tree):
            if not c.args:
                continue
            p = ts.props(c.args[0])
            if c.name in SQS_SEND | SQS_RECV and "QueueUrl" in p:
                key, ident = self._key(keys, p["QueueUrl"])
                hits.append(Hit(c.name in SQS_SEND, key, ident, rel, c.line))
            elif c.name == "create" and c.receiver in ("Consumer", "Producer") and "queueUrl" in p:
                key, ident = self._key(keys, p["queueUrl"])
                hits.append(Hit(c.receiver == "Producer", key, ident, rel, c.line,
                                handler=_handler_name(p.get("handleMessage") or p.get("handleMessageBatch"))))


_CHANNEL = re.compile(r"(^ch$|chan)")


def _binding_matches(pattern: str, key: str) -> bool:
    """AMQP topic patterns: `*` is exactly one word, `#` zero or more; words are separated by dots."""
    p, k = pattern.split("."), key.split(".")

    def m(i: int, j: int) -> bool:
        if i == len(p):
            return j == len(k)
        if p[i] == "#":
            return any(m(i + 1, x) for x in range(j, len(k) + 1))
        return j < len(k) and p[i] in ("*", k[j]) and m(i + 1, j + 1)
    return m(0, 0)


@register_linker
class RabbitMqLinker(BrokerLinker):
    type = "rabbitmq"
    broker = "rabbitmq"

    def run(self, ctx):
        self.bindings: dict[str, list[tuple[str, str]]] = {}  # exchange -> [(pattern, queue)]
        return super().run(ctx)

    def scan(self, tree, rel, keys, hits):
        for c in ts.all_calls(tree):
            if not _CHANNEL.search(c.receiver.split(".")[-1].lower()) or not c.args:
                continue
            if c.name == "sendToQueue":
                key, ident = keys(c.args[0])
                hits.append(Hit(True, key, ident, rel, c.line))
            elif c.name == "publish" and len(c.args) >= 2:
                ex, _ = keys(c.args[0])
                rk, ident = keys(c.args[1])
                hits.append(Hit(True, f"{ex}\x00{rk}" if ex is not None and rk is not None else None, ident, rel, c.line))
            elif c.name == "bindQueue" and len(c.args) >= 2:
                q, _ = keys(c.args[0])
                ex, _ = keys(c.args[1])
                pat, _ = keys(c.args[2]) if len(c.args) > 2 else ("", "")
                if q and ex is not None:
                    self.bindings.setdefault(ex, []).append((pat or "", q))
            elif c.name == "consume":
                key, ident = keys(c.args[0])
                hits.append(Hit(False, key, ident, rel, c.line,
                                handler=_handler_name(c.args[1]) if len(c.args) > 1 else ""))
        self.nest(tree, rel, keys, hits)

    def targets(self, hit, scan):
        if not hit.key or "\x00" not in hit.key:
            return super().targets(hit, scan)
        ex, rk = hit.key.split("\x00", 1)
        if ex == "":   # the default exchange routes by queue name
            return [rk]
        qs = sorted({q for pat, q in self.bindings.get(ex, []) if rk == "" or pat == "" or _binding_matches(pat, rk)})
        return qs or [f"{ex}/{rk}" if rk else ex]
