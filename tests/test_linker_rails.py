"""Rails: routes.rb -> controller actions, Ruby HTTP clients, Sidekiq jobs and Kafka in Ruby."""

from pathlib import Path

from kb.linkers.rails import RoutesReader, http_calls
from kb.linkers import rb
from kb.linkers.rest import RestLinker

from linker_helpers import ctx, run


def write(root: Path, files: dict[str, str]) -> Path:
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    return root


ROUTES = """
Rails.application.routes.draw do
  root 'home#index'
  concern :commentable do
    resources :comments, only: [:index, :create]
  end
  namespace :api do
    namespace :v1 do
      resources :users, only: [:index, :show, :create, :update] do
        member do
          post :ban
        end
        collection { get :search }
        get :preview, on: :member
        resources :orders, only: :index
      end
      resource :profile, only: [:show]
      resources :posts, concerns: :commentable, except: [:new, :edit, :destroy]
      get 'status' => 'health#show'
      get 'reports/:id/download', to: 'reports#download'
      match 'webhooks/:provider', to: 'webhooks#receive', via: [:post, :put]
    end
  end
  scope module: 'admin', path: 'staff' do
    resources :invites, only: :create
  end
  draw :billing
  mount Sidekiq::Web => '/sidekiq'
end
"""


def rails_app(root: Path) -> Path:
    files = {"config/routes.rb": ROUTES,
             "config/routes/billing.rb": "resources :invoices, only: [:show]\n",
             "app/controllers/api/v1/users_controller.rb": "module Api\n  module V1\n    class UsersController < ApplicationController\n"
                                                         "      def show; end\n      def ban; end\n    end\n  end\nend\n"}
    return write(root, files)


def test_routes_rb_becomes_routes_with_controller_actions(tmp_path):
    app = rails_app(tmp_path / "api")
    got = {(r.method, r.path): (r.ref[1:3] if r.ref else None) for r in RoutesReader(app, "api", app).read()}
    U = "Api::V1::UsersController"
    assert got[("GET", "/")] is None
    assert got[("GET", "/api/v1/users/:id")] == (U, "show")
    assert got[("POST", "/api/v1/users/:id/ban")] == (U, "ban")
    assert ("GET", "/api/v1/users/search") in got and ("GET", "/api/v1/users/:id/preview") in got
    assert ("PATCH", "/api/v1/users/:id") in got and ("PUT", "/api/v1/users/:id") in got
    assert ("DELETE", "/api/v1/users/:id") not in got                      # only:
    assert ("GET", "/api/v1/users/:user_id/orders") in got                 # nested
    assert ("GET", "/api/v1/profile") in got and ("GET", "/api/v1/profile/:id") not in got   # singular resource
    assert ("POST", "/api/v1/posts/:post_id/comments") in got              # concern
    assert ("GET", "/api/v1/posts/new") not in got                         # except:
    assert ("GET", "/api/v1/status") in got and ("GET", "/api/v1/reports/:id/download") in got
    assert ("POST", "/api/v1/webhooks/:provider") in got and ("PUT", "/api/v1/webhooks/:provider") in got
    assert ("POST", "/staff/invites") in got                               # scope path + module
    assert ("GET", "/invoices/:id") in got                                 # draw :billing
    assert not any("sidekiq" in p for _, p in got)
    users = next(r for r in RoutesReader(app, "api", app).read() if r.path == "/api/v1/users/:id" and r.method == "GET")
    assert users.ref[0] == ("api", "app/controllers/api/v1/users_controller.rb")
    assert users.file == ("api", "config/routes.rb")


