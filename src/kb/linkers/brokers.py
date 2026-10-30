"""Link services that talk through a message broker: Kafka, Amazon SQS and RabbitMQ.

Each linker scans every repo in `in` for places that send a message and places that receive one, keyed by the
topic or queue name, and draws `publishes` (sending file -> topic) and `consumed_by` (topic -> handler) edges.
A topic node is `<repo>:msg:<broker>:<name>`, kind `message_topic`.

Names are matched after resolving declared constants across all `in` repos, so `producer.send({topic:
'order.created'})` in one service meets `consumer.subscribe({topic: Topics.ORDER_CREATED})` in another when
`Topics` is an object or enum declaring `ORDER_CREATED = 'order.created'`. `process.env.X` is keyed by the setting's
name `X`, so two services reading the same setting meet. A name held in a parameter, `this.x` or a config object
is recorded as unresolved, by its name; it is never matched on the name alone.

Recognised:
- kafka: kafkajs `producer.send({topic})` / `sendBatch({topicMessages: [{topic}]})`, `consumer.subscribe({topic |
  topics})` with the handler given to `consumer.run({eachMessage | eachBatch})`; NestJS `@EventPattern('t')` /
  `@MessagePattern('t')` handlers and `client.emit('t', …)` / `client.send('t', …)`.
- sqs: AWS SDK v3 `new SendMessageCommand({QueueUrl})` (and Batch) / `new ReceiveMessageCommand({QueueUrl})`,
  v2 `sqs.sendMessage({QueueUrl})` / `receiveMessage`, and sqs-consumer / sqs-producer `Consumer.create({queueUrl,
  handleMessage})` / `Producer.create({queueUrl})`. A queue URL is keyed by its last path part (the queue name).
- rabbitmq: amqplib `channel.sendToQueue(q)`, `channel.publish(exchange, routingKey)` (followed through
  `bindQueue(q, exchange, pattern)` to the queues it reaches, by the exchange type declared with `assertExchange`
  (fanout: every bound queue; direct: equal keys; topic: `*`/`#` patterns), `channel.consume(q, handler)`;
  NestJS patterns as for Kafka.
NestJS `@EventPattern` / `@MessagePattern` handlers are transport-neutral, so each broker takes them only in repos
whose package.json uses that broker. `a || 'x'` and `a ?? 'x'` use the setting, else the default; a NestJS object
pattern `{cmd: 'sum'}` is keyed `cmd=sum`.
Only names are ever stored, never argument source text.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

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
    qualified: dict[str, set[str]] = field(default_factory=dict)  # `Obj.KEY` -> values, across all `in` repos


_CONST = re.compile(r"^[A-Z][A-Za-z0-9_]*$")
_UPPER = re.compile(r"^[A-Z][A-Z0-9_]*$")


class Keys:
    """Turns a topic/queue argument into a name, only through what is declared: a literal; a `const` in the same
    file; `Obj.KEY` on a declared object or enum (looked up across all `in` repos); `process.env.X`. Parameters,
    `this.x`, `config.topic` and server-named queues (`q.queue`) stay unresolved, so they never meet a topic
    that merely shares their name."""

    def __init__(self, qualified: dict[str, set[str]], local: dict[str, str]):
        self.qualified, self.local = qualified, local

    def __call__(self, n) -> tuple[str | None, str]:
        if n is None:
            return None, "?"
        while n.type == "parenthesized_expression" and n.named_children:
            n = n.named_children[0]
        if n.type == "binary_expression" and n.child_by_field_name("operator") is not None \
                and n.child_by_field_name("operator").text in (b"||", b"??"):
            key, ident = self(n.child_by_field_name("left"))   # `process.env.T || 'orders'`: the setting, else the default
            return (key, ident) if key is not None else self(n.child_by_field_name("right"))
        if n.type == "object":   # NestJS `{ cmd: 'sum' }` patterns: every value must be a literal
            vals = {k: ts.literal(v) for k, v in ts.props(n).items()}
            if vals and all(v is not None and "*" not in v for v in vals.values()):
                key = ",".join(f"{k}={v}" for k, v in sorted(vals.items()))
                return key, key
            return None, "pattern"
        lit = ts.literal(n)
        if lit is not None:
            # a plain string may hold a real `*` (an AMQP pattern); only a template's `${…}` is unknown
            return (lit, lit) if n.type == "string" or "*" not in lit else (None, "template")
        nm = ts.name_of(n)
        if not nm:
            return None, "?"
        if n.type in ("identifier", "shorthand_property_identifier"):
            return (self.local[nm], nm) if nm in self.local else (None, nm)
        if ts.is_env(n):
            return nm, nm
        obj = n.child_by_field_name("object") if n.type == "member_expression" else None
        if obj is None or obj.type != "identifier":
            return None, nm
        q = f"{ts.name_of(obj)}.{nm}"
        vals = self.qualified.get(q, set())
        if len(vals) == 1:
            return next(iter(vals)), q
        if _CONST.match(ts.name_of(obj)) and _UPPER.match(nm):
            return q, q   # `Topics.ORDER_CREATED` declared outside these repos: both sides must name it the same
        return None, nm


def _queue_name(url: str | None) -> str | None:
    if url is None:
        return None
    last = url.rstrip("/").rsplit("/", 1)[-1]
    return last if last and "*" not in last else None


NEST_PATTERNS = {"EventPattern", "MessagePattern"}


def _deps(root: Path) -> set[str] | None:
    """The repo's package.json dependencies; None when it has none to read (then nothing is ruled out)."""
    try:
        d = json.loads((root / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return {k for sect in ("dependencies", "devDependencies", "peerDependencies") if isinstance(d.get(sect), dict)
            for k in d[sect]}


class BrokerLinker:
    type = ""
    broker = ""
    libs: set[str] = set()   # packages that mean a repo talks to this broker (for transport-neutral NestJS patterns)

    def run(self, ctx: LinkContext) -> list[CodeEdge]:
        files = []
        scan = Scan()
        self.uses: dict[str, bool] = {}
        for repo in ctx.config.ins():
            root = ctx.checkouts[repo].root
            deps = _deps(Path(root))
            self.uses[repo] = deps is None or bool(deps & self.libs)
            for path in ts.iter_source_files(root, list(ctx.ignore)):
                tree = ts.parse(path)
                if tree is None:
                    continue
                rel = (repo, path.relative_to(root).as_posix())
                files.append((rel, tree))
                for name, line in ts.top_level_names(tree):
                    scan.defs.setdefault(name, []).append((rel, line))
                for k, v in ts.qualified_strings(tree).items():
                    scan.qualified.setdefault(k, set()).add(v)
        for rel, tree in files:
            self.scan(tree, rel, Keys(scan.qualified, ts.string_constants(tree)), scan.hits)
        return self.edges(ctx, scan)

    def scan(self, tree, rel: File, keys: Keys, hits: list[Hit]) -> None:  # pragma: no cover - per broker
        raise NotImplementedError

    def targets(self, hit: Hit, scan: Scan) -> list[str]:
        """The topic/queue names a send reaches (RabbitMQ follows exchange bindings)."""
        return [hit.key] if hit.key else []

    def nest(self, tree, rel: File, keys: Keys, hits: list[Hit]) -> None:
        """NestJS microservice patterns are transport-neutral: they count for this broker only in a repo that uses
        it. `client.emit` only in a file importing @nestjs/microservices (socket.io has `client.emit` too)."""
        if not self.uses.get(rel[0], True):
            return
        for m in ts.decorated_methods(tree):
            for dec in NEST_PATTERNS & set(m.decorators):
                key, ident = keys(m.decorators[dec])
                hits.append(Hit(False, key, ident, rel, m.line, method=(m.cls, m.method, m.line)))
        if b"@nestjs/microservices" not in (tree.root_node.text or b""):
            return
        for c in ts.all_calls(tree):
            last = c.receiver.split(".")[-1].lower()
            if c.name in ("emit", "send") and c.args and ("client" in last or self.broker in last) \
                    and c.args[0].type in ("string", "member_expression", "object"):
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
    libs = {"kafkajs", "node-rdkafka", "@confluentinc/kafka-javascript"}

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
        objects = ts.local_objects(tree)
        for c in ts.all_calls(tree):
            if not c.args:
                continue
            a = c.args[0]
            # v2 SDK: `const params = { QueueUrl, … }; sqs.sendMessage(params)`
            p = ts.props(objects.get(ts.name_of(a)) if a.type == "identifier" else a)
            if c.name in SQS_SEND | SQS_RECV and "QueueUrl" in p:
                key, ident = self._key(keys, p["QueueUrl"])
                hits.append(Hit(c.name in SQS_SEND, key, ident, rel, c.line))
            elif c.name == "create" and c.receiver in ("Consumer", "Producer") and "queueUrl" in p:
                key, ident = self._key(keys, p["queueUrl"])
                hits.append(Hit(c.receiver == "Producer", key, ident, rel, c.line,
                                handler=_handler_name(p.get("handleMessage") or p.get("handleMessageBatch"))))


_CHANNEL = re.compile(r"(^ch$|^chan$|^channel|channel$|channelwrapper$)")   # not `merchant`


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
    libs = {"amqplib", "amqp-connection-manager", "@golevelup/nestjs-rabbitmq", "rascal"}

    def run(self, ctx):
        self.bindings: dict[str, list[tuple[str | None, str]]] = {}  # exchange -> [(pattern or None, queue)]
        self.kinds: dict[str, str] = {}   # exchange -> fanout | direct | topic | headers, from assertExchange
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
                pat = keys(c.args[2])[0] if len(c.args) > 2 else ""   # None: a pattern we can't read matches nothing
                if q and ex is not None:
                    self.bindings.setdefault(ex, []).append((pat, q))
            elif c.name == "assertExchange" and len(c.args) >= 2:
                ex, _ = keys(c.args[0])
                kind = ts.literal(c.args[1])
                if ex is not None and kind:
                    self.kinds[ex] = kind
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
        kind = self.kinds.get(ex) or ("fanout" if rk == "" else "topic")   # unknown: an empty key suggests fanout

        def reaches(pat: str | None) -> bool:
            if kind in ("fanout", "headers"):   # every bound queue (headers can't be evaluated statically)
                return True
            if pat is None:
                return False
            return pat == rk if kind == "direct" else _binding_matches(pat, rk) if pat else rk == ""
        qs = sorted({q for pat, q in self.bindings.get(ex, []) if reaches(pat)})
        return qs or [f"{ex}/{rk}" if rk else ex]
