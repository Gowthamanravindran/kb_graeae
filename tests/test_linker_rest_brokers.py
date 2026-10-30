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
    assert score(["users", "*"], ["api", "users", "*"]) == 1          # mounted under /api
    assert score(["users", "*"], ["orders", "*"]) == -1
    assert score(["*"], ["users"]) == -1                               # nothing fixed matched
    assert score(["api", "users"], ["users"]) == -1                    # the route is longer than the call


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
