# KB Desktop

The desktop app is an Electron window around the same engine the CLI uses. On start it launches
`kb serve` (the "engine") on a random local port with a one-time token. The engine serves both
the API and the app's pages. The window only talks to `127.0.0.1`, and every request carries the token.

```
Electron main ──spawns──▶ kb serve --port 0 --kb-home … --ui-dir …   (prints KB_SERVE_READY {"port": N})
     │                         │
     └─ window loads http://127.0.0.1:N/  (token added to every request by the main process)
```

## Develop

Use **Node 22 LTS** (`desktop/.nvmrc`). On Node 26, Electron's installer stops partway through unzipping, and `npm run dev` then fails with "Electron failed to install correctly".

```bash
uv sync                          # engine + dev deps (repo root)
npm --prefix desktop install
npm --prefix desktop run dev     # Vite + Electron; the engine runs as `uv run kb serve`
```

The app keeps its project list in `$KB_HOME/desktop.json` (default `~/.kb`).

### Many repos and services

In `kb.yaml`, a link's `from`, `to` and `in` take one repo or a list:

```yaml
linkers:
  - type: graphql
    from: [sop-web, sop-mobile, sop-editor]
    to: [sop-backend, payments-svc]          # an operation is matched against all of them
    options:
      backend_style: {sop-backend: graphql-compose, payments-svc: sdl}   # or one style for all
  - type: bull-queue
    in: [sop-backend, worker-svc]            # a job queued in one service and processed in another links up
```

An operation counts as unmatched only when no listed service handles it. Several entries can share one folder or
link with different `path:` values; that is how a monorepo's apps are listed.

### REST and message brokers

Services that talk over HTTP or through a broker are linked too:

```yaml
linkers:
  - type: rest
    from: [sop-web, sop-backend]     # apps, and services that call other services
    to: [sop-backend, payments-svc]  # services that serve HTTP routes
  - type: kafka                      # or sqs, rabbitmq
    in: [orders-svc, mail-svc]       # a message sent by one service and handled by another links up
```

- **rest**: `fetch`, `axios` and client objects (`api.get`, `this.http.post`, …) in `from` are matched to Express,
  Koa-router, Fastify, hapi and NestJS (`@Controller` + `@Get`) routes in `to`. Paths are compared from the end, so a
  router mounted under `/api` or a client with a base URL still meets; `:id`, `{id}` and `${…}` match any part.
  The route matching the most fixed parts wins. A path no route serves counts as unmatched; a full URL no route
  serves is taken to be an outside service (Stripe, Google…) and left out. URLs held in variables are skipped.
- **kafka**: kafkajs `producer.send({topic})` to `consumer.subscribe({topic(s)})`, with the handler given to
  `consumer.run({eachMessage})`; NestJS `@EventPattern` / `@MessagePattern` and `client.emit`.
- **sqs**: AWS SDK `SendMessageCommand` / `ReceiveMessageCommand` (and v2 `sendMessage` / `receiveMessage`),
  `sqs-consumer` and `sqs-producer` (parameters may be in a variable declared in the same file). Queues are matched by name (the last part of the queue URL), or by the
  setting that holds the URL (`process.env.EMAIL_QUEUE_URL`) when both sides read the same one.
- **rabbitmq**: amqplib `sendToQueue` / `consume`, and `publish(exchange, key)` followed through `bindQueue` to the
  queues it reaches, by the exchange's type from `assertExchange` (fanout: every bound queue; direct: the same
  key; topic: `*` and `#` patterns); NestJS patterns as for Kafka.

NestJS `@EventPattern` / `@MessagePattern` handlers are counted for a broker only in repos whose `package.json` uses
it (kafkajs, amqplib…), so they aren't drawn twice; `client.emit` only counts in files importing
`@nestjs/microservices`, so socket.io isn't mistaken for a broker. `process.env.T || 'orders'` uses the setting,
else the default, and `{ cmd: 'sum' }` patterns are matched as `cmd=sum`.

### Ruby on Rails

Rails apps are read alongside JavaScript/TypeScript ones, and link to them both ways:

```yaml
linkers:
  - type: rest
    from: [web, orders-api]           # a React app and a Rails app that calls other services
    to: [orders-api, payments, inventory]
  - type: sidekiq
    in: [orders-api, worker]          # a job queued by one app and run by another links up
  - type: kafka
    in: [orders-api, notifications]   # Ruby and Node producers and consumers meet on the topic name
```

