"""REST and message-broker linkers (Kafka, SQS, RabbitMQ)."""

from pathlib import Path

from kb.linkers.base import get_linker
from kb.linkers.brokers import KafkaLinker, RabbitMqLinker, SqsLinker, _binding_matches
from kb.linkers.rest import RestLinker, score, segments

from linker_helpers import ctx, run


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


def linked(edges, g, kind):
    return sorted((e.src, g.node(e.dst).name if g.has_node(e.dst) else e.dst) for e in edges if e.kind == kind and e.resolved)


# ---------------------------------------------------------------- REST

def test_segments_and_score():
    assert segments("https://api.x.com/api/v1/users/${id}?full=1") == ["api", "v1", "users", "*"]
    assert segments("*/users/*/orders") == ["users", "*", "orders"]
    assert segments("/users/:id") == ["users", "*"] and segments("/users/{id}") == ["users", "*"]
    assert score(["users", "*"], ["api", "users", "*"]) == (1, 1)     # mounted under /api; the id meets :id
    assert score(["users", "me"], ["users", "*"]) == (1, 0)
    assert score(["users", "*"], ["orders", "*"]) is None
    assert score(["*"], ["users"]) is None                             # nothing fixed matched
    assert score(["api", "users"], ["users"]) is None                  # the route is longer than the call


def rest_repos(tmp_path):
    fe = write(tmp_path / "web", {
        "src/api.ts": "import axios from 'axios'\n"
                      "export const getUser = (id) => axios.get(`/api/users/${id}`)\n"
                      "export const saveUser = (u) => fetch('/api/users', { method: 'POST', body: JSON.stringify(u) })\n"
                      "export const del = (id) => apiClient.delete(`${BASE}/orders/${id}`)\n"
                      "export const missing = () => this.http.get('/api/nothing/here')\n"
                      "export const dyn = (url) => axios.get(url)\n"
                      "const m = new Map(); m.get('/api/users')\n",
    })
    be = write(tmp_path / "svc", {
        "src/app.ts": "import { getUserHandler } from './users/get'\n"
                      "const app = express()\napp.use('/api', router)\n"
                      "router.get('/users/:id', getUserHandler)\n"
                      "router.post('/users', async (req, res) => res.send(1))\n",
        "src/users/get.ts": "export async function getUserHandler(req, res) { res.json({}) }\n",
    })
    nest = write(tmp_path / "orders", {
        "src/orders.controller.ts": "@Controller('orders')\nexport class OrdersController {\n"
                                    "  @Delete(':id')\n  remove(@Param('id') id: string) {}\n"
                                    "  @Get()\n  list() {}\n}\n",
    })
    return {"web": fe, "svc": be, "orders": nest}


def test_rest_links_calls_to_express_and_nest_routes(tmp_path):
    roots = rest_repos(tmp_path)
    c = ctx({"type": "rest", "from": "web", "to": ["svc", "orders"]}, repos=tuple(roots), roots=roots)
    edges = run(get_linker("rest"), c)
    assert linked(edges, c.graph, "calls_http") == [
        ("web:src/api.ts", "DELETE /orders/:id"), ("web:src/api.ts", "GET /users/:id"), ("web:src/api.ts", "POST /users")]
    served = {c.graph.node(e.src).name: e.dst for e in edges if e.kind == "served_by"}
    assert served["GET /users/:id"] == "svc:src/users/get.ts#getUserHandler"      # the handler, not the router file
    assert served["POST /users"] == "svc:src/app.ts"                               # inline handler: the file
    assert served["DELETE /orders/:id"] == "orders:src/orders.controller.ts#OrdersController.remove"
    assert served["GET /orders"] == "orders:src/orders.controller.ts#OrdersController.list"
    un = [e.dst for e in edges if not e.resolved]
    assert un == ["unresolved:http:GET /api/nothing/here"]                         # dynamic URLs and Map.get skipped


def test_rest_prefers_the_route_matching_more_of_the_path(tmp_path):
    fe = write(tmp_path / "web", {"a.ts": "api.get('/admin/users/list')\n"})
    be = write(tmp_path / "svc", {"r.ts": "router.get('/users/list', a)\nrouter.get('/list', b)\nrouter.post('/admin/users/list', c)\n"})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    edges = run(RestLinker(), c)
    assert linked(edges, c.graph, "calls_http") == [("web:a.ts", "GET /users/list")]