def test_ruby_http_clients(tmp_path):
    src = '''
class PaymentsClient
  def initialize
    @conn = Faraday.new(url: ENV.fetch("PAYMENTS_URL"))
  end
  def charge(id) = @conn.post("/v1/charges", { id: id })
  def refund(id) = conn.post("v1/refunds/#{id}")
  def ping = HTTParty.get("#{ENV['PAYMENTS_URL']}/health")
  def old = RestClient::Request.execute(method: :delete, url: "https://payments.internal/v1/cards/1")
  def net = Net::HTTP.get(URI("http://inventory/api/items"))
  def raw = Net::HTTP::Put.new("/api/items/2")
  def cache = Rails.cache.get("/not/http")
end
class Inventory
  include HTTParty
  base_uri ENV["INVENTORY_URL"]
  def stock(sku) = self.class.get("/stock/#{sku}")
  def all = get("/stock")
end
'''
    calls = {(c.method, c.path, c.external) for c in http_calls(rb.parse_text(src), ("app", "c.rb"))}
    assert calls == {("POST", "/v1/charges", False), ("POST", "/v1/refunds/*", True), ("GET", "/health", False),
                     ("DELETE", "/v1/cards/1", True), ("GET", "/api/items", True), ("PUT", "/api/items/2", False),
                     ("GET", "/stock/*", False), ("GET", "/stock", False)}


def test_react_app_and_rails_service_link_to_rails_controller_actions(tmp_path):
    from kb.graph.model import CodeNode

    app = rails_app(tmp_path / "api")
    web = write(tmp_path / "web", {"src/users.ts": "axios.get(`/api/v1/users/${id}`)\naxios.post(`/api/v1/users/${id}/ban`)\n"})
    pay = write(tmp_path / "pay", {"app/clients/users.rb": "class C\n  def x = conn.get(\"/api/v1/users/search\")\nend\n"})
    c = ctx({"type": "rest", "from": ["web", "pay"], "to": "api"}, repos=("web", "api", "pay"),
            roots={"web": web, "api": app, "pay": pay})
    show = "api:app/controllers/api/v1/users_controller.rb#.show"     # Graphify's node for the action
    c.graph.add_node(CodeNode(id=show, repo="api", kind="function", name=".show",
                              file="app/controllers/api/v1/users_controller.rb", line=4))
    edges = run(RestLinker(), c)
    calls = sorted((e.src, c.graph.node(e.dst).name) for e in edges if e.kind == "calls_http" and e.resolved)
    assert calls == [("pay:app/clients/users.rb", "GET /api/v1/users/search"),
                     ("web:src/users.ts", "GET /api/v1/users/:id"), ("web:src/users.ts", "POST /api/v1/users/:id/ban")]
    served = {c.graph.node(e.src).name: e.dst for e in edges if e.kind == "served_by"}
    assert served["GET /api/v1/users/:id"] == show                     # attaches to Graphify's method node
    assert served["POST /api/v1/users/:id/ban"].endswith("users_controller.rb#Api::V1::UsersController.ban")


# ---------------------------------------------------------------- Sidekiq / ActiveJob

def test_sidekiq_and_active_job_link_callers_to_perform(tmp_path):
    from kb.graph.model import CodeNode
    from kb.linkers.sidekiq import SidekiqLinker

    api = write(tmp_path / "api", {
        "app/controllers/users_controller.rb": "class UsersController\n  def show\n"
                                               "    SyncUserJob.perform_async(1)\n"
                                               "    Billing::InvoiceJob.set(queue: :low).perform_later(2)\n"
                                               "    MailerJob.perform_in(5.minutes, 3)\n"
                                               "    Sidekiq::Client.push('class' => 'ReportWorker', 'args' => [])\n"
                                               "    GhostJob.perform_async\n    NowJob.perform_now\n  end\nend\n",
        "app/jobs/sync_user_job.rb": "class SyncUserJob\n  include Sidekiq::Job\n  def perform(id); end\nend\n",
        "app/jobs/billing/invoice_job.rb": "module Billing\n  class InvoiceJob < ApplicationJob\n    def perform(id); end\n  end\nend\n",
        "app/jobs/mailer_job.rb": "class MailerJob\n  include Sidekiq::Worker\n  def perform(id); end\nend\n",
        "app/jobs/now_job.rb": "class NowJob < ApplicationJob\n  def perform; end\nend\n",
    })
    worker = write(tmp_path / "worker", {"app/workers/report_worker.rb": "class ReportWorker\n  include Sidekiq::Job\n  def perform; end\nend\n"})
    c = ctx({"type": "sidekiq", "in": ["api", "worker"]}, repos=("api", "worker"), roots={"api": api, "worker": worker})
    graphify_perform = "api:app/jobs/sync_user_job.rb#.perform"
    c.graph.add_node(CodeNode(id=graphify_perform, repo="api", kind="function", name=".perform",
                              file="app/jobs/sync_user_job.rb", line=3))
    edges = run(SidekiqLinker(), c)
    enq = sorted(c.graph.node(e.dst).name for e in edges if e.kind == "enqueues")
    assert enq == ["Billing::InvoiceJob", "GhostJob", "MailerJob", "ReportWorker", "SyncUserJob"]  # perform_now: inline
    cons = {c.graph.node(e.src).name: e.dst for e in edges if e.kind == "consumed_by" and e.resolved}
    assert cons["SyncUserJob"] == graphify_perform
    assert cons["Billing::InvoiceJob"].endswith("app/jobs/billing/invoice_job.rb#Billing::InvoiceJob.perform")
    assert cons["ReportWorker"].startswith("worker:app/workers/report_worker.rb#")      # another service's worker
    assert c.graph.node("api:queue_topic:SyncUserJob").kind == "queue_topic"
    assert [e.dst for e in edges if not e.resolved] == ["unresolved:queue:GhostJob"]


