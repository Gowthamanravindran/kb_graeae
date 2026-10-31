"""Test planning for Ruby: method spans and decision points (Rails controllers, models, jobs)."""

from kb.testplan.delta import find_span, symbol_spans
from kb.testplan.inventory import extract_decisions

CTRL = '''module Api
  module V1
    class UsersController < ApplicationController
      before_action :authenticate_user!, only: [:show]
      before_action :load_user

      def show
        raise Forbidden unless current_user.admin?
        return head :unprocessable_entity if params[:id].blank?
        if @user.suspended?
          render json: { error: "suspended" }, status: :forbidden
        elsif @user.pending?
          notify
        end
        label = @user.vip? ? "VIP" : "regular"
        case @user.plan
        when :free, :trial then limit = 1
        when :pro then limit = 10
        end
        authorize @user
        orders = Order.where(user_id: @user.id).order(:created_at)
        @user.touch_login!
        @user.save!
        render json: @user
      rescue ActiveRecord::RecordNotFound => e
        head :not_found
      end

      def update
        @user.update!(user_params)
      end

      private

      def user_params
        params.require(:user).permit(:name, :email)
      end
    end
  end
end
'''


def test_ruby_spans_use_graphifys_names():
    spans = {n: (a, b) for n, a, b in symbol_spans(CTRL, "app/controllers/api/v1/users_controller.rb")}
    assert spans["Api::V1::UsersController"][0] == 3
    assert spans[".show"] == (7, 27) and spans[".update"] == (29, 31)
    assert find_span(CTRL, "x.rb", ".user_params") == (35, 37)


def _kinds(src, a, b):
    return sorted((d.kind, d.norm, tuple(d.outcomes)) for d in extract_decisions(src, "app/x.rb", a, b))


def test_rails_decision_points():
    got = _kinds(CTRL, 7, 27)
    assert ("permission", "current_user.admin?", ("allowed", "denied")) in got           # raise … unless
    assert ("validation", "params[:id].blank?", ("valid", "invalid")) in got             # 422 guard
    assert ("permission", "@user.suspended?", ("allowed", "denied")) in got              # renders 403
    assert ("branch", "@user.pending?", ("true", "false")) in got                        # elsif
    assert ("branch", "@user.vip?", ("true", "false")) in got                            # ternary
    assert ("branch", "case @user.plan", (":free", ":trial", ":pro", "else")) in got
    assert ("permission", "authorize @user", ("allowed", "denied")) in got               # Pundit
    assert ("query", "Order.where(user_id: @user.id).order(:created_at)", ("runs",)) in got
    assert ("query", "@user.save!", ("runs",)) in got
    assert ("error", "rescue ActiveRecord::RecordNotFound", ("error",)) in got
    assert not [d for d in got if d[0] == "error" and "Forbidden" in d[1]]               # part of the permission check
    assert not [d for d in got if "touch_login" in d[1]]


def test_class_level_filters_validations_and_permitted_fields():
    got = _kinds(CTRL, 1, 6)
    assert ("permission", "before_action :authenticate_user!, only: [:show]", ("allowed", "denied")) in got
    assert not [d for d in got if "load_user" in d[1]]                                   # not an auth filter
    assert ("field", "name", ("sent",)) in _kinds(CTRL, 35, 37) and ("field", "email", ("sent",)) in _kinds(CTRL, 35, 37)
    model = "class User < ApplicationRecord\n  validates :email, presence: true, uniqueness: true\n  validate :domain_allowed\nend\n"
    assert _kinds(model, 1, 4) == [("validation", "validate :domain_allowed", ("valid", "invalid")),
                                   ("validation", "validates :email, presence: true, uniqueness: true", ("valid", "invalid"))]