- **Routes** come from `config/routes.rb` (and `draw(:name)` files): `resources` / `resource` with `only:` and
  `except:` (also as `%i[…]`), nesting, `shallow` nesting, `member` / `collection` and `on:`, `namespace`, `scope`,
  `concern`s (`concerns: […]` or `concerns :x`), and `get 'x', to: 'c#a'` style routes. Each route points at its controller action, so `GET /api/v1/users/:id` leads to
  `Api::V1::UsersController#show`. Every Rails app in a repo (each folder with a `config/routes.rb`) is read.
- **HTTP calls** from Ruby: Faraday, HTTParty (including `self.class.get` and bare `get` in an HTTParty class),
  RestClient,
  http.rb, Typhoeus, Excon and Net::HTTP. A path relative to a client's base URL (`conn.get('charges')`) is
  matched when a service serves it, and otherwise left out rather than counted as unmatched.
- **Jobs**: Sidekiq `perform_async` / `perform_in` / `perform_at` / `perform_bulk` and `Sidekiq::Client.push`, and
  ActiveJob `perform_later` (also through `.set(…)`), lead to the job's `perform`. Jobs are matched by full class
  name, looked up from the caller's modules as Ruby does, so `Billing::SyncJob` and `Crm::SyncJob` stay apart. `perform_now` runs inline and
  isn't counted.
- **Kafka**: Karafka routes (`topic :orders do consumer OrdersConsumer end` → `#consume`), WaterDrop and
  `Karafka.producer.produce_*`, ruby-kafka, Racecar (`subscribes_to` → `#process`) and rdkafka. Topic constants
  (`Topics::ORDERS`) and `ENV['X']` / `ENV.fetch('X', …)` settings are resolved like their JavaScript equivalents.

Add project reads a repo's `Gemfile`: Rails, Sinatra, Grape, Hanami or Roda make it a backend; Sidekiq (or GoodJob,
Solid Queue, Resque…) suggests the jobs link; Karafka, WaterDrop, ruby-kafka, Racecar or rdkafka add it to Kafka.
A Rails app in a sub-folder of a larger repo is found too. SQS and RabbitMQ from Ruby, and GraphQL served by
graphql-ruby, aren't linked yet.

Topic and queue names written as constants (`Topics.ORDER_CREATED`, from an object or enum) are looked up across all
the listed repos, so a literal in one service meets a constant in another. A name held in a function parameter,
`this.topic` or a config object isn't guessed: it counts as unmatched rather than risk a wrong line. In the Map's **Graph** view each kind has its own colour;
pick a line to see the calls or messages it stands for. A change to a message handler shows up in test planning
as "Message <topic>", and an endpoint nothing in the analysed repos calls as "API <method> <path>".

### Adding a project

Choose **Add a project** (on the empty Library, or **Add a project…** in the project menu).
1. **Name the project and list its repos.** For each repo, paste a GitHub link or choose the folder of a clone you have. A link is cloned with your own git sign-in, so a private repo works if `git clone <link>` works in Terminal.
2. **Read repos.** KB reads each repo's `package.json` and suggests:
   - a name;
   - a role (frontend or backend);
   - the production branch, chosen from the repo's branches;
   - for a monorepo, **one entry per app**: `sop-fe` gives `sop-web`, `sop-mobile`, `sop-editor` and `sop-commons`, each pointing at its package. Component kits and other libraries with no GraphQL, routes, queues, brokers or server are left out. Remove any app you don't want;
   - how the repos connect, with one link of each kind covering every repo it applies to:
     - GraphQL from every app that calls it to every service that serves it;
     - React Router screens in every app that has them;
     - Bull queues matched across every service that uses them;
     - REST from every app (and every service with an HTTP client such as axios) to every service running
       Express, Koa, Fastify, NestJS or hapi;
     - Kafka (kafkajs), SQS (`@aws-sdk/client-sqs`, sqs-consumer) and RabbitMQ (amqplib) across every service that
       uses each.

   Change anything that's off.
3. **Create project.** KB writes `~/.kb/projects/<name>/kb.yaml` and opens the project. You can still edit that file by hand.

**Use an existing kb.yaml…** in the same dialog adds a project from a `kb.yaml` you already have.

### Re-analysing or deleting a branch

The **⋯** menu on an analysed branch in the Library has three actions:
- **Re-analyse** rebuilds the map and modules from the current code. It keeps saved answers and test plans, and marks answers whose code changed as stale.
- **Start fresh…** deletes the branch's analysis, including its answers and test plans, then analyses the same branches again. A per-repo set such as `production` keeps its branch per repo.
- **Delete…** removes the analysis from this computer. A copy shared to the registry stays there and can be pulled again.

