"""Shapes the desktop screens need, built from packs, the registry and jobs. Pure functions."""

from __future__ import annotations

import json
from pathlib import Path


def local_branches(store, project: str) -> dict[str, dict]:
    out = {}
    root = Path(store.kb_home) / "packs"
    for d in sorted(root.glob(f"{project}@*")) if root.exists() else []:
        m = d / "manifest.json"
        if not m.exists():
            continue
        manifest = json.loads(m.read_text())
        pack = store.load(project, manifest["branch"])
        out[manifest["branch"]] = {
            "version": manifest.get("version", 1), "status": manifest["status"], "commits": manifest["commits"],
            "author": manifest.get("author"), "created_at": manifest.get("created_at"),
            "modules": len(pack.modules), "qa_total": len(pack.qa),
            "qa_published": sum(1 for q in pack.qa if q.visibility == "published"),
            "synced": bool(manifest.get("synced")), "stats": manifest.get("stats", {}),
        }
    return out


def branch_rows(project: str, store, remote: list[dict] | None, active: dict[str, str]) -> list[dict]:
    """active: branch -> running analyze job id."""
    local = local_branches(store, project)
    remote_by = {r["branch"]: r for r in (remote or [])}
    rows = []
    for b in sorted(set(local) | set(remote_by)):
        lo, re_ = local.get(b), remote_by.get(b)
        rem = None if re_ is None else {"latest_version": re_["latest_version"], "overlay_count": re_["overlay_count"],
                                        "created_by": re_["created_by"], "commits": re_["commits"]}
        if b in active:
            state = "in-progress"
        elif lo is None:
            state = "not-pulled"
        elif rem is None or not lo["synced"]:
            state = "local-only"
        elif rem["latest_version"] > lo["version"]:
            state = "update-available"
        else:
            state = "synced"
        rows.append({"branch": b, "local": lo, "remote": rem, "state": state, "job_id": active.get(b)})
    return rows


def ask_payload(state: dict, stats, seconds: float, pack) -> dict:
    names = {m.id: m.name for m in pack.modules}
    return {
        "answer": state.get("answer", ""), "reused": bool(state.get("reused_flow_id")),
        "reused_flow_id": state.get("reused_flow_id"), "saved_qa_id": state.get("saved_qa_id"),
        "suggestions": state.get("suggestions", []), "warnings": state.get("warnings", []),
        "mermaid": state.get("mermaid", ""), "not_found": bool(state.get("not_found")),
        "steps": [{"side": s.side, "text": s.text, "verified": s.verified,
                   "citation": s.citation.model_dump() if s.citation else None} for s in state.get("steps", [])],
        "how": {"modules": [names.get(m, m) for m in state.get("module_ids", [])],
                "shortlist_size": len(state.get("shortlist", [])), "claude_calls": stats.claude_calls,
                "files_read": stats.files_read, "tool_steps": stats.tool_steps, "seconds": round(seconds, 1),
                "trace_id": stats.trace_id},
    }