# ---------------------------------------------------------------- Kafka in Ruby

def test_kafka_between_rails_karafka_racecar_and_node(tmp_path):
    from kb.linkers.brokers import KafkaLinker

    rails = write(tmp_path / "rails", {
        "app/lib/topics.rb": "module Topics\n  ORDERS = 'orders.created'\n  REFUNDS = ENV.fetch('REFUNDS_TOPIC', 'refunds')\nend\n",
        "app/services/order_publisher.rb": "class OrderPublisher\n  def call(o)\n"
                                           "    Karafka.producer.produce_async(topic: Topics::ORDERS, payload: o.to_json)\n"
                                           "    Karafka.producer.produce_sync(topic: :audit, payload: '')\n"
                                           "    producer.produce_many_async([{ topic: 'bulk', payload: '' }])\n"
                                           "    Karafka.producer.produce_async(topic: @topic, payload: '')\n  end\nend\n",
        "karafka.rb": "class KarafkaApp < Karafka::App\n  routes.draw do\n    consumer_group :g do\n"
                      "      topic :payments do\n        consumer PaymentsConsumer\n      end\n"
                      "      topic Topics::REFUNDS do\n        consumer RefundsConsumer\n      end\n    end\n  end\nend\n",
        "app/consumers/payments_consumer.rb": "class PaymentsConsumer < ApplicationConsumer\n  def consume\n  end\nend\n",
        "app/consumers/refunds_consumer.rb": "class RefundsConsumer < ApplicationConsumer\n  def consume; end\nend\n",
    })
    racecar = write(tmp_path / "audit", {"app/consumers/audit_consumer.rb":
                                         "class AuditConsumer < Racecar::Consumer\n  subscribes_to 'audit', 'bulk'\n"
                                         "  def process(message); end\nend\n"})
    node = write(tmp_path / "node", {
        "src/c.ts": "await consumer.subscribe({ topic: 'orders.created' })\nawait consumer.run({ eachMessage: onOrder })\n",
        "src/h.ts": "export async function onOrder() {}\n",
        "src/p.ts": "await producer.send({ topic: 'payments', messages: [] })\n"
                    "await producer.send({ topic: process.env.REFUNDS_TOPIC, messages: [] })\n"})
    roots = {"rails": rails, "audit": racecar, "node": node}
    c = ctx({"type": "kafka", "in": list(roots)}, repos=tuple(roots), roots=roots)
    edges = run(KafkaLinker(), c)
    cons = sorted((c.graph.node(e.src).name, e.dst) for e in edges if e.kind == "consumed_by" and e.resolved)
    assert cons == [
        ("REFUNDS_TOPIC", "rails:app/consumers/refunds_consumer.rb#RefundsConsumer.consume"),
        ("audit", "audit:app/consumers/audit_consumer.rb#AuditConsumer.process"),
        ("bulk", "audit:app/consumers/audit_consumer.rb#AuditConsumer.process"),
        ("orders.created", "node:src/h.ts#onOrder"),
        ("payments", "rails:app/consumers/payments_consumer.rb#PaymentsConsumer.consume"),
    ]
    pubs = sorted((e.src, c.graph.node(e.dst).name) for e in edges if e.kind == "publishes" and e.resolved)
    assert pubs == [("node:src/p.ts", "REFUNDS_TOPIC"), ("node:src/p.ts", "payments"),
                    ("rails:app/services/order_publisher.rb", "audit"), ("rails:app/services/order_publisher.rb", "bulk"),
                    ("rails:app/services/order_publisher.rb", "orders.created")]
    assert [e.dst for e in edges if not e.resolved] == ["unresolved:message:@topic"]


