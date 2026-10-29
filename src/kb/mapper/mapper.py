"""LLM step of analysis: name clusters as product modules and write the system overview."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from pydantic import BaseModel, Field

from kb.errors import LLMOutputError
from kb.graph.model import CodeGraph
from kb.llm.base import LLMProvider, LLMRequest
from kb.llm.json_call import ask_json
from kb.mapper.cluster import LINKER_KINDS, Cluster
from kb.packs.model import Module

SYSTEM = (
    "You document software for people who do not read code. Use the product's own glossary terms. "
    "Write plain English: short sentences, no class or function names in summaries."
)
GLOSSARY_LIMIT = 12_000
ENTRY_KINDS = ("route", "graphql_op", "resolver", "queue_topic", "component")


class ModuleDraft(BaseModel):
    name: str = Field(description="Product module name, 1-3 words, using glossary terms")
    summary: str = Field(description="3-5 sentences, plain English: what users do here and what happens behind the scenes")
    entry_points: list[str] = Field(description="1-5 ids chosen ONLY from the candidate entry point list")


def _degree(g: CodeGraph, nid: str) -> int:
    return g.nx.degree(nid) if g.has_node(nid) else 0


def _candidates(c: Cluster, g: CodeGraph, limit: int = 40) -> list[str]:
    nodes = [g.node(m) for m in c.members if g.has_node(m)]
    rank = {k: i for i, k in enumerate(ENTRY_KINDS)}
    nodes.sort(key=lambda n: (rank.get(n.kind, len(ENTRY_KINDS)), -_degree(g, n.id), n.id))
    return [n.id for n in nodes[:limit]]


def _prompt(c: Cluster, g: CodeGraph, glossary: str) -> str:
    member_set = set(c.members)
    symbols = sorted((g.node(m) for m in c.members if g.has_node(m) and g.node(m).kind != "file"),
                     key=lambda n: (-_degree(g, n.id), n.id))[:60]
    links = [e for e in g.edges() if e.kind in LINKER_KINDS and (e.src in member_set or e.dst in member_set)][:30]
    lines = [
        "Name this group of code as one product module.",
        f"Glossary and product context:\n{glossary[:GLOSSARY_LIMIT] or '(none)'}",
        "Folders: " + ", ".join(c.top_paths[:10]),
        "Symbols: " + ", ".join(f"{n.name} ({n.kind}, {n.file})" for n in symbols),
        "Connections: " + ("; ".join(f"{e.src} -{e.kind}-> {e.dst}" for e in links) or "(none)"),
        "Candidate entry point ids:\n" + "\n".join(_candidates(c, g)),
    ]
    return "\n\n".join(lines)


def map_modules(clusters: list[Cluster], g: CodeGraph, context_text: str, provider: LLMProvider, tracer,
                checkpoint_dir: Path, resume: bool, on_module=None) -> tuple[list[Module], list[str]]:
    checkpoint_dir = Path(checkpoint_dir)
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    modules: list[Module] = []
    failed: list[str] = []
    for i, c in enumerate(clusters, 1):
        if on_module is not None:
            on_module(i, len(clusters))
        ck = checkpoint_dir / f"{c.id}.json"
        d = None
        if resume and ck.exists():
            try:
                d = ModuleDraft.model_validate_json(ck.read_text())
            except ValueError:
                d = None  # truncated/corrupt checkpoint: redo it
        if d is None:
            try:
                d = ask_json(provider, LLMRequest(prompt=_prompt(c, g, context_text), system=SYSTEM, cwd=checkpoint_dir),
                             ModuleDraft, tracer, f"name_module:{c.id}")
            except LLMOutputError:
                failed.append(c.id)
                continue
            ck.write_text(d.model_dump_json())
        members = set(c.members)
        entries = [e for e in d.entry_points if e in members] or _candidates(c, g, 1)
        modules.append(Module(id=c.id, name=d.name, summary=d.summary, members=c.members, entry_points=entries))
    return modules, failed


def _inter_module_edges(modules: list[Module], g: CodeGraph) -> Counter:
    owner = {m: mod.id for mod in modules for m in mod.members}
    counts: Counter = Counter()
    for e in g.edges():
        a, b = owner.get(e.src), owner.get(e.dst)
        if e.resolved and a and b and a != b and e.kind in LINKER_KINDS | {"imports", "calls"}:
            counts[(a, b)] += 1
    return counts


def _fallback_mermaid(modules: list[Module], counts: Counter) -> str:
    names = {m.id: m.name.replace('"', "'") for m in modules}
    lines = ["```mermaid", "flowchart LR"]
    lines += [f'  {m.id.replace("-", "_")}["{names[m.id]}"]' for m in modules]
    for (a, b), _ in counts.most_common(30):
        lines.append(f"  {a.replace('-', '_')} -->|uses| {b.replace('-', '_')}")
    lines.append("```")
    return "\n".join(lines)


def write_system_overview(modules: list[Module], g: CodeGraph, provider: LLMProvider, tracer,
                          cwd: Path | None = None) -> str:
    counts = _inter_module_edges(modules, g)
    names = {m.id: m.name for m in modules}
    prompt = "\n\n".join([
        "Write a plain-English overview of how this system fits together, for someone new to it. "
        "Start with one paragraph, then one short paragraph per major flow between modules. "
        "End with a ```mermaid flowchart whose node labels are short sentences (what happens), not code names.",
        "Modules:\n" + "\n".join(f"- {m.name}: {m.summary}" for m in modules),
        "How modules connect (count of code links):\n"
        + "\n".join(f"- {names[a]} -> {names[b]}: {n}" for (a, b), n in counts.most_common(40)),
    ])
    with tracer.span("system_overview") as span:
        result = provider.run(LLMRequest(prompt=prompt, system=SYSTEM, cwd=Path(cwd or Path.cwd())))
        tracer.record_llm(span, result, "claude-code", prompt)
        text = result.text.strip()
        span.end(output=text[:2000])
    if "```mermaid" not in text:
        text += "\n\n" + _fallback_mermaid(modules, counts)
    return text
