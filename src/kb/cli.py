from __future__ import annotations

import functools
from pathlib import Path
from typing import Callable

import typer

from kb import __version__
from kb.errors import KbError, LLMUnavailable

app = typer.Typer(no_args_is_help=True, add_completion=False)


def handle_errors(fn: Callable) -> Callable:
    """Turn KbError into a one-line message: exit 2 for LLMUnavailable, 1 otherwise."""

    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except LLMUnavailable as e:
            typer.echo(f"Claude Code is not available: {e}. Run `claude` once to sign in.", err=True)
            raise typer.Exit(2)
        except KbError as e:
            typer.echo(f"Error: {e}", err=True)
            raise typer.Exit(1)

    return wrapper


@app.callback()
def _root() -> None:
    """kb — analyse codebases into shareable knowledge packs."""
    from kb.envfile import load_kb_env

    load_kb_env()  # ~/.kb/kb.env (or $KB_HOME/kb.env): Langfuse keys etc.


@app.command()
def version() -> None:
    """Print the kb version."""
    typer.echo(__version__)


def get_provider():
    from kb.llm.claude_code import ClaudeCodeProvider

    return ClaudeCodeProvider()


def _author() -> str:
    import getpass
    import subprocess

    r = subprocess.run(["git", "config", "user.name"], capture_output=True, text=True)
    return r.stdout.strip() or getpass.getuser()


@app.command()
@handle_errors
def analyze(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: list[str] = typer.Option(..., "--branch", "-b",
                                     help="Branch to analyse (any label for plain folders), or repo=branch per repo"),
    label: str = typer.Option(None, "--label", help="Name for a pack with different branches per repo"),
    resume: bool = typer.Option(False, "--resume", help="Reuse module names already produced for these commits"),
) -> None:
    """Analyse a repo set at a branch into a local pack."""
    from kb.analyze import run_analyze
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.tracing import make_tracer

    from kb.config import resolve_branch_set

    cfg = load_config(config)
    branch, branches = resolve_branch_set(cfg, branch, label)
    store = PackStore()
    m = run_analyze(cfg, branch, store, get_provider(), make_tracer(cfg), resume=resume, author=_author(),
                    branches=branches)
    s = m.stats
    typer.echo(f"{cfg.project}@{branch}: {m.status}")
    if len(set(m.branches.values())) > 1:
        typer.echo("  branches: " + ", ".join(f"{k}={v}" for k, v in m.branches.items()))
    typer.echo("  commits: " + ", ".join(f"{k}={v[:12]}" for k, v in m.commits.items()))
    typer.echo(f"  files {s.get('files', 0)} (unparsed {s.get('unparsed', 0)}), nodes {s.get('nodes', 0)}")
    typer.echo(f"  links {s.get('links_resolved', 0)} resolved, {s.get('links_unresolved', 0)} unresolved")
    typer.echo(f"  modules {s.get('modules', 0)}" + (f", failed: {', '.join(m.failed_modules)} (rerun with --resume)"
                                                      if m.failed_modules else ""))
    typer.echo(f"  pack: {store.pack_dir(cfg.project, branch)}")


def _cite(step) -> str:
    c = step.citation
    return f"{c.repo}:{c.file}:{c.line_start}-{c.line_end}" if c else "no valid citation"


@app.command()
@handle_errors
def ask(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    question: str = typer.Argument(..., help="Your question, e.g. 'How does scheduling work in Tasks?'"),
    branch: str = typer.Option(..., "--branch", "-b"),
    from_qa: str = typer.Option(None, "--from", help="Continue from a saved answer id (see kb memory list)"),
    as_json: bool = typer.Option(False, "--json", help="Print the full result as JSON"),
) -> None:
    """Ask a question about an analysed branch."""
    import json

    from kb.ask import run_ask
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.tracing import make_tracer

    cfg = load_config(config)
    st = run_ask(cfg, branch, question, PackStore(), get_provider(), make_tracer(cfg), from_qa=from_qa, author=_author())
    if as_json:
        out = {k: v for k, v in st.items() if k != "draft_steps"}
        out["steps"] = [s.model_dump() for s in st.get("steps", [])]
        typer.echo(json.dumps(out, indent=2, default=str))
        return
    for w in st.get("warnings", []):
        typer.echo(f"warning: {w}", err=True)
    typer.echo(st.get("answer", ""))
    steps = st.get("steps", [])
    for title, side in (("UI flow", "ui"), ("Backend flow", "backend")):
        part = [s for s in steps if s.side == side]
        if part:
            typer.echo(f"\n{title}:")
            for i, s in enumerate(part, 1):
                mark = "✓" if s.verified else "✗ unverified"
                typer.echo(f"  {i}. {s.text}\n     {mark}  {_cite(s)}")
    if st.get("mermaid"):
        typer.echo("\nDiagram:\n```mermaid\n" + st["mermaid"] + "\n```")
    if st.get("reused_flow_id"):
        typer.echo(f"\n(Reused saved flow {st['reused_flow_id']}; no Claude call made.)")
    if st.get("saved_qa_id"):
        typer.echo(f"\nSaved as {st['saved_qa_id']} (private).")
    if st.get("suggestions"):
        typer.echo("\nYou might also ask:")
        for q in st["suggestions"]:
            typer.echo(f"  - {q}")


