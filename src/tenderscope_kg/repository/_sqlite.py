"""
TenderScope Knowledge Graph — SQLite reference implementation.

╔══════════════════════════════════════════════════════════════════════════╗
║  REFERENCE IMPLEMENTATION ONLY — NEVER USE IN PRODUCTION               ║
║                                                                          ║
║  This backend exists for:                                                ║
║    • Repository contract verification (tests/repository_contract/)       ║
║    • Local development without a running PostgreSQL instance             ║
║    • Deterministic regression testing and golden-file comparisons        ║
║    • Portable offline graph snapshots and debugging                      ║
║                                                                          ║
║  Production always uses BizRepositoryPG.                                ║
║  Any PR that instantiates BizRepositorySQLite outside of tests/ or      ║
║  local dev tooling must be rejected.                                     ║
╚══════════════════════════════════════════════════════════════════════════╝

Storage design
--------------
biz_entities       — nodes; dedup key: (kind, canonical_name)
biz_relations      — edges; dedup key: sha256[:16](source_uid+kind+target_uid)
biz_entity_history — append-only snapshot log
sequences          — per-kind UID counters
biz_fts            — FTS5 non-content table; rebuilt explicitly via rebuild_fts()
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Iterable, Optional

from ..domain import (
    BizEntity,
    BizEntityKind,
    BizRelation,
    BizRelationKind,
    UID_PREFIXES,
    canonicalize,
)
from ._base import BizRepository

# ── DDL ───────────────────────────────────────────────────────────────────────

_DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sequences (
    prefix   TEXT PRIMARY KEY,
    next_val INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS biz_entities (
    uid            TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    attributes     TEXT NOT NULL DEFAULT '{}',
    source         TEXT,
    confidence     REAL NOT NULL DEFAULT 1.0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_ent_kind      ON biz_entities(kind);
CREATE INDEX IF NOT EXISTS idx_biz_ent_canonical ON biz_entities(kind, canonical_name);
CREATE INDEX IF NOT EXISTS idx_biz_ent_name      ON biz_entities(name);

CREATE TABLE IF NOT EXISTS biz_relations (
    id          TEXT PRIMARY KEY,
    source_uid  TEXT NOT NULL REFERENCES biz_entities(uid) ON DELETE CASCADE,
    target_uid  TEXT NOT NULL REFERENCES biz_entities(uid) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    confidence  REAL NOT NULL DEFAULT 1.0,
    source      TEXT,
    attributes  TEXT NOT NULL DEFAULT '{}',
    valid_from  TEXT,
    valid_to    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_rel_source   ON biz_relations(source_uid);
CREATE INDEX IF NOT EXISTS idx_biz_rel_target   ON biz_relations(target_uid);
CREATE INDEX IF NOT EXISTS idx_biz_rel_kind     ON biz_relations(kind);
CREATE INDEX IF NOT EXISTS idx_biz_rel_src_kind ON biz_relations(source_uid, kind);
CREATE INDEX IF NOT EXISTS idx_biz_rel_tgt_kind ON biz_relations(target_uid, kind);

CREATE TABLE IF NOT EXISTS biz_entity_history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT NOT NULL,
    snapshot   TEXT NOT NULL,
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_biz_hist_uid ON biz_entity_history(uid);

CREATE VIRTUAL TABLE IF NOT EXISTS biz_fts USING fts5(
    uid UNINDEXED,
    name,
    canonical_name,
    attributes_text,
    tokenize='porter unicode61'
);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_relation_id(source_uid: str, kind: str, target_uid: str) -> str:
    h = hashlib.sha256(f"{source_uid}:{kind}:{target_uid}".encode()).hexdigest()
    return h[:16]


def _row_to_entity(row: sqlite3.Row) -> BizEntity:
    d = dict(row)
    return BizEntity(
        uid=d["uid"],
        kind=BizEntityKind(d["kind"]),
        name=d["name"],
        canonical_name=d["canonical_name"],
        attributes=json.loads(d.get("attributes") or "{}"),
        source=d.get("source"),
        confidence=d.get("confidence", 1.0),
        created_at=d.get("created_at"),
        updated_at=d.get("updated_at"),
    )


def _row_to_relation(row: sqlite3.Row) -> BizRelation:
    d = dict(row)
    return BizRelation(
        id=d["id"],
        source_uid=d["source_uid"],
        target_uid=d["target_uid"],
        kind=BizRelationKind(d["kind"]),
        confidence=d.get("confidence", 1.0),
        source=d.get("source"),
        attributes=json.loads(d.get("attributes") or "{}"),
        valid_from=d.get("valid_from"),
        valid_to=d.get("valid_to"),
        created_at=d.get("created_at"),
    )


# ── Implementation ────────────────────────────────────────────────────────────

class BizRepositorySQLite(BizRepository):
    """
    SQLite-backed BizRepository.

    REFERENCE IMPLEMENTATION ONLY.  Never instantiate in production code.

    Constructor
    -----------
    Pass either an open sqlite3.Connection:
        repo = BizRepositorySQLite(conn=sqlite3.connect(":memory:"))
        repo.setup_schema()

    The caller owns the connection lifecycle (open/close).
    setup_schema() must be called explicitly after construction.
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        if not isinstance(conn, sqlite3.Connection):
            raise TypeError(
                "BizRepositorySQLite requires a sqlite3.Connection. "
                "For production use, instantiate BizRepositoryPG instead."
            )
        conn.row_factory = sqlite3.Row
        self._conn = conn
        self._in_transaction: bool = False

    def _commit(self) -> None:
        """Commit only when not inside an explicit transaction() block."""
        if not self._in_transaction:
            self._conn.commit()

    def setup_schema(self) -> None:
        """Create all tables, indexes, and FTS virtual table."""
        self._conn.executescript(_DDL)
        self._conn.commit()

    # ── Internal UID allocation (not on the public interface) ─────────────────

    def _next_uid(self, kind: BizEntityKind) -> str:
        """Atomically allocate the next UID for the given entity kind."""
        prefix = UID_PREFIXES[kind.value]
        self._conn.execute(
            "INSERT INTO sequences(prefix, next_val) VALUES(?,1) "
            "ON CONFLICT(prefix) DO UPDATE SET next_val = next_val + 1",
            (prefix,),
        )
        self._commit()
        val = self._conn.execute(
            "SELECT next_val FROM sequences WHERE prefix = ?", (prefix,)
        ).fetchone()[0]
        return f"{prefix}-{val:08d}"

    # ── BizRepository interface ───────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Generator:
        """Opaque atomic context manager.  Does not expose the connection."""
        self._in_transaction = True
        try:
            yield
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise
        finally:
            self._in_transaction = False

    def put_entity(
        self,
        kind: BizEntityKind,
        name: str,
        attributes: Optional[dict] = None,
        source: Optional[str] = None,
        confidence: float = 1.0,
        uid: Optional[str] = None,
        write_history: bool = True,
    ) -> tuple[BizEntity, bool]:
        canonical = canonicalize(name)
        now = _now()
        attrs = attributes or {}

        existing = self.find_by_canonical(kind, canonical)
        created = existing is None

        if created:
            if uid is None:
                uid = self._next_uid(kind)
            entity = BizEntity(
                uid=uid,
                kind=kind,
                name=name,
                canonical_name=canonical,
                attributes=attrs,
                source=source,
                confidence=confidence,
                created_at=now,
                updated_at=now,
            )
            self._conn.execute(
                """INSERT INTO biz_entities
                   (uid, kind, name, canonical_name, attributes,
                    source, confidence, created_at, updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    entity.uid, entity.kind.value, entity.name,
                    entity.canonical_name, json.dumps(attrs),
                    source, confidence, now, now,
                ),
            )
            self._commit()
        else:
            merged = {**existing.attributes, **attrs}
            entity = BizEntity(
                uid=existing.uid,
                kind=kind,
                name=name,
                canonical_name=canonical,
                attributes=merged,
                source=source or existing.source,
                confidence=max(confidence, existing.confidence),
                created_at=existing.created_at,
                updated_at=now,
            )
            self._conn.execute(
                """UPDATE biz_entities
                   SET name=?, attributes=?, source=?, confidence=?, updated_at=?
                   WHERE uid=?""",
                (
                    name, json.dumps(merged),
                    entity.source, entity.confidence, now, entity.uid,
                ),
            )
            self._commit()

        if write_history:
            self._append_history(entity, source)

        return entity, created

    def put_relation(
        self,
        source_uid: str,
        kind: BizRelationKind,
        target_uid: str,
        confidence: float = 1.0,
        source: Optional[str] = None,
        attributes: Optional[dict] = None,
        valid_from: Optional[str] = None,
        valid_to: Optional[str] = None,
    ) -> tuple[BizRelation, bool]:
        rel_id = _make_relation_id(source_uid, kind.value, target_uid)
        now = _now()
        attrs = attributes or {}

        existing = self._conn.execute(
            "SELECT * FROM biz_relations WHERE id = ?", (rel_id,)
        ).fetchone()
        created = existing is None

        self._conn.execute(
            """INSERT INTO biz_relations
               (id, source_uid, target_uid, kind, confidence, source,
                attributes, valid_from, valid_to, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                   confidence = MAX(confidence, excluded.confidence),
                   source     = COALESCE(excluded.source, source),
                   valid_to   = excluded.valid_to""",
            (
                rel_id, source_uid, target_uid, kind.value,
                confidence, source, json.dumps(attrs),
                valid_from, valid_to, now,
            ),
        )
        self._commit()

        stored = self._conn.execute(
            "SELECT confidence, source FROM biz_relations WHERE id = ?", (rel_id,)
        ).fetchone()
        stored_confidence = stored[0] if stored else confidence
        stored_source = stored[1] if stored else source

        rel = BizRelation(
            id=rel_id,
            source_uid=source_uid,
            target_uid=target_uid,
            kind=kind,
            confidence=stored_confidence,
            source=stored_source,
            attributes=attrs,
            valid_from=valid_from,
            valid_to=valid_to,
            created_at=now,
        )
        return rel, created

    def bulk_put_entities(
        self,
        records: Iterable[dict],
        source: Optional[str] = None,
        write_history: bool = False,
    ) -> tuple[int, int]:
        created = updated = 0
        for rec in records:
            _, was_created = self.put_entity(
                kind=BizEntityKind(rec["kind"]),
                name=rec["name"],
                attributes=rec.get("attributes"),
                source=source or rec.get("source"),
                confidence=rec.get("confidence", 1.0),
                uid=rec.get("uid"),
                write_history=write_history,
            )
            if was_created:
                created += 1
            else:
                updated += 1
        return created, updated

    def get(self, uid: str) -> Optional[BizEntity]:
        row = self._conn.execute(
            "SELECT * FROM biz_entities WHERE uid = ?", (uid,)
        ).fetchone()
        return _row_to_entity(row) if row else None

    def find_by_canonical(
        self,
        kind: BizEntityKind,
        canonical_name: str,
    ) -> Optional[BizEntity]:
        row = self._conn.execute(
            "SELECT * FROM biz_entities WHERE kind = ? AND canonical_name = ?",
            (kind.value, canonical_name),
        ).fetchone()
        return _row_to_entity(row) if row else None

    def find(
        self,
        kind: Optional[BizEntityKind] = None,
        name_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BizEntity]:
        clauses, params = [], []
        if kind:
            clauses.append("kind = ?")
            params.append(kind.value)
        if name_like:
            clauses.append("canonical_name LIKE ?")
            params.append(f"%{canonicalize(name_like)}%")
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]
        rows = self._conn.execute(
            f"SELECT * FROM biz_entities {where} ORDER BY name LIMIT ? OFFSET ?",
            params,
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def find_by_attribute(
        self,
        key: str,
        value: object,
        kind: Optional[BizEntityKind] = None,
        limit: int = 10,
    ) -> list[BizEntity]:
        clauses = ["json_extract(attributes, ?) = ?"]
        params: list = [f"$.{key}", value]
        if kind is not None:
            clauses.append("kind = ?")
            params.append(kind.value)
        params.append(limit)
        where = "WHERE " + " AND ".join(clauses)
        rows = self._conn.execute(
            f"SELECT * FROM biz_entities {where} LIMIT ?",
            params,
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def search_fts(self, query: str, limit: int = 20) -> list[BizEntity]:
        import re as _re
        words = [w for w in _re.split(r"[^\w]+", query.strip()) if w]
        if not words:
            return []
        fts_q = " OR ".join(f'"{w}"*' for w in words)
        try:
            fts_rows = self._conn.execute(
                "SELECT uid FROM biz_fts WHERE biz_fts MATCH ? "
                "ORDER BY bm25(biz_fts) LIMIT ?",
                (fts_q, limit),
            ).fetchall()
        except Exception:
            return []
        uids = [r[0] for r in fts_rows]
        if not uids:
            return []
        placeholders = ",".join("?" * len(uids))
        rows = self._conn.execute(
            f"SELECT * FROM biz_entities WHERE uid IN ({placeholders})", uids
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def get_neighbors(
        self,
        uid: str,
        direction: str = "both",
        kinds: Optional[list[BizRelationKind]] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[tuple[BizRelation, BizEntity]]:
        kind_filter = ""
        kparams: list = []
        if kinds:
            placeholders = ",".join("?" * len(kinds))
            kind_filter = f"AND r.kind IN ({placeholders})"
            kparams = [k.value for k in kinds]
        active_filter = "AND r.valid_to IS NULL" if active_only else ""

        results: list[tuple[BizRelation, BizEntity]] = []

        def _fetch(uid_col: str, join_col: str) -> None:
            q = f"""
            SELECT r.id as r_id, r.source_uid as r_source_uid,
                   r.target_uid as r_target_uid, r.kind as r_kind,
                   r.confidence as r_conf, r.source as r_source,
                   r.attributes as r_attrs, r.valid_from, r.valid_to,
                   r.created_at as r_created_at,
                   e.uid as e_uid, e.kind as e_kind, e.name as e_name,
                   e.canonical_name as e_canonical, e.attributes as e_attrs,
                   e.source as e_source, e.confidence as e_conf,
                   e.created_at as e_created_at, e.updated_at as e_updated_at
            FROM biz_relations r
            JOIN biz_entities e ON e.uid = r.{join_col}
            WHERE r.{uid_col} = ? {kind_filter} {active_filter}
            LIMIT ?
            """
            rows = self._conn.execute(q, [uid] + kparams + [limit]).fetchall()
            for row in rows:
                d = dict(row)
                rel = BizRelation(
                    id=d["r_id"],
                    source_uid=d["r_source_uid"],
                    target_uid=d["r_target_uid"],
                    kind=BizRelationKind(d["r_kind"]),
                    confidence=d["r_conf"],
                    source=d["r_source"],
                    attributes=json.loads(d["r_attrs"] or "{}"),
                    valid_from=d.get("valid_from"),
                    valid_to=d.get("valid_to"),
                    created_at=d.get("r_created_at"),
                )
                ent = BizEntity(
                    uid=d["e_uid"],
                    kind=BizEntityKind(d["e_kind"]),
                    name=d["e_name"],
                    canonical_name=d["e_canonical"],
                    attributes=json.loads(d["e_attrs"] or "{}"),
                    source=d["e_source"],
                    confidence=d["e_conf"],
                    created_at=d.get("e_created_at"),
                    updated_at=d.get("e_updated_at"),
                )
                results.append((rel, ent))

        if direction in ("out", "both"):
            _fetch("source_uid", "target_uid")
        if direction in ("in", "both"):
            _fetch("target_uid", "source_uid")

        return results

    def get_relations_between(
        self,
        source_uid: str,
        target_uid: str,
    ) -> list[BizRelation]:
        rows = self._conn.execute(
            "SELECT * FROM biz_relations WHERE source_uid=? AND target_uid=?",
            (source_uid, target_uid),
        ).fetchall()
        return [_row_to_relation(r) for r in rows]

    def entity_history(self, uid: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT snapshot, changed_by, changed_at FROM biz_entity_history "
            "WHERE uid = ? ORDER BY id ASC",
            (uid,),
        ).fetchall()
        return [
            {
                "changed_at": r["changed_at"],
                "changed_by": r["changed_by"],
                "snapshot": json.loads(r["snapshot"]),
            }
            for r in rows
        ]

    def get_stats(self) -> dict:
        entity_count = self._conn.execute(
            "SELECT COUNT(*) FROM biz_entities"
        ).fetchone()[0]
        relation_count = self._conn.execute(
            "SELECT COUNT(*) FROM biz_relations"
        ).fetchone()[0]
        kind_rows = self._conn.execute(
            "SELECT kind, COUNT(*) as c FROM biz_entities GROUP BY kind"
        ).fetchall()
        history_count = self._conn.execute(
            "SELECT COUNT(*) FROM biz_entity_history"
        ).fetchone()[0]
        seq_rows = self._conn.execute(
            "SELECT prefix, next_val FROM sequences ORDER BY prefix"
        ).fetchall()
        return {
            "entities": entity_count,
            "relations": relation_count,
            "history_entries": history_count,
            "by_kind": {r["kind"]: r["c"] for r in kind_rows},
            "sequences": {r["prefix"]: r["next_val"] - 1 for r in seq_rows},
        }

    def rebuild_fts(self) -> None:
        """Rebuild FTS5 non-content table.  Call after bulk_put_entities()."""
        self._conn.execute("DELETE FROM biz_fts")
        self._conn.execute(
            """INSERT INTO biz_fts(uid, name, canonical_name, attributes_text)
               SELECT uid, name, canonical_name, attributes FROM biz_entities"""
        )
        self._commit()

    # ── Private helpers ───────────────────────────────────────────────────────

    def _append_history(self, entity: BizEntity, changed_by: Optional[str]) -> None:
        snapshot = json.dumps(entity.to_full())
        self._conn.execute(
            "INSERT INTO biz_entity_history(uid, snapshot, changed_by, changed_at) "
            "VALUES (?,?,?,?)",
            (entity.uid, snapshot, changed_by, _now()),
        )
        self._commit()
