"""Turning a local pack into registry uploads and back."""

from __future__ import annotations

from pathlib import Path

from kb_registry.paths import is_allowed_path


def base_files(pack_dir: Path) -> dict[str, bytes]:
    """The base files a push uploads: every allowed path except manifest.json (sent as JSON)."""
    pack_dir = Path(pack_dir)
    out: dict[str, bytes] = {}
    for p in sorted((pack_dir / "base").rglob("*")):
        rel = p.relative_to(pack_dir).as_posix()
        if p.is_file() and rel != "manifest.json" and is_allowed_path(rel):
            out[rel] = p.read_bytes()
    return out


def apply_overlay(store, project: str, branch: str, items: list[dict]) -> tuple[int, list[str]]:
    """Write published items into the local overlay. A local *private* item with the same id and
    different content is kept (yours wins) and reported. Returns (written, warnings)."""
    from kb.packs.model import Flow, QAItem, Suggestion

    pack = store.load(project, branch)
    local_qa = {q.id: q for q in pack.qa}
    local_flows = {f.id: f for f in pack.flows}
    written, warnings = 0, []
    notes: dict[str, tuple[str, str]] = {}
    suggestions = []
    from kb_registry.paths import is_safe_id

    for it in items:
        kind, payload = it.get("kind"), it.get("payload") or {}
        ident = payload.get("module_id") if kind == "module_note" else payload.get("id")
        if not is_safe_id(str(it.get("id", ""))) or not is_safe_id(str(ident or "")):
            warnings.append(f"skipped a shared {kind} with an unsafe id from the registry: {it.get('id')!r}")
            continue
        if kind in ("qa", "flow"):
            model = QAItem if kind == "qa" else Flow
            obj = model.model_validate(payload)
            mine = (local_qa if kind == "qa" else local_flows).get(obj.id)
            if mine is not None and mine.visibility == "private":
                if mine.model_dump(exclude={"visibility", "stale"}) != obj.model_dump(exclude={"visibility", "stale"}):
                    warnings.append(f"kept your private {kind} {obj.id}; the registry has a different one")
                    continue
            if kind == "qa":
                if mine is not None:
                    obj = obj.model_copy(update={"stale": mine.stale})
                store.save_qa(project, branch, obj)
            else:
                store.save_flow(project, branch, obj)
            written += 1
        elif kind == "suggestion":
            suggestions.append(Suggestion.model_validate(payload))
        elif kind == "module_note":
            prev = notes.get(payload["module_id"])
            if prev is None or it.get("published_at", "") >= prev[0]:
                notes[payload["module_id"]] = (it.get("published_at", ""), payload["text"])
    if suggestions:
        store.add_suggestions(project, branch, suggestions)
        written += len(suggestions)
    for module_id, (_, text) in notes.items():
        store.save_module_note(project, branch, module_id, text)
        written += 1
    return written, warnings


def note_id(module_id: str, text: str) -> str:
    import hashlib

    return f"note:{module_id}:{hashlib.sha256(text.encode()).hexdigest()[:12]}"


def overlay_items_for_publish(pack, qa_ids: list[str], include_notes: bool) -> list[dict]:
    """Each Q&A with its flow and the suggestions it produced; optionally every local module note."""
    from kb.errors import KbError

    qa = {q.id: q for q in pack.qa}
    flows = {f.id: f for f in pack.flows}
    items: list[dict] = []
    for qid in qa_ids:
        q = qa.get(qid)
        if q is None:
            raise KbError(f"no saved answer with id '{qid}' (see kb memory list)")
        items.append({"id": q.id, "kind": "qa", "payload": q.model_dump(mode="json")})
        if q.flow_id and q.flow_id in flows:
            f = flows[q.flow_id]
            items.append({"id": f.id, "kind": "flow", "payload": f.model_dump(mode="json")})
        items += [{"id": s.id, "kind": "suggestion", "payload": s.model_dump(mode="json")}
                  for s in pack.suggestions if s.source_qa_id == q.id]
    if include_notes:
        items += [{"id": note_id(mid, text), "kind": "module_note", "payload": {"module_id": mid, "text": text}}
                  for mid, text in sorted(pack.module_notes.items())]
    return items