memory_app = typer.Typer(help="Saved answers on an analysed branch.")
app.add_typer(memory_app, name="memory")


@memory_app.command("list")
@handle_errors
def memory_list(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: str = typer.Option(..., "--branch", "-b"),
) -> None:
    """List saved Q&As: id, visibility, stale flag, question."""
    from kb.config import load_config
    from kb.packs.store import PackStore

    from kb.sync.outbox import Outbox

    cfg = load_config(config)
    store = PackStore()
    pack = store.load(cfg.project, branch)
    queued = {i["id"] for i in Outbox(store.kb_home).pending(cfg.project, branch)}
    if not pack.qa:
        typer.echo("No saved answers yet.")
    for q in sorted(pack.qa, key=lambda q: q.created_at):
        state = "queued" if q.id in queued else q.visibility
        typer.echo(f"{q.id}  {state:<9} {'STALE ' if q.stale else ''}{q.question}")


@app.command()
@handle_errors
def login(
    registry: str = typer.Argument(..., help="Registry URL, e.g. https://kb.acme.internal"),
    token: str = typer.Option(None, "--token", help="API token from your registry admin"),
) -> None:
    """Sign in to a registry (API token, or single sign-on when the registry uses it)."""
    from kb.packs.store import default_kb_home
    from kb.sync import client as sc
    from kb.sync.credentials import norm_url, save_token

    url = norm_url(registry)
    if token:
        me = sc.RegistryClient(url, token, http=sc.make_http(url)).me()
        save_token(default_kb_home(), url, token, "static")
    else:
        from kb.sync.oidc import device_login

        got = device_login(url, sc.make_http(url))
        save_token(default_kb_home(), url, got["token"], "oidc", got.get("refresh_token"), got.get("expires_at"))
        me = sc.client_for(registry=url).me()
    typer.echo(f"logged in to {url} as {me['email']}")


@app.command("list")
@handle_errors
def list_cmd(
    config: Path = typer.Argument(None, help="kb.yaml: list this project's branches"),
    registry: str = typer.Option(None, "--registry"),
) -> None:
    """List projects on the registry, or the analysed branches of a project."""
    from kb.config import load_config
    from kb.sync import client as sc

    cfg = load_config(config) if config else None
    c = sc.client_for(cfg, registry)
    if cfg is None:
        rows = c.projects()
        if not rows:
            typer.echo("No projects you can see.")
        for p in rows:
            typer.echo(f"{p['name']:<30} {p['role']}")
        return
    rows = c.branches(cfg.project)
    if not rows:
        typer.echo(f"No analysed branches for {cfg.project} yet.")
    for b in rows:
        commits = ", ".join(f"{k}={v[:12]}" for k, v in b["commits"].items())
        typer.echo(f"{b['branch']:<28} v{b['latest_version']:<4} {b['overlay_count']:>4} shared  {b['created_by']}  {commits}")


