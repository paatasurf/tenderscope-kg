"""
TenderScope Knowledge Graph — PostgreSQL production implementation.

╔══════════════════════════════════════════════════════════════════════════╗
║  PRODUCTION IMPLEMENTATION                                               ║
║                                                                          ║
║  All graph tables live in the 'graph' schema inside the same            ║
║  PostgreSQL database as the operational data.  No second database.      ║
║  No synchronization jobs.  No rebuild pipelines.                        ║
║                                                                          ║
║  FTS is maintained live via a GENERATED ALWAYS tsvector column +        ║
║  GIN index.  rebuild_fts() is a documented no-op.                       ║
║                                                                          ║
║  Prerequisites:                                                          ║
║    pip install tenderscope-kg[postgres]                                  ║
║    python -m tenderscope_kg.repository._postgres --migrate <DSN>        ║
╚══════════════════════════════════════════════════════════════════════════╝

Constructor
-----------
BizRepositoryPG accepts either:

  1. A connection factory (callable returning a psycopg2 connection):
       repo = BizRepositoryPG(conn_factory=lambda: psycopg2.connect(dsn))
       repo.setup_schema()

  2. A pre-opened psycopg2 connection (for testing / single-thread use):
       conn = psycopg2.connect(dsn)
       repo = BizRepositoryPG(conn=conn)
       repo.setup_schema()

The caller owns the connection lifecycle (open/close).
setup_schema() must be called explicitly after construction.
"""

from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Callable, Generator, Optional

from ..domain import (
    UID_PREFIXES,
    BizEntity,
    BizEntityKind,
    BizRelation,
    BizRelationKind,
    canonicalize,
)
from ._base import BizRepository

# ── Schema DDL ────────────────────────────────────────────────────────────────

_DDL = """
CREATE SCHEMA IF NOT EXISTS graph;

CREATE TABLE IF NOT EXISTS graph.graph_uid_map (
    prefix   TEXT PRIMARY KEY,
    next_val BIGINT NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS graph.biz_entities (
    uid            TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    attributes     JSONB NOT NULL DEFAULT '{}'::jsonb,
    source         TEXT,
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at     TEXT NOT NULL,
    updated_at     TEXT NOT NULL,
    fts_vector     TSVECTOR GENERATED ALWAYS AS (
        to_tsvector('english',
            coalesce(name, '') || ' ' ||
            coalesce(canonical_name, '') || ' ' ||
            coalesce(attributes::text, '')
        )
    ) STORED
);

CREATE INDEX IF NOT EXISTS idx_pg_biz_ent_kind
    ON graph.biz_entities (kind);
CREATE INDEX IF NOT EXISTS idx_pg_biz_ent_canonical
    ON graph.biz_entities (kind, canonical_name);
CREATE INDEX IF NOT EXISTS idx_pg_biz_ent_name
    ON graph.biz_entities (name);
CREATE INDEX IF NOT EXISTS idx_pg_biz_ent_fts
    ON graph.biz_entities USING GIN (fts_vector);

CREATE TABLE IF NOT EXISTS graph.biz_relations (
    id          TEXT PRIMARY KEY,
    source_uid  TEXT NOT NULL REFERENCES graph.biz_entities(uid) ON DELETE CASCADE,
    target_uid  TEXT NOT NULL REFERENCES graph.biz_entities(uid) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    source      TEXT,
    attributes  JSONB NOT NULL DEFAULT '{}'::jsonb,
    valid_from  TEXT,
    valid_to    TEXT,
    created_at  TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pg_biz_rel_source
    ON graph.biz_relations (source_uid);
CREATE INDEX IF NOT EXISTS idx_pg_biz_rel_target
    ON graph.biz_relations (target_uid);
CREATE INDEX IF NOT EXISTS idx_pg_biz_rel_kind
    ON graph.biz_relations (kind);
CREATE INDEX IF NOT EXISTS idx_pg_biz_rel_src_kind
    ON graph.biz_relations (source_uid, kind);
CREATE INDEX IF NOT EXISTS idx_pg_biz_rel_tgt_kind
    ON graph.biz_relations (target_uid, kind);

CREATE TABLE IF NOT EXISTS graph.biz_entity_history (
    id         BIGSERIAL PRIMARY KEY,
    uid        TEXT NOT NULL,
    snapshot   JSONB NOT NULL,
    changed_by TEXT,
    changed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pg_biz_hist_uid
    ON graph.biz_entity_history (uid);
"""


# ── Helpers ───────────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _make_relation_id(source_uid: str, kind: str, target_uid: str) -> str:
    h = hashlib.sha256(f"{source_uid}:{kind}:{target_uid}".encode()).hexdigest()
    return h[:16]