def test_rest_route_chain_and_fastify(tmp_path):
    fe = write(tmp_path / "web", {"a.ts": "axios({ url: '/items', method: 'put' })\naxios('/health')\n"})
    be = write(tmp_path / "svc", {"r.ts": "router.route('/items').put(updateItem)\n"
                                          "fastify.route({ method: 'GET', url: '/health', handler: health })\n"})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    edges = run(RestLinker(), c)
    assert linked(edges, c.graph, "calls_http") == [("web:a.ts", "GET /health"), ("web:a.ts", "PUT /items")]


# ---------------------------------------------------------------- Kafka

def test_kafka_links_producer_to_consumer_across_services_through_constants(tmp_path):
    a = write(tmp_path / "orders", {
        "src/topics.ts": "export const Topics = { ORDER_CREATED: 'order.created' }\n",
        "src/publish.ts": "await producer.send({ topic: Topics.ORDER_CREATED, messages: [] })\n"
                          "await producer.sendBatch({ topicMessages: [{ topic: 'order.paid', messages: [] }] })\n"
                          "await producer.send({ topic: someTopic, messages: [] })\n",
    })
    b = write(tmp_path / "mail", {
        "src/consumer.ts": "await consumer.subscribe({ topics: ['order.created', 'order.paid'] })\n"
                           "await consumer.run({ eachMessage: handleOrder })\n",
        "src/handlers/order.ts": "export async function handleOrder({ message }) {}\n",
        "src/nest.ts": "@Controller()\nexport class Events {\n  @EventPattern('order.shipped')\n  shipped(data) {}\n}\n",
    })
    c = ctx({"type": "kafka", "in": ["orders", "mail"]}, repos=("orders", "mail"), roots={"orders": a, "mail": b})
    edges = run(KafkaLinker(), c)
    assert linked(edges, c.graph, "publishes") == [("orders:src/publish.ts", "order.created"),
                                                   ("orders:src/publish.ts", "order.paid")]
    cons = sorted((c.graph.node(e.src).name, e.dst) for e in edges if e.kind == "consumed_by" and e.resolved)
    assert cons == [("order.created", "mail:src/handlers/order.ts#handleOrder"),
                    ("order.paid", "mail:src/handlers/order.ts#handleOrder"),
                    ("order.shipped", "mail:src/nest.ts#Events.shipped")]
    assert c.graph.node("orders:msg:kafka:order.created").kind == "message_topic"
    assert sorted(e.dst for e in edges if not e.resolved) == ["unresolved:message:someTopic"]


# ---------------------------------------------------------------- SQS

def test_sqs_matches_queue_urls_and_env_settings(tmp_path):
    a = write(tmp_path / "api", {
        "src/send.ts": "await sqs.send(new SendMessageCommand({ QueueUrl: process.env.EMAIL_QUEUE_URL, MessageBody: b }))\n"
                       "await client.send(new SendMessageCommand({ QueueUrl: `${base}/invoices`, MessageBody: b }))\n",
    })
    b = write(tmp_path / "worker", {
        "src/poll.ts": "const app = Consumer.create({ queueUrl: process.env.EMAIL_QUEUE_URL, handleMessage: sendEmail })\n"
                       "await sqs.receiveMessage({ QueueUrl: 'https://sqs.eu-west-1.amazonaws.com/123/invoices' }).promise()\n",
        "src/email.ts": "export const sendEmail = async (m) => {}\n",
    })
    c = ctx({"type": "sqs", "in": ["api", "worker"]}, repos=("api", "worker"), roots={"api": a, "worker": b})
    edges = run(SqsLinker(), c)
    assert linked(edges, c.graph, "publishes") == [("api:src/send.ts", "EMAIL_QUEUE_URL"), ("api:src/send.ts", "invoices")]
    cons = sorted((c.graph.node(e.src).name, e.dst) for e in edges if e.kind == "consumed_by" and e.resolved)
    assert cons == [("EMAIL_QUEUE_URL", "worker:src/email.ts#sendEmail"), ("invoices", "worker:src/poll.ts")]


# ---------------------------------------------------------------- RabbitMQ