# ---------------------------------------------------------------- final review fixes

def _routes(tmp_path, text):
    app = write(tmp_path / "app", {"config/routes.rb": f"Rails.application.routes.draw do\n{text}\nend\n"})
    return {(r.method, r.path) for r in RoutesReader(app, "app", app).read()}


def test_percent_i_and_w_arrays(tmp_path):
    got = _routes(tmp_path, "resources :users, only: %i[index show]\nresources :posts, except: %i[new edit destroy update create]\n"
                            "match 'hook', to: 'hooks#run', via: %w[post put]")
    assert got == {("GET", "/users"), ("GET", "/users/:id"), ("GET", "/posts"), ("GET", "/posts/:id"),
                   ("POST", "/hook"), ("PUT", "/hook")}


def test_shallow_nesting(tmp_path):
    got = _routes(tmp_path, "resources :posts, only: [], shallow: true do\n  resources :comments, only: [:index, :show]\nend\n"
                            "shallow do\n  resources :boards, only: [] do\n    resources :cards, only: [:create, :destroy]\n  end\nend")
    assert got == {("GET", "/posts/:post_id/comments"), ("GET", "/comments/:id"),
                   ("POST", "/boards/:board_id/cards"), ("DELETE", "/cards/:id")}


def test_concerns_statement_and_on_member_inside_concern(tmp_path):
    got = _routes(tmp_path, "concern :c do\n  get :preview, on: :member\n  resources :likes, only: :create\nend\n"
                            "resources :users, only: [] do\n  concerns :c\nend")
    assert got == {("GET", "/users/:id/preview"), ("POST", "/users/:user_id/likes")}


def test_namespaced_jobs_with_the_same_short_name_dont_collide(tmp_path):
    from kb.linkers.sidekiq import SidekiqLinker

    api = write(tmp_path / "api", {
        "app/jobs/billing/sync_job.rb": "module Billing\n  class SyncJob\n    include Sidekiq::Job\n    def perform; end\n  end\nend\n",
        "app/jobs/crm/sync_job.rb": "module Crm\n  class SyncJob\n    include Sidekiq::Job\n    def perform; end\n  end\nend\n",
        "app/jobs/solo_job.rb": "class SoloJob\n  include Sidekiq::Job\n  def perform; end\nend\n",
        "app/x.rb": "Crm::SyncJob.perform_in(5, 1)\nSidekiq::Client.push('class' => 'Billing::SyncJob')\nSoloJob.perform_async\n",
        "app/crm/y.rb": "module Crm\n  class Y\n    def go = SyncJob.perform_async\n  end\nend\n",
    })
    c = ctx({"type": "sidekiq", "in": "api"}, repos=("api",), roots={"api": api})
    edges = run(SidekiqLinker(), c)
    cons = sorted((c.graph.node(e.src).name, e.dst.split("#")[0]) for e in edges if e.kind == "consumed_by" and e.resolved)
    assert cons == [("Billing::SyncJob", "api:app/jobs/billing/sync_job.rb"), ("Crm::SyncJob", "api:app/jobs/crm/sync_job.rb"),
                    ("SoloJob", "api:app/jobs/solo_job.rb")]
    enq = sorted((e.src, c.graph.node(e.dst).name) for e in edges if e.kind == "enqueues")
    assert ("api:app/crm/y.rb", "Crm::SyncJob") in enq                   # resolved through the caller's module