@app.command()
@handle_errors
def push(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: str = typer.Option(..., "--branch", "-b"),
) -> None:
    """Upload an analysed branch to the registry (maintainers)."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.sync import client as sc
    from kb.sync.commands import push_pack

    cfg = load_config(config)
    out = push_pack(cfg, branch, PackStore(), sc.client_for(cfg))
    typer.echo(f"pushed {cfg.project}@{branch} as version {out['version']} "
               f"({out['files']} files, {out['uploaded']} uploaded)")


@app.command()
@handle_errors
def pull(
    config: Path = typer.Argument(None, help="kb.yaml (or use --project and --registry)"),
    branch: str = typer.Option(..., "--branch", "-b"),
    project: str = typer.Option(None, "--project"),
    registry: str = typer.Option(None, "--registry"),
    force: bool = typer.Option(False, "--force", help="Replace a local analysis that was never pushed"),
) -> None:
    """Download an analysed branch (and everything shared on it) from the registry."""
    from kb.config import load_config
    from kb.errors import ConfigError
    from kb.packs.store import PackStore
    from kb.sync import client as sc
    from kb.sync.commands import pull_pack

    cfg = load_config(config) if config else None
    name = cfg.project if cfg else project
    if not name:
        raise ConfigError("give a kb.yaml, or --project and --registry")
    store = PackStore()
    out = pull_pack(name, branch, store, sc.client_for(cfg, registry), cfg=cfg, force=force)
    for w in out["warnings"]:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(f"pulled {name}@{branch} version {out['version']}: {out['modules']} modules, "
               f"{out['shared']} shared answers ({out['stale']} stale)")
    if cfg is None:
        typer.echo(f"project config saved to {store.kb_home / 'projects' / name / 'kb.yaml'} "
                   "(add local_path for repos you have checked out)")


@app.command()
@handle_errors
def publish(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    ids: list[str] = typer.Argument(None, help="Saved answer ids (see kb memory list)"),
    branch: str = typer.Option(..., "--branch", "-b"),
    notes: bool = typer.Option(False, "--notes", help="Also share your improved module summaries"),
) -> None:
    """Share chosen answers (with their flows and suggestions) on the registry."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.sync import client as sc
    from kb.sync.commands import publish_items

    cfg = load_config(config)
    out = publish_items(cfg, branch, PackStore(), sc.client_for(cfg), ids or [], notes)
    if "queued" in out:
        typer.echo(f"registry unreachable: queued {out['queued']} items; they will be sent on the next "
                   "kb sync / push / pull / publish")
    else:
        typer.echo(f"published {len(out['added'])} new, {len(out['existing'])} already shared")


@app.command()
@handle_errors
def sync(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    branch: str = typer.Option(..., "--branch", "-b"),
) -> None:
    """Send publishes that were queued while the registry was unreachable."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.sync import client as sc
    from kb.sync.commands import flush_outbox
    from kb.sync.outbox import Outbox

    cfg = load_config(config)
    store = PackStore()
    warnings: list[str] = []
    sent = flush_outbox(cfg.project, branch, sc.client_for(cfg), store, warnings)
    for w in warnings:
        typer.echo(f"warning: {w}", err=True)
    left = len(Outbox(store.kb_home).pending(cfg.project, branch))
    typer.echo(f"sent {sent} queued items" + (f"; {left} still queued (registry unreachable)" if left else ""))


@app.command()
def serve(
    port: int = typer.Option(0, "--port", help="0 = any free port"),
    kb_home: Path = typer.Option(None, "--kb-home"),
    ui_dir: Path = typer.Option(None, "--ui-dir", help="Serve the desktop renderer from this folder"),
    exit_on_stdin_eof: bool = typer.Option(False, "--exit-on-stdin-eof",
                                           help="Stop when stdin closes (the desktop app holds it open)"),
) -> None:
    """Run the local API for the Graeae desktop app (127.0.0.1 only; needs KB_SERVE_TOKEN)."""
    import json
    import os
    import sys
    import threading
    import time

    import uvicorn

    from kb.packs.store import default_kb_home
    from kb.serve.app import ServeSettings, create_app

    from kb.envfile import load_kb_env

    load_kb_env(kb_home)  # the desktop app passes --kb-home; its kb.env holds e.g. Langfuse keys
    token = os.environ.get("KB_SERVE_TOKEN")
    if not token:
        typer.echo("KB_SERVE_TOKEN not set", err=True)
        raise typer.Exit(2)
    app_ = create_app(ServeSettings(kb_home=kb_home or default_kb_home(), token=token, ui_dir=ui_dir))
    server = uvicorn.Server(uvicorn.Config(app_, host="127.0.0.1", port=port, log_level="warning"))

    def announce() -> None:
        while not server.started:
            time.sleep(0.05)
        bound = server.servers[0].sockets[0].getsockname()[1]
        sys.stdout.write("KB_SERVE_READY " + json.dumps({"port": bound}) + "\n")
        sys.stdout.flush()

    threading.Thread(target=announce, daemon=True).start()
    if exit_on_stdin_eof:
        def watch_parent() -> None:
            while sys.stdin.buffer.read(4096):
                pass
            server.should_exit = True  # parent gone (quit, crash or kill): don't keep spending Claude usage

        threading.Thread(target=watch_parent, daemon=True).start()
    server.run()
    if exit_on_stdin_eof:
        os._exit(0)  # don't wait for running jobs: nobody is left to read their results


testplan_app = typer.Typer(help="Test plans: cases for a pull request, or for a scenario on a branch.")
app.add_typer(testplan_app, name="testplan")


def _pairs(values: list[str] | None, what: str) -> dict[str, str]:
    out = {}
    for v in values or []:
        if "=" not in v:
            raise KbError(f"{what} must be repo=value, got {v!r}")
        k, val = v.split("=", 1)
        out[k.strip()] = val.strip()
    return out


def _plan_label(store, project: str, plan_id: str) -> str:
    from kb.testplan.store import PlanStore

    label = PlanStore(store).find(project, plan_id)
    if label is None:
        raise KbError(f"no test plan {plan_id} in {project}")
    return label


def _print_counts(slots) -> None:
    from collections import Counter

    c = Counter(s.priority for s in slots if s.status in ("planned", "failed"))
    typer.echo("  planned: " + "  ".join(f"{p} {c.get(p, 0)}" for p in ("P0", "P1", "P2", "P3")))


@testplan_app.command("pr")
@handle_errors
def testplan_pr(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    base: list[str] = typer.Option(None, "--base", help="repo=branch (default: the repo's default_branch)"),
    head: list[str] = typer.Option(None, "--head", help="repo=branch for each repo the PR changes"),
    pr: list[str] = typer.Option(None, "--pr", help="repo=GitHub pull request link"),
) -> None:
    """Find what a pull request changed and plan its test cases (no Claude calls except naming flows)."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan.graph_impact import ChangeSetRequest, run_impact
    from kb.testplan.store import PlanStore
    from kb.tracing import make_tracer

    cfg = load_config(config)
    store = PackStore()
    req = ChangeSetRequest(project=cfg.project, base=_pairs(base, "--base"), head=_pairs(head, "--head"),
                           pr_links=_pairs(pr, "--pr"))
    rep = run_impact(cfg, req, store, get_provider(), make_tracer(cfg), author=_author(),
                     client=_team_client_or_none(cfg))
    label = _plan_label(store, cfg.project, rep.change_set_id)
    typer.echo(f"plan {rep.change_set_id}")
    typer.echo(f"  {len(rep.inventory)} decision points, {len(rep.flows)} flows affected")
    for f in rep.flows:
        typer.echo(f"  - {f.name}  (risk {f.risk:g}: {', '.join(f.risk_reasons) or 'low'})")
    _print_counts(PlanStore(store).load_slots(cfg.project, label, rep.change_set_id))
    if rep.needs_full_reanalysis:
        typer.echo(f"  note: {rep.full_reanalysis_reason}")