def test_a_rails_pr_reaches_the_screen_and_the_job_through_the_links(tmp_path):
    """A PR changing a Rails controller action and a Sidekiq job: the impact flows start at the React screen that
    calls the endpoint, and at the background job; the inventory lists the action's new decision points."""
    from kb.analyze import build_graph
    from kb.config import load_config
    from kb.testplan.delta import graph_delta, head_graph
    from kb.testplan.diff import repo_diff
    from kb.testplan.inventory import inventory
    from kb.testplan.reach import reach
    from kb.testplan.worktrees import worktree_at

    from fixture_repos import _commit, _init, git

    web, api = tmp_path / "repos" / "web", tmp_path / "repos" / "api"
    files = {
        web: {"package.json": '{"dependencies": {"react": "18", "react-router-dom": "6", "axios": "1"}}',
              "src/App.tsx": "import { Route } from 'react-router-dom'\nimport UserPage from './UserPage'\n"
                             "export const App = () => <Route path=\"/users/:id\" element={<UserPage />} />\n",
              "src/UserPage.tsx": "import axios from 'axios'\nexport default function UserPage({ id }) {\n"
                                  "  axios.get(`/api/v1/users/${id}`)\n  return null\n}\n"},
        api: {"Gemfile": "gem 'rails'\ngem 'sidekiq'\n",
              "config/routes.rb": "Rails.application.routes.draw do\n  namespace :api do\n    namespace :v1 do\n"
                                  "      resources :users, only: [:show]\n    end\n  end\nend\n",
              "app/controllers/api/v1/users_controller.rb":
                  "module Api\n  module V1\n    class UsersController < ApplicationController\n      def show\n"
                  "        user = User.find(params[:id])\n        render json: user\n      end\n    end\n  end\nend\n",
              "app/jobs/sync_user_job.rb": "class SyncUserJob\n  include Sidekiq::Job\n  def perform(id)\n"
                                           "    User.find(id)\n  end\nend\n",
              "app/models/user.rb": "class User < ApplicationRecord\n  def sync_later\n    SyncUserJob.perform_async(id)\n"
                                    "  end\nend\n"},
    }
    for root, fs in files.items():
        for rel, text in fs.items():
            (root / rel).parent.mkdir(parents=True, exist_ok=True)
            (root / rel).write_text(text)
    _init(web, "main")
    _commit(web, "base")
    _init(api, "main")
    _commit(api, "base")
    git(api, "checkout", "-q", "-b", "pr")
    ctrl = api / "app/controllers/api/v1/users_controller.rb"
    ctrl.write_text(ctrl.read_text().replace("        user = User.find(params[:id])\n",
                                             "        user = User.find(params[:id])\n"
                                             "        head :forbidden and return unless current_user.admin?\n"))
    job = api / "app/jobs/sync_user_job.rb"
    job.write_text(job.read_text().replace("    User.find(id)\n", "    User.find(id).touch\n"))
    _commit(api, "admins only")
    git(api, "checkout", "-q", "main")
    cfg_path = tmp_path / "kb.yaml"
    cfg_path.write_text(f"""project: shop
repos:
  - {{name: web, role: frontend, local_path: {web}, default_branch: main}}
  - {{name: api, role: backend, local_path: {api}, default_branch: main}}
linkers:
  - {{type: react-router, in: web}}
  - {{type: rest, from: web, to: api}}
  - {{type: sidekiq, in: api}}
""")
    cfg = load_config(cfg_path)
    work = tmp_path / "work"
    base = {"web": worktree_at(cfg.repo("web"), "main", work), "api": worktree_at(cfg.repo("api"), "main", work)}
    head = {"web": worktree_at(cfg.repo("web"), "main", work), "api": worktree_at(cfg.repo("api"), "pr", work)}
    hunks = repo_diff(api, "api", "main", "pr", [])
    bg, hg = build_graph(cfg, base, work / "b").merged, head_graph(cfg, head, work)
    d = graph_delta(bg, hg, hunks, base, head)
    inv = inventory(base, head, d, hunks)
    assert ("permission", "current_user.admin?") in {(i.kind, i.text) for i in inv}
    flows = {f.name: f for f in reach(hg, bg, None, d, inv)}
    assert "Screen /users/:id" in flows and "Background job SyncUserJob" in flows, list(flows)
    screen = flows["Screen /users/:id"]
    assert screen.crosses_repos and any(p.startswith("api:http:GET /api/v1/users/:id") for p in screen.path)
