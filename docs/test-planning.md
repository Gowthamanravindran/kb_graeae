# Test planning

KB writes manual test cases a QA team can run: preconditions, numbered steps, test data and expected
results. Each case is grounded in the code it tests. There are two kinds of plan, one per tab under
**Test plans**:

| Tab | What it answers | Needs |
|---|---|---|
| **Test Plan for PR** | What did this pull request change, what could it break, and which cases check that? | git repos, and an analysis of the PR's base branches |
| **Test Scenarios in a Branch** | How do we test this flow completely, as it works today? | an analysed branch (plain folders are fine) |

Nothing calls Claude until you choose how many cases to write. The one exception is a single call that
names the affected flows of a PR.

## Test Plan for PR

1. **Pick the branches.** Fill one row per repo.
   - **Base** is the production branch of that repo (for example `master` for the web app and `develop` for the backend). Repos can differ.
   - **PR branch** is the branch to test. Leave it empty when the PR doesn't change that repo.
   - Optionally paste the **GitHub PR link**: it fills the branches and adds the PR's title and description as context. Private repos need `GITHUB_TOKEN` (in **Settings**, or in `~/.kb/kb.env`).
2. **The base must be analysed.** KB compares the PR with an analysis of exactly those base branches. If there isn't one, the form says **Analyse … first** and offers the analysis. Per-repo branches are analysed under a name such as `production`.
3. **Find what changed** builds the **Impact** screen, with no Claude calls apart from naming the flows:
   - **Changed decision points:** each new or changed branch, validation, permission check, error, field, route, job or data query in the diff, plus behaviour the PR removed.
   - **Affected flows:** each screen, background job or API that reaches the changed code, across repos, ranked by risk (removed behaviour, permission change, crosses repos, background job, no saved flow on the base).
   - **Planned cases per priority:** the count is exact, because cases are planned from the decision points before anything is written.
4. **Choose what to write now.**
   - Untick flows you don't want.
   - Set a number per priority (P0–P3) and, optionally, a maximum per flow.
   - The panel shows how many cases that is and about how many Claude calls it costs.
5. **The Plan board** fills in as each flow finishes. From there you can:
   - **Generate more** cases, by priority, at any time.
   - **Regenerate** stale cases.
   - **Retry** failed ones.
   - Accept or dismiss **suggested extras** (cases Claude thought worth adding that no slot covered).
   - **Export CSV**, which follows the priority filter.

### Priorities

| Priority | What lands there |
|---|---|
| P0 | permission checks (allowed and denied), removed behaviour, and the changed main path of the riskiest third of flows; flows touching delete, payment, refund, invoice or charge code lift their P1 cases to P0 |
| P1 | other changed main paths, each outcome of a changed branch, new or removed fields, routes, jobs and queries |
| P2 | validations (valid and invalid) and errors |
| P3 | regression of unchanged steps, and effects on the other repo or on background jobs |

### When the PR gets new commits

Run **Find what changed** again with the same branches; it updates the same plan:
- **Unchanged code:** cases whose code didn't change stay as they are.
- **Changed code:** cases whose code changed are marked **stale** and can be regenerated.
- **Removed code:** cases for decision points that disappeared are kept but marked obsolete.

## Test Scenarios in a Branch

You can start a scenario plan in two ways:
- Type a scenario ("Admin schedules a task that repeats every week") and pick an analysed branch.
- Or use **Make test plan** on any saved answer in **Ask** or **Memory**.

KB finds or traces the flow as Ask does, then lists every decision point in the code it passes through.
You see the planned cases per priority before choosing how many to write. Regression isn't a category
here, because nothing is being compared.

## CSV

There is one row per case, with these columns:

`ID, Flow, Scenario, Type, Priority, Title, Preconditions, Steps, Test data, Expected, Status, Author, Stale`

Lists inside a cell are numbered (`1. …`). The file is UTF-8 with a byte-order mark, so Excel opens it
with the right characters.

## Command line

```bash
kb testplan pr kb.yaml --base sop-web=master --base sop-backend=develop \
  --head sop-web=feature/x --head sop-backend=feature/x [--pr sop-web=https://github.com/acme/sop-fe/pull/482]
kb testplan generate kb.yaml <plan-id> --p0 30 [--p1 10] [--per-flow 5]
kb testplan status kb.yaml <plan-id>
kb testplan export kb.yaml <plan-id> -o cases.csv [--priority P0]
kb testplan scenario kb.yaml --branch production "Admin schedules a weekly task"
kb analyze kb.yaml --branch sop-web=master --branch sop-backend=develop --label production
```

`default_branch` on a repo in `kb.yaml` fills its base automatically.

## Checking it on your Mac

1. Make the SOP repos available with their git history. In `kb.yaml`, point `local_path` at your clones and set `default_branch` for each.
2. Analyse the base: **Library → Analyse a branch → Different branch per repo**, name it `production`.
3. Pick a real PR: **Test plans → Test Plan for PR**, fill the PR branches (or paste its link) → **Find what changed**.
4. Check the Impact screen:
   - the flows are the screens and jobs you'd expect;
   - the decision points match the diff;
   - the planned counts look sensible.
5. Generate 5 P0 cases with real Claude Code and review them as a QA lead would. Export CSV and open it in Excel.
6. Push a new commit to the PR and run **Find what changed** again. Cases whose code changed show as stale.

Sharing a plan between several QAs is stage 5b: shared plans, reserved slots and lead assignment.