@testplan_app.command("scenario")
@handle_errors
def testplan_scenario(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    text: str = typer.Argument(None, help="The scenario, e.g. 'Admin schedules a weekly task'"),
    branch: str = typer.Option(..., "--branch", "-b", help="Analysed branch (label)"),
    answer: str = typer.Option(None, "--from-answer", help="Plan from a saved answer id instead"),
) -> None:
    """Plan test cases for how one flow works today on a branch."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan.scenario import plan_scenario
    from kb.tracing import make_tracer

    cfg = load_config(config)
    sp = plan_scenario(cfg, branch, PackStore(), get_provider(), make_tracer(cfg), text=text, answer_id=answer,
                       author=_author())
    typer.echo(f"plan {sp.plan_id}")
    _print_counts(sp.slots)


@testplan_app.command("generate")
@handle_errors
def testplan_generate(
    config: Path = typer.Argument(..., help="Path to kb.yaml"),
    plan_id: str = typer.Argument(...),
    p0: int = typer.Option(0, "--p0"), p1: int = typer.Option(0, "--p1"),
    p2: int = typer.Option(0, "--p2"), p3: int = typer.Option(0, "--p3"),
    per_flow: int = typer.Option(None, "--per-flow", help="At most this many cases per flow"),
) -> None:
    """Write the next cases, highest priority first."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan.graph_design import run_design
    from kb.testplan.store import PlanStore
    from kb.tracing import make_tracer

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    cs = PlanStore(store).load_change_set(cfg.project, label, plan_id)
    res = run_design(cfg, plan_id, label, store, get_provider(), make_tracer(cfg), "scenario" if cs.kind == "scenario" else "more",
                     {"P0": p0, "P1": p1, "P2": p2, "P3": p3}, per_flow, author=_author(),
                     progress=lambda text, frac: typer.echo(f"  {text}", err=True),
                     client=_team_client(cfg) if cs.shared else None)
    for w in res.warnings:
        typer.echo(f"warning: {w}", err=True)
    typer.echo(f"wrote {res.written} cases" + (f", {res.failed} failed" if res.failed else "")
               + (f", {res.extras} suggested extras" if res.extras else "")
               + (f", {res.extras_from_conflicts} already written by a teammate (kept as extras)" if res.extras_from_conflicts else ""))