def test_binding_patterns():
    assert _binding_matches("order.*", "order.created")
    assert not _binding_matches("order.*", "order.created.eu")
    assert _binding_matches("order.#", "order") and _binding_matches("order.#", "order.created.eu")
    assert _binding_matches("#.eu", "order.created.eu") and not _binding_matches("#.eu", "order.us")


def test_rabbitmq_follows_exchanges_to_bound_queues(tmp_path):
    a = write(tmp_path / "shop", {
        "src/pub.ts": "channel.sendToQueue('emails', Buffer.from(x))\n"
                      "ch.publish('orders', 'order.created', Buffer.from(x))\n"
                      "ch.publish('audit', '', Buffer.from(x))\n",
    })
    b = write(tmp_path / "ops", {
        "src/setup.ts": "await channel.bindQueue('billing', 'orders', 'order.*')\n"
                        "await channel.bindQueue('archive', 'orders', '#')\n"
                        "await channel.bindQueue('stats', 'orders', 'payment.*')\n",
        "src/consume.ts": "channel.consume('billing', onBilling)\nchannel.consume('emails', async (m) => {})\n",
        "src/billing.ts": "export function onBilling(msg) {}\n",
    })
    c = ctx({"type": "rabbitmq", "in": ["shop", "ops"]}, repos=("shop", "ops"), roots={"shop": a, "ops": b})
    edges = run(RabbitMqLinker(), c)
    assert linked(edges, c.graph, "publishes") == [("shop:src/pub.ts", "archive"), ("shop:src/pub.ts", "audit"),
                                                   ("shop:src/pub.ts", "billing"), ("shop:src/pub.ts", "emails")]
    cons = sorted((c.graph.node(e.src).name, e.dst) for e in edges if e.kind == "consumed_by" and e.resolved)
    assert cons == [("billing", "ops:src/billing.ts#onBilling"), ("emails", "ops:src/consume.ts")]
    assert sorted(e.dst for e in edges if not e.resolved) == ["unresolved:message:archive", "unresolved:message:audit"]


def test_unmatched_absolute_urls_are_outside_services_not_missing_links(tmp_path):
    fe = write(tmp_path / "web", {"a.ts": "axios.post('https://api.stripe.com/v1/charges')\nfetch('/api/missing')\n"
                                          "fetch(`${API}/users`)\n"})
    be = write(tmp_path / "svc", {"r.ts": "router.get('/users', list)\n"})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    edges = run(RestLinker(), c)
    assert linked(edges, c.graph, "calls_http") == [("web:a.ts", "GET /users")]
    assert [e.dst for e in edges if not e.resolved] == ["unresolved:http:GET /api/missing"]


# ---------------------------------------------------------------- final review fixes

def test_rest_scales_to_thousands_of_routes_and_calls(tmp_path):
    import time
    routes = "".join(f"router.get('/r{i}/items/:id', h{i})\nexport function h{i}() {{}}\n" for i in range(2000))
    calls = "".join(f"api.get(`/api/r{i}/items/${{id}}`)\n" for i in range(2000))
    fe = write(tmp_path / "web", {"a.ts": calls})
    be = write(tmp_path / "svc", {"r.ts": routes})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    t = time.perf_counter()
    edges = run(RestLinker(), c)
    assert time.perf_counter() - t < 10
    assert len([e for e in edges if e.kind == "calls_http" and e.resolved]) == 2000


