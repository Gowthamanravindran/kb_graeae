"""`kb ask`: run the agent over a local pack, using the user's own checkouts as source."""

from __future__ import annotations

from kb.agent.graph import AgentDeps, build_agent
from kb.analyze import context_text
from kb.config import ProjectConfig
from kb.errors import KbError
from kb.ingest.checkout import Checkout, checkout_at
from kb.packs.store import PackStore


def available_checkouts(cfg: ProjectConfig, branch: str, store: PackStore,
                        commits: dict[str, str], branches: dict[str, str] | None = None) -> tuple[dict[str, Checkout], list[str]]:
    """Source for each repo at the pack's commits (url repos) or as it is now (local folders).
    Repos without source are skipped with a warning and answered from the map only."""
    work = store.kb_home / "work" / f"{cfg.project}@{branch.replace('/', '__')}" / "repos"
    out, warnings = {}, []
    for r in cfg.repos:
        try:
            br = (branches or {}).get(r.name, branch)
            out[r.name] = checkout_at(r, br, commits.get(r.name, br), work)
        except KbError as e:
            warnings.append(f"no source for {r.name}: {e}")
    return out, warnings


def run_ask(cfg: ProjectConfig, branch: str, question: str, store: PackStore, provider, tracer,
            from_qa: str | None = None, author: str = "unknown", no_reuse: bool = False) -> dict:
    pack = store.load(cfg.project, branch)
    if from_qa and not any(q.id == from_qa for q in pack.qa):
        raise KbError(f"No saved answer with id '{from_qa}' in {cfg.project}@{branch}. See `kb memory list`.")
    checkouts, warnings = available_checkouts(cfg, branch, store, pack.manifest.commits, pack.manifest.branches)
    for name, co in checkouts.items():
        analysed = pack.manifest.commits.get(name)
        if analysed and analysed != co.commit:
            warnings.append(f"{name} changed since analysis ({analysed[:12]} -> {co.commit[:12]}); "
                            "citations are checked against the current code. Re-run kb analyze to refresh the map.")
    deps = AgentDeps(pack=pack, pack_dir=pack.dir, checkouts=checkouts, provider=provider, tracer=tracer, store=store,
                     author=author, context=context_text(cfg, checkouts) if checkouts else "")
    tags = {"project": cfg.project, "branch": branch, "pack_version": str(pack.manifest.version),
            "commits": ",".join(f"{k}={v[:12]}" for k, v in pack.manifest.commits.items())}
    try:
        with tracer.trace("ask", tags) as root:
            state = build_agent(deps).invoke({"question": question, "seed_qa_id": from_qa, "no_reuse": no_reuse},
                                             config={"callbacks": tracer.langgraph_callbacks()})
            root.end(output={"answer": state.get("answer"), "saved_qa_id": state.get("saved_qa_id")})
    finally:
        tracer.flush()
    return {**state, "warnings": warnings + deps.extra.get("warnings", [])}