Each one asks first and says what will be lost. None of them can run while a question, analysis or test plan is running on that branch.

### Tests

| What | Command |
|---|---|
| Engine API | `uv run pytest tests/serve` |
| Renderer (Vitest + Testing Library) | `npm --prefix desktop test` |
| Whole app (Playwright driving Electron) | `npm --prefix desktop run build && npm --prefix desktop run e2e` (Linux without a display: `xvfb-run -a npm --prefix desktop run e2e`) |

The end-to-end tests use a scripted fake instead of Claude. Set `KB_SERVE_PROVIDER=module:function`
to make `kb serve` build its LLM provider from that function. The tests set it to
`e2e_fakes:make_provider` from `tests/e2e_support`. This is a testing hook only; leave it unset in real use.
`full_journey` also starts a real `kb-registry` on SQLite and saves screenshots of each view to
`desktop/e2e/screenshots/` (git-ignored).

## Build the app

Always build the engine first. The app bundles whatever is in `dist/kb-serve`, so a stale
engine ships stale behaviour.

```bash
bash packaging/build-sidecar.sh          # PyInstaller one-folder engine → dist/kb-serve (includes Graphify)
npm --prefix desktop run dist            # app for the current OS → desktop/release/
```

| OS | Output | Notes |
|---|---|---|
| macOS | `KB Desktop-<v>-arm64.dmg`, `…-x64.dmg` | build on a Mac, once per architecture you need: the engine is native to the machine that built it |
| Windows | NSIS installer | build on Windows |
| Linux | `KB Desktop-<v>.AppImage` | `npm --prefix desktop run dist -- --linux AppImage` |

The builds are **unsigned** for now.

- **macOS, first launch:** right-click the app in Applications → **Open** → **Open**. After that it opens normally. If macOS says the app "is damaged", run `xattr -dr com.apple.quarantine "/Applications/KB Desktop.app"`.
- **Windows:** SmartScreen → **More info** → **Run anyway**.

Claude Code must be installed and signed in on the machine (`claude` once) to ask new questions.
Without it the app still opens, browses maps and reads saved answers, and the Ask view explains what is missing.
Langfuse is optional. Enter the keys in **Settings** (they are saved to `~/.kb/kb.env`), or put `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` and `LANGFUSE_HOST` in that file yourself
(see `docs/langfuse-local.md`). That gives "Open trace in Langfuse" and thumbs feedback, even when the app is opened from Finder.

If the engine fails to start, the app shows **KB engine didn't start** with the last lines of the log,
a **Retry** button, and **Open logs** (`$KB_HOME/logs/`).

## Mac acceptance checklist (to do on your Mac)

1. In the repo: `uv sync && npm --prefix desktop install`.
2. `bash packaging/build-sidecar.sh && npm --prefix desktop run dist`, and check that `desktop/release/` has a `.dmg`.
3. Install the dmg, then first-launch with right-click → Open. Expect the Library with the empty-state message.
4. Add a project: **Add a project** → name it `sop`, paste the links of `sop-fe-master` and `sop-backend-main` (or choose your clones) → **Read repos**. Check that the web repo uses `packages/sop-web`, the GraphQL link uses `graphql-compose`, and the production branches are right → **Create project**.
5. **Analyse a branch** → `main`. Watch the progress steps. The row ends as "Private · local only".
6. **Open** → the Map shows the SOP modules in plain English. Toggle **Code names**, pick **Tasks**, then **Ask about Tasks**. Switch to **Graph**: each repo is a column. Lines show GraphQL calls from web to backend and the jobs queued for workers. Pick a line to list the operations with their file and line on both sides.
7. Ask "How does scheduling work in Tasks?" with real Claude Code. Expect UI flow and Backend flow with citations, a diagram, and "n of m steps verified". Click a citation: it opens in your editor. A citation to a script or a file with no extension is shown in Finder rather than opened, so nothing runs.
8. With the registry from `docs/registry-acceptance.md` Run 2:
   - sign in from the sidebar (**Sign in to registry**);
   - **Push to registry**, then **Share to sop@main** on the answer;
   - Memory shows the answer as Published.
9. Second profile: quit the app, then start it with another home, for example
   `KB_HOME=~/.kb-second open -a "KB Desktop"`.
   - Sign in as the viewer, **Pull** `main`, and ask the same question.
   - Expect "Reused a saved answer — no Claude call".
10. Quit the app, then confirm no engine is left running: `pgrep -f kb-serve` prints nothing.