def _team_client_or_none(cfg):
    """The registry client when the project has one (a shared plan's re-run updates the team's copy)."""
    from kb.errors import KbError

    try:
        return _team_client(cfg) if cfg.registry else None
    except KbError:
        return None


def _team_client(cfg):
    from kb.errors import ConfigError
    from kb.sync import client as sc

    if not cfg.registry:
        raise ConfigError("this project has no registry: add `registry:` to kb.yaml to work as a team")
    return sc.client_for(cfg)


@testplan_app.command("share")
@handle_errors
def testplan_share(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...)) -> None:
    """Share a test plan with the team through the registry (push the analysis first)."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan import shared

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    shared.share(cfg, label, plan_id, store, _team_client(cfg))
    typer.echo(f"shared {plan_id} on {cfg.project}@{label}")


@testplan_app.command("pull")
@handle_errors
def testplan_pull(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...),
                  label: str = typer.Option(..., "--label", help="The analysis the plan belongs to, e.g. production")) -> None:
    """Download a test plan a teammate shared."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan import shared

    cfg = load_config(config)
    shared.pull(cfg, label, plan_id, PackStore(), _team_client(cfg))
    typer.echo(f"pulled {plan_id}")


@testplan_app.command("sync")
@handle_errors
def testplan_sync(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...)) -> None:
    """Send work done offline and take the team's latest cases, reservations and assignments."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan import shared

    cfg = load_config(config)
    store = PackStore()
    r = shared.sync(cfg, _plan_label(store, cfg.project, plan_id), plan_id, store, _team_client(cfg))
    typer.echo("offline: nothing synced" if r.offline else f"synced: {r.accepted} accepted, {r.extras} became extras")


@testplan_app.command("assign")
@handle_errors
def testplan_assign(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...),
                    flow: list[str] = typer.Option(None, "--flow"), slot: list[str] = typer.Option(None, "--slot"),
                    to: str = typer.Option(None, "--to", help="Member email; leave out to unassign")) -> None:
    """QA lead: assign flows or slots to a teammate."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan import shared

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    client = _team_client(cfg)
    r = client.assign(cfg.project, label, plan_id, slot_ids=slot or None, flow_ids=flow or None, email=to)
    shared.sync(cfg, label, plan_id, store, client)
    typer.echo(f"assigned {len(r['assigned'])} slots" + (f" to {to}" if to else " (unassigned)"))


@testplan_app.command("release")
@handle_errors
def testplan_release(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...),
                     slot: list[str] = typer.Option(..., "--slot")) -> None:
    """Give reserved slots back (a QA lead can release anyone's)."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan import shared

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    client = _team_client(cfg)
    r = client.release(cfg.project, label, plan_id, slot)
    shared.sync(cfg, label, plan_id, store, client)
    typer.echo("released " + (", ".join(r["released"]) or "nothing"))


@testplan_app.command("status")
@handle_errors
def testplan_status(config: Path = typer.Argument(...), plan_id: str = typer.Argument(...)) -> None:
    """Counts per status and priority, and decision points without a case."""
    from collections import Counter

    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan.graph_design import coverage_for
    from kb.testplan.store import PlanStore

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    ps = PlanStore(store)
    slots = ps.load_slots(cfg.project, label, plan_id)
    st = Counter(s.status for s in slots)
    typer.echo("  " + "  ".join(f"{k} {v}" for k, v in sorted(st.items())))
    _print_counts(slots)
    cov = coverage_for(ps, cfg.project, label, plan_id)
    gaps = Counter(cov.items.values())
    typer.echo("  decision points: " + "  ".join(f"{k} {v}" for k, v in sorted(gaps.items())))


@testplan_app.command("export")
@handle_errors
def testplan_export(
    config: Path = typer.Argument(...), plan_id: str = typer.Argument(...),
    out: Path = typer.Option(..., "-o", "--out", help="CSV file to write"),
    priority: list[str] = typer.Option(None, "--priority", help="Only these priorities (repeatable)"),
) -> None:
    """Write the plan's cases as CSV (opens in Excel)."""
    from kb.config import load_config
    from kb.packs.store import PackStore
    from kb.testplan.export import export_csv
    from kb.testplan.store import PlanStore

    cfg = load_config(config)
    store = PackStore()
    label = _plan_label(store, cfg.project, plan_id)
    data = export_csv(PlanStore(store), cfg.project, label, plan_id, priorities=priority or None)
    Path(out).write_bytes(data)
    typer.echo(f"wrote {out}")


def main() -> None:
    app()


if __name__ == "__main__":
    main()