def _row_to_entity(row: dict) -> BizEntity:
    attrs = row["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return BizEntity(
        uid=row["uid"],
        kind=BizEntityKind(row["kind"]),
        name=row["name"],
        canonical_name=row["canonical_name"],
        attributes=attrs or {},
        source=row.get("source"),
        confidence=row.get("confidence", 1.0),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _row_to_relation(row: dict) -> BizRelation:
    attrs = row["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    return BizRelation(
        id=row["id"],
        source_uid=row["source_uid"],
        target_uid=row["target_uid"],
        kind=BizRelationKind(row["kind"]),
        confidence=row.get("confidence", 1.0),
        source=row.get("source"),
        attributes=attrs or {},
        valid_from=row.get("valid_from"),
        valid_to=row.get("valid_to"),
        created_at=row.get("created_at"),
    )


# ── Implementation ────────────────────────────────────────────────────────────


class BizRepositoryPG(BizRepository):
    """
    PostgreSQL-backed BizRepository.

    PRODUCTION implementation.

    Graph tables live in the 'graph' schema.  FTS is maintained live via a
    GENERATED ALWAYS tsvector column backed by a GIN index — rebuild_fts()
    is a documented no-op.

    Usage:
        import psycopg2
        from tenderscope_kg.repository import create_repository

        repo = create_repository(
            "postgres",
            conn_factory=lambda: psycopg2.connect(os.environ["PG_DSN"]),
        )
        repo.setup_schema()
    """

    def __init__(
        self,
        conn_factory: Optional[Callable] = None,
        conn=None,
    ) -> None:
        if conn_factory is None and conn is None:
            raise TypeError(
                "BizRepositoryPG requires either conn_factory= or conn=. "
                "Example: BizRepositoryPG(conn_factory=lambda: psycopg2.connect(dsn))"
            )
        self._conn_factory = conn_factory
        self._fixed_conn = conn
        self._active_conn = None  # set inside transaction() block

    # ── Connection helpers ────────────────────────────────────────────────────

    def _get_conn(self):
        if self._active_conn is not None:
            return self._active_conn
        if self._conn_factory is not None:
            return self._conn_factory()
        return self._fixed_conn

    def _cursor(self, conn=None):
        try:
            from psycopg2.extras import RealDictCursor
        except ImportError as exc:
            raise ImportError(
                "psycopg2 is required for BizRepositoryPG. Install it with: pip install tenderscope-kg[postgres]"
            ) from exc
        c = conn or self._get_conn()
        return c.cursor(cursor_factory=RealDictCursor)

    def _commit(self, conn) -> None:
        """Commit only when not inside an explicit transaction() block."""
        if self._active_conn is None:
            conn.commit()

    # ── Schema ────────────────────────────────────────────────────────────────

    def setup_schema(self) -> None:
        """Create all tables, indexes, and sequences.  Safe to re-run."""
        conn = self._get_conn()
        # psycopg2 cursor.execute() only accepts one statement at a time.
        # Split on semicolons and execute each non-empty statement individually.
        statements = [s.strip() for s in _DDL.split(";") if s.strip()]
        with conn.cursor() as cur:
            for stmt in statements:
                cur.execute(stmt)
        conn.commit()

    # ── UID allocation ────────────────────────────────────────────────────────

    def _next_uid(self, kind: BizEntityKind, conn=None) -> str:
        prefix = UID_PREFIXES[kind.value]
        c = conn or self._get_conn()
        with c.cursor() as cur:
            cur.execute(
                """
                INSERT INTO graph.graph_uid_map (prefix, next_val)
                VALUES (%s, 1)
                ON CONFLICT (prefix) DO UPDATE
                    SET next_val = graph.graph_uid_map.next_val + 1
                RETURNING next_val
                """,
                (prefix,),
            )
            val = cur.fetchone()[0]
        self._commit(c)
        return f"{prefix}-{val:08d}"

    # ── Transactions ──────────────────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Generator:
        """
        Opaque atomic context manager.

        All repository calls within the block share one connection.
        COMMIT on clean exit, ROLLBACK on exception.
        Yields nothing — callers must not interact with storage primitives.
        """
        conn = self._conn_factory() if self._conn_factory is not None else self._fixed_conn
        conn.autocommit = False
        self._active_conn = conn
        try:
            yield
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            self._active_conn = None
            if self._conn_factory is not None:
                conn.close()

    # ── Write: entities ───────────────────────────────────────────────────────

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
        conn = self._get_conn()
        canonical = canonicalize(name)
        attrs = attributes or {}
        now = _now()

        with self._cursor(conn) as cur:
            if kind == BizEntityKind.COMPANY and attrs.get("scraper_id") is not None:
                cur.execute(
                    """
                    SELECT uid, attributes, source, confidence, created_at
                    FROM graph.biz_entities
                    WHERE kind = %s AND attributes @> %s::jsonb
                    LIMIT 1
                    """,
                    (kind.value, json.dumps({"scraper_id": attrs["scraper_id"]})),
                )
            else:
                cur.execute(
                    """
                    SELECT uid, attributes, source, confidence, created_at
                    FROM graph.biz_entities
                    WHERE kind = %s AND canonical_name = %s
                    """,
                    (kind.value, canonical),
                )
            existing = cur.fetchone()
            created = existing is None

            if created:
                if uid is None:
                    uid = self._next_uid(kind, conn)
                cur.execute(
                    """
                    INSERT INTO graph.biz_entities
                        (uid, kind, name, canonical_name, attributes,
                         source, confidence, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s::jsonb, %s, %s, %s, %s)
                    """,
                    (
                        uid,
                        kind.value,
                        name,
                        canonical,
                        json.dumps(attrs),
                        source,
                        confidence,
                        now,
                        now,
                    ),
                )
                entity = BizEntity(
                    uid=uid,
                    kind=kind,
                    name=name,
                    canonical_name=canonical,
                    attributes=dict(attrs),
                    source=source,
                    confidence=confidence,
                    created_at=now,
                    updated_at=now,
                )
            else:
                ex = dict(existing)
                ex_attrs = ex["attributes"]
                if isinstance(ex_attrs, str):
                    ex_attrs = json.loads(ex_attrs)
                merged = {**ex_attrs, **attrs}
                new_confidence = max(confidence, ex["confidence"])
                new_source = source or ex["source"]
                cur.execute(
                    """
                    UPDATE graph.biz_entities
                    SET name = %s,
                        canonical_name = %s,
                        attributes = %s::jsonb,
                        source = %s,
                        confidence = %s,
                        updated_at = %s
                    WHERE uid = %s
                    """,
                    (
                        name,
                        canonical,
                        json.dumps(merged),
                        new_source,
                        new_confidence,
                        now,
                        ex["uid"],
                    ),
                )
                entity = BizEntity(
                    uid=ex["uid"],
                    kind=kind,
                    name=name,
                    canonical_name=canonical,
                    attributes=merged,
                    source=new_source,
                    confidence=new_confidence,
                    created_at=ex["created_at"],
                    updated_at=now,
                )

            if write_history:
                cur.execute(
                    """
                    INSERT INTO graph.biz_entity_history
                        (uid, snapshot, changed_by, changed_at)
                    VALUES (%s, %s::jsonb, %s, %s)
                    """,
                    (entity.uid, json.dumps(entity.to_full()), source, now),
                )

        self._commit(conn)
        return entity, created

    # ── Write: relations ──────────────────────────────────────────────────────

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
        conn = self._get_conn()
        rel_id = _make_relation_id(source_uid, kind.value, target_uid)
        attrs = attributes or {}
        now = _now()

        with self._cursor(conn) as cur:
            cur.execute(
                "SELECT id FROM graph.biz_relations WHERE id = %s",
                (rel_id,),
            )
            created = cur.fetchone() is None

            cur.execute(
                """
                INSERT INTO graph.biz_relations
                    (id, source_uid, target_uid, kind, confidence, source,
                     attributes, valid_from, valid_to, created_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    confidence = GREATEST(graph.biz_relations.confidence,
                                          EXCLUDED.confidence),
                    source     = COALESCE(EXCLUDED.source,
                                          graph.biz_relations.source),
                    valid_to   = EXCLUDED.valid_to
                """,
                (
                    rel_id,
                    source_uid,
                    target_uid,
                    kind.value,
                    confidence,
                    source,
                    json.dumps(attrs),
                    valid_from,
                    valid_to,
                    now,
                ),
            )
            cur.execute(
                "SELECT confidence, source FROM graph.biz_relations WHERE id = %s",
                (rel_id,),
            )
            stored = cur.fetchone()
            stored_confidence = stored["confidence"] if stored else confidence
            stored_source = stored["source"] if stored else source

        self._commit(conn)

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

    # ── Write: bulk ───────────────────────────────────────────────────────────

    def bulk_put_entities(
        self,
        records,
        source: Optional[str] = None,
        write_history: bool = False,
    ) -> tuple[int, int]:
        created_count = updated_count = 0
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
                created_count += 1
            else:
                updated_count += 1
        return created_count, updated_count

    # ── Read: single entity ───────────────────────────────────────────────────

    def get(self, uid: str) -> Optional[BizEntity]:
        conn = self._get_conn()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT uid, kind, name, canonical_name, attributes,
                       source, confidence, created_at, updated_at
                FROM graph.biz_entities
                WHERE uid = %s
                """,
                (uid,),
            )
            row = cur.fetchone()
        return _row_to_entity(dict(row)) if row else None

    def find_by_canonical(self, kind: BizEntityKind, canonical_name: str) -> Optional[BizEntity]:
        conn = self._get_conn()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT uid, kind, name, canonical_name, attributes,
                       source, confidence, created_at, updated_at
                FROM graph.biz_entities
                WHERE kind = %s AND canonical_name = %s
                """,
                (kind.value, canonical_name),
            )
            row = cur.fetchone()
        return _row_to_entity(dict(row)) if row else None

    # ── Read: filtered listing ────────────────────────────────────────────────

    def find(
        self,
        kind: Optional[BizEntityKind] = None,
        name_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BizEntity]:
        conn = self._get_conn()
        clauses: list[str] = []
        params: list = []

        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind.value)
        if name_like is not None:
            clauses.append("canonical_name LIKE %s")
            params.append(f"%{canonicalize(name_like)}%")

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params += [limit, offset]

        with self._cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT uid, kind, name, canonical_name, attributes,
                       source, confidence, created_at, updated_at
                FROM graph.biz_entities
                {where}
                ORDER BY name ASC
                LIMIT %s OFFSET %s
                """,
                params,
            )
            rows = cur.fetchall()
        return [_row_to_entity(dict(r)) for r in rows]

    def find_by_attribute(
        self,
        key: str,
        value: object,
        kind: Optional[BizEntityKind] = None,
        limit: int = 10,
    ) -> list[BizEntity]:
        conn = self._get_conn()
        clauses: list[str] = ["attributes @> %s::jsonb"]
        params: list = [json.dumps({key: value})]
        if kind is not None:
            clauses.append("kind = %s")
            params.append(kind.value)
        params.append(limit)
        where = "WHERE " + " AND ".join(clauses)
        with self._cursor(conn) as cur:
            cur.execute(
                f"""
                SELECT uid, kind, name, canonical_name, attributes,
                       source, confidence, created_at, updated_at
                FROM graph.biz_entities
                {where}
                LIMIT %s
                """,
                params,
            )
            rows = cur.fetchall()
        return [_row_to_entity(dict(r)) for r in rows]

    # ── Read: full-text search ────────────────────────────────────────────────

    def search_fts(self, query: str, limit: int = 20) -> list[BizEntity]:
        if not query or not query.strip():
            return []
        words = [w for w in query.strip().split() if w]
        if not words:
            return []
        # websearch_to_tsquery (PG 11+) safely handles arbitrary user input,
        # stop-words, special characters, and single-letter words without
        # raising a syntax error.  It also supports prefix matching via :*.
        # ts_rank uses the same query expression for scoring.
        conn = self._get_conn()
        try:
            with self._cursor(conn) as cur:
                cur.execute(
                    """
                    SELECT uid, kind, name, canonical_name, attributes,
                           source, confidence, created_at, updated_at,
                           ts_rank(fts_vector,
                               websearch_to_tsquery('english', %s)) AS rank
                    FROM graph.biz_entities
                    WHERE fts_vector @@ websearch_to_tsquery('english', %s)
                    ORDER BY rank DESC
                    LIMIT %s
                    """,
                    (query, query, limit),
                )
                rows = cur.fetchall()
        except Exception:
            return []
        return [_row_to_entity(dict(r)) for r in rows]

    # ── Read: graph traversal ─────────────────────────────────────────────────

    def get_neighbors(
        self,
        uid: str,
        direction: str = "both",
        kinds: Optional[list[BizRelationKind]] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[tuple[BizRelation, BizEntity]]:
        conn = self._get_conn()
        kind_filter = ""
        kind_params: list = []
        if kinds:
            placeholders = ",".join(["%s"] * len(kinds))
            kind_filter = f"AND r.kind IN ({placeholders})"
            kind_params = [k.value for k in kinds]
        active_filter = "AND r.valid_to IS NULL" if active_only else ""

        results: list[tuple[BizRelation, BizEntity]] = []

        def _fetch(uid_col: str, join_col: str) -> None:
            with self._cursor(conn) as cur:
                cur.execute(
                    f"""
                    SELECT
                        r.id          AS r_id,
                        r.source_uid  AS r_source_uid,
                        r.target_uid  AS r_target_uid,
                        r.kind        AS r_kind,
                        r.confidence  AS r_conf,
                        r.source      AS r_source,
                        r.attributes  AS r_attrs,
                        r.valid_from  AS r_valid_from,
                        r.valid_to    AS r_valid_to,
                        r.created_at  AS r_created_at,
                        e.uid         AS e_uid,
                        e.kind        AS e_kind,
                        e.name        AS e_name,
                        e.canonical_name AS e_canonical,
                        e.attributes  AS e_attrs,
                        e.source      AS e_source,
                        e.confidence  AS e_conf,
                        e.created_at  AS e_created_at,
                        e.updated_at  AS e_updated_at
                    FROM graph.biz_relations r
                    JOIN graph.biz_entities  e ON e.uid = r.{join_col}
                    WHERE r.{uid_col} = %s
                      {kind_filter}
                      {active_filter}
                    LIMIT %s
                    """,
                    [uid] + kind_params + [limit],
                )
                rows = cur.fetchall()
            for row in rows:
                d = dict(row)
                r_attrs = d["r_attrs"]
                if isinstance(r_attrs, str):
                    r_attrs = json.loads(r_attrs)
                e_attrs = d["e_attrs"]
                if isinstance(e_attrs, str):
                    e_attrs = json.loads(e_attrs)
                rel = BizRelation(
                    id=d["r_id"],
                    source_uid=d["r_source_uid"],
                    target_uid=d["r_target_uid"],
                    kind=BizRelationKind(d["r_kind"]),
                    confidence=d["r_conf"],
                    source=d["r_source"],
                    attributes=r_attrs or {},
                    valid_from=d.get("r_valid_from"),
                    valid_to=d.get("r_valid_to"),
                    created_at=d.get("r_created_at"),
                )
                ent = BizEntity(
                    uid=d["e_uid"],
                    kind=BizEntityKind(d["e_kind"]),
                    name=d["e_name"],
                    canonical_name=d["e_canonical"],
                    attributes=e_attrs or {},
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
        conn = self._get_conn()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT id, source_uid, target_uid, kind, confidence, source,
                       attributes, valid_from, valid_to, created_at
                FROM graph.biz_relations
                WHERE source_uid = %s AND target_uid = %s
                """,
                (source_uid, target_uid),
            )
            rows = cur.fetchall()
        return [_row_to_relation(dict(r)) for r in rows]

    # ── Read: history ─────────────────────────────────────────────────────────

    def entity_history(self, uid: str) -> list[dict]:
        conn = self._get_conn()
        with self._cursor(conn) as cur:
            cur.execute(
                """
                SELECT snapshot, changed_by, changed_at
                FROM graph.biz_entity_history
                WHERE uid = %s
                ORDER BY id ASC
                """,
                (uid,),
            )
            rows = cur.fetchall()
        result = []
        for r in rows:
            snap = r["snapshot"]
            if isinstance(snap, str):
                snap = json.loads(snap)
            result.append(
                {
                    "changed_at": r["changed_at"],
                    "changed_by": r["changed_by"],
                    "snapshot": snap,
                }
            )
        return result

    # ── Read: stats ───────────────────────────────────────────────────────────

    def get_stats(self) -> dict:
        conn = self._get_conn()
        with self._cursor(conn) as cur:
            cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entities")
            entity_count = cur.fetchone()["cnt"]
            cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_relations")
            relation_count = cur.fetchone()["cnt"]
            cur.execute("SELECT kind, COUNT(*) AS cnt FROM graph.biz_entities GROUP BY kind")
            by_kind = {r["kind"]: r["cnt"] for r in cur.fetchall()}
        return {
            "entities": entity_count,
            "relations": relation_count,
            "by_kind": by_kind,
        }

    # ── FTS maintenance ───────────────────────────────────────────────────────

    def rebuild_fts(self) -> None:
        """
        No-op.  PostgreSQL maintains the tsvector column live via a GENERATED
        ALWAYS expression backed by a GIN index.  Callers may call this method
        without error; it does nothing.
        """


# ── CLI migrate helper ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    if len(sys.argv) < 3 or sys.argv[1] != "--migrate":
        print("Usage: python -m tenderscope_kg.repository._postgres --migrate <DSN>")
        sys.exit(1)
    try:
        import psycopg2
    except ImportError:
        print("psycopg2 not installed. Run: pip install tenderscope-kg[postgres]")
        sys.exit(1)
    dsn = sys.argv[2]
    conn = psycopg2.connect(dsn)
    repo = BizRepositoryPG(conn=conn)
    repo.setup_schema()
    conn.close()
    print("PostgreSQL graph schema created/verified successfully.")
