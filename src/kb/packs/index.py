"""SQLite FTS5 search over node names/files, module summaries and saved Q&As."""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

STOP = {"how", "does", "do", "is", "are", "the", "a", "an", "of", "in", "on", "to", "and", "or", "what", "when",
        "where", "why", "who", "which", "get", "gets", "it", "this", "that", "for", "with", "be", "work", "works"}
_WORD = re.compile(r"[A-Za-z][a-z]+|[A-Z]+(?![a-z])|\d+")


@dataclass
class Hit:
    kind: str
    ref: str
    score: float


def words(text: str) -> list[str]:
    return [w.lower() for w in _WORD.findall(text)]


def _db(pack_dir: Path) -> Path:
    return Path(pack_dir) / "index.sqlite"


def build_index(pack_dir: Path, pack) -> None:
    path = _db(pack_dir)
    tmp = path.with_suffix(".tmp")
    tmp.unlink(missing_ok=True)
    con = sqlite3.connect(tmp)
    con.execute("CREATE VIRTUAL TABLE docs USING fts5(kind UNINDEXED, ref UNINDEXED, text, tokenize='porter unicode61')")
    rows = []
    for n in pack.graph.nodes():
        rows.append(("node", n.id, " ".join([n.name, " ".join(words(n.name)), " ".join(words(n.file))])))
    for m in pack.modules:
        note = pack.module_notes.get(m.id, "")
        rows.append(("module", m.id, f"{m.name} {m.name} {m.summary} {note}"))
    for q in pack.qa:
        rows.append(("qa", q.id, f"{q.question} {q.question} {q.answer}"))
    for f in pack.flows:
        rows.append(("flow", f.id, f"{f.question} " + " ".join(s.text for s in f.steps)))
    con.executemany("INSERT INTO docs VALUES (?, ?, ?)", rows)
    con.commit()
    con.close()
    tmp.replace(path)


def search(pack_dir: Path, query: str, limit: int = 20, kinds: set[str] | None = None) -> list[Hit]:
    terms = [w for w in words(query) if w not in STOP and len(w) > 1]
    if not terms or not _db(pack_dir).exists():
        return []
    match = " OR ".join(f'"{t}"' for t in dict.fromkeys(terms))
    con = sqlite3.connect(_db(pack_dir))
    try:
        sql = "SELECT kind, ref, bm25(docs) FROM docs WHERE docs MATCH ?"
        args: list = [match]
        if kinds:
            sql += f" AND kind IN ({','.join('?' * len(kinds))})"
            args += sorted(kinds)
        rows = con.execute(sql + " ORDER BY bm25(docs) LIMIT ?", [*args, limit]).fetchall()
    finally:
        con.close()
    return [Hit(k, r, -s) for k, r, s in rows]