def test_broker_names_resolve_only_through_declared_constants(tmp_path):
    a = write(tmp_path / "a", {
        "src/wrap.ts": "export function publish(topic, m) { return producer.send({ topic, messages: m }) }\n"
                       "export class P { send() { return producer.send({ topic: this.topic, messages: [] }) } }\n"
                       "const cfg = { topic: 'user-events' }\n",
        "src/q.ts": "const q = await channel.assertQueue('')\nchannel.bindQueue(q.queue, 'logs', '')\nchannel.consume(q.queue, onLog)\n",
    })
    b = write(tmp_path / "b", {
        "src/c.ts": "await consumer.subscribe({ topic: 'user-events' })\n",
        "src/q.ts": "const q = await channel.assertQueue('')\nchannel.bindQueue(q.queue, 'billing', '')\nchannel.consume(q.queue, onBill)\n",
        "src/p.ts": "channel.publish('billing', '', b)\n",
    })
    roots = {"a": a, "b": b}
    c = ctx({"type": "kafka", "in": ["a", "b"]}, repos=("a", "b"), roots=roots)
    edges = run(KafkaLinker(), c)
    assert linked(edges, c.graph, "publishes") == []          # a wrapper's parameter is not 'user-events'
    c = ctx({"type": "rabbitmq", "in": ["a", "b"]}, repos=("a", "b"), roots=roots)
    edges = run(RabbitMqLinker(), c)
    assert not any(e.kind == "publishes" and e.resolved and "a:" in str(c.graph.node(e.dst)) for e in edges)
    assert not [n for n in c.graph.nodes() if n.kind == "message_topic" and n.name == "queue"]  # server-named queues
    assert linked(edges, c.graph, "consumed_by") == []                                          # stay unmatched


def test_enum_and_object_constants_meet_literals(tmp_path):
    a = write(tmp_path / "a", {"src/t.ts": "export enum Topics { ORDER_CREATED = 'order.created' }\n"
                                           "export const Q = { PAID: 'order.paid' }\n",
                               "src/p.ts": "producer.send({ topic: Topics.ORDER_CREATED, messages: [] })\n"
                                           "producer.send({ topic: Q.PAID, messages: [] })\n"
                                           "producer.send({ topic: Other.ORDER_CREATED, messages: [] })\n"})
    b = write(tmp_path / "b", {"src/c.ts": "consumer.subscribe({ topics: ['order.created', 'order.paid'] })\n"})
    c = ctx({"type": "kafka", "in": ["a", "b"]}, repos=("a", "b"), roots={"a": a, "b": b})
    edges = run(KafkaLinker(), c)
    assert linked(edges, c.graph, "publishes") == [("a:src/p.ts", "Other.ORDER_CREATED"), ("a:src/p.ts", "order.created"),
                                                   ("a:src/p.ts", "order.paid")]


def test_a_parameter_in_the_call_prefers_a_parameter_in_the_route(tmp_path):
    fe = write(tmp_path / "web", {"a.ts": "axios.get(`/users/${id}`)\n"})
    be = write(tmp_path / "svc", {"r.ts": "router.get('/users/me', a)\nrouter.get('/users/search', b)\nrouter.get('/users/:id', c)\n"})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    assert linked(run(RestLinker(), c), c.graph, "calls_http") == [("web:a.ts", "GET /users/:id")]


def test_a_service_both_calling_and_serving_does_not_mistake_its_client_for_routes(tmp_path):
    svc = write(tmp_path / "svc", {
        "src/client.ts": "import axios from 'axios'\nconst api = axios.create()\n"
                         "export const f = () => api.get('/remote/thing', { params })\n"
                         "export const g = (user) => api.post('/remote/users', user)\n",
        "src/server.ts": "import express from 'express'\nconst api = express.Router()\napi.post('/self/x', saveHandler)\n"
                         "router.get('/remote/thing', thing)\n",
    })
    c = ctx({"type": "rest", "from": "svc", "to": "svc"}, repos=("svc",), roots={"svc": svc})
    edges = run(RestLinker(), c)
    routes = sorted(n.name for n in c.graph.nodes() if n.kind == "http_route")
    assert routes == ["GET /remote/thing", "POST /self/x"]
    assert linked(edges, c.graph, "calls_http") == [("svc:src/client.ts", "GET /remote/thing")]


def test_route_chain_keeps_every_method(tmp_path):
    be = write(tmp_path / "svc", {"r.ts": "router.route('/books').get(a).post(b).put(c)\n"})
    fe = write(tmp_path / "web", {"a.ts": "fetch('/books', { method: 'PUT' });\n(api).post('/books')\n"})
    c = ctx({"type": "rest", "from": "web", "to": "svc"}, repos=("web", "svc"), roots={"web": fe, "svc": be})
    edges = run(RestLinker(), c)
    assert sorted(n.name for n in c.graph.nodes() if n.kind == "http_route") == ["GET /books", "POST /books", "PUT /books"]
    assert linked(edges, c.graph, "calls_http") == [("web:a.ts", "POST /books"), ("web:a.ts", "PUT /books")]
