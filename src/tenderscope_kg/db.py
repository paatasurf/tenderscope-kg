"""
SQLite-backed graph database layer.
Schema uses three normalized tables: entities, relations, and metadata.
WAL mode is enabled for concurrent readers.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Iterable, Optional

from .models import Entity, EntityKind, Relation, RelationKind

SCHEMA_VERSION = "1"

DDL = """
PRAGMA journal_mode = WAL;
PRAGMA synchronous  = NORMAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS entities (
    id             TEXT PRIMARY KEY,
    kind           TEXT NOT NULL,
    name           TEXT NOT NULL,
    qualified_name TEXT NOT NULL,
    file_path      TEXT NOT NULL,
    line_start     INTEGER NOT NULL,
    line_end       INTEGER NOT NULL,
    signature      TEXT,
    docstring      TEXT,
    language       TEXT,
    extra          TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS relations (
    id         TEXT PRIMARY KEY,
    source_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    target_id  TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    file_path  TEXT,
    line       INTEGER,
    weight     REAL NOT NULL DEFAULT 1.0,
    extra      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_entities_file     ON entities(file_path);
CREATE INDEX IF NOT EXISTS idx_entities_kind     ON entities(kind);
CREATE INDEX IF NOT EXISTS idx_entities_qname    ON entities(qualified_name);
CREATE INDEX IF NOT EXISTS idx_relations_source  ON relations(source_id);
CREATE INDEX IF NOT EXISTS idx_relations_target  ON relations(target_id);
CREATE INDEX IF NOT EXISTS idx_relations_kind    ON relations(kind);

CREATE VIRTUAL TABLE IF NOT EXISTS entities_fts USING fts5(
    name, qualified_name, docstring, signature,
    content='entities', content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS entities_fts_insert AFTER INSERT ON entities BEGIN
    INSERT INTO entities_fts(rowid, name, qualified_name, docstring, signature)
    VALUES (new.rowid, new.name, new.qualified_name, new.docstring, new.signature);
END;

CREATE TRIGGER IF NOT EXISTS entities_fts_delete AFTER DELETE ON entities BEGIN
    INSERT INTO entities_fts(entities_fts, rowid, name, qualified_name, docstring, signature)
    VALUES ('delete', old.rowid, old.name, old.qualified_name, old.docstring, old.signature);
END;
"""


def make_entity_id(kind: EntityKind, qualified_name: str) -> str:
    h = hashlib.sha256(f"{kind.value}:{qualified_name}".encode()).hexdigest()
    return h[:16]


def make_relation_id(source_id: str, kind: RelationKind, target_id: str) -> str:
    h = hashlib.sha256(f"{source_id}:{kind.value}:{target_id}".encode()).hexdigest()
    return h[:16]


class GraphDB:
    #: Injected BizRepository instance.  Set to a concrete repo by the
    #: composition root before any engine accesses it.  The class-level
    #: sentinel (None) allows unittest.mock.patch.object to locate the
    #: attribute name on the class even before instantiation.
    biz_repo = None

    def __init__(self, db_path: Path, biz_repo=None):
        """Create a GraphDB instance.

        Args:
            db_path:  Path to the SQLite file for the code-graph tables.
            biz_repo: An already-constructed :class:`BizRepository` instance.
                      Injected by the composition root (CLI/MCP server) via
                      ``create_repository()``.  ``None`` is only valid when
                      the caller never accesses ``biz_repo`` (e.g. pure
                      code-graph indexing/query commands).
        """
        self.db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None
        self.biz_repo = biz_repo

    def connect(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(DDL)
        self._conn.commit()

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    @contextmanager
    def transaction(self) -> Generator[sqlite3.Connection, None, None]:
        assert self._conn, "Database not connected"
        try:
            yield self._conn
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    # ── Write operations ──────────────────────────────────────────────────

    def upsert_entities(self, entities: Iterable[Entity]) -> int:
        sql = """
        INSERT OR REPLACE INTO entities
            (id, kind, name, qualified_name, file_path, line_start, line_end,
             signature, docstring, language, extra)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                e.id,
                e.kind.value,
                e.name,
                e.qualified_name,
                e.file_path,
                e.line_start,
                e.line_end,
                e.signature,
                e.docstring,
                e.language,
                json.dumps(e.extra),
            )
            for e in entities
        ]
        with self.transaction() as conn:
            conn.executemany(sql, rows)
        return len(rows)

    def upsert_relations(self, relations: Iterable[Relation]) -> int:
        sql = """
        INSERT OR REPLACE INTO relations
            (id, source_id, target_id, kind, file_path, line, weight, extra)
        VALUES (?,?,?,?,?,?,?,?)
        """
        rows = [
            (
                r.id,
                r.source_id,
                r.target_id,
                r.kind.value,
                r.file_path,
                r.line,
                r.weight,
                json.dumps(r.extra),
            )
            for r in relations
        ]
        assert self._conn
        # Disable FK checks for bulk insert: many relations reference entities
        # from other files not yet written; the resolver pass fixes orphans.
        self._conn.execute("PRAGMA foreign_keys = OFF")
        try:
            with self.transaction() as conn:
                conn.executemany(sql, rows)
        finally:
            self._conn.execute("PRAGMA foreign_keys = ON")
        return len(rows)

    def rebuild_fts(self) -> None:
        """Full FTS content sync — call once after bulk indexing."""
        assert self._conn
        self._conn.execute("INSERT INTO entities_fts(entities_fts) VALUES('delete-all')")
        self._conn.execute(
            "INSERT INTO entities_fts(rowid, name, qualified_name, docstring, signature) "
            "SELECT rowid, name, qualified_name, docstring, signature FROM entities"
        )
        self._conn.commit()

    def delete_by_file(self, file_path: str) -> None:
        with self.transaction() as conn:
            conn.execute("DELETE FROM entities WHERE file_path = ?", (file_path,))

    def set_meta(self, key: str, value: str) -> None:
        with self.transaction() as conn:
            conn.execute("INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)", (key, value))

    def get_meta(self, key: str) -> Optional[str]:
        assert self._conn
        row = self._conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    # ── Read operations ───────────────────────────────────────────────────

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        assert self._conn
        row = self._conn.execute("SELECT * FROM entities WHERE id = ?", (entity_id,)).fetchone()
        return _row_to_entity(row) if row else None

    def get_entity_by_qname(self, qualified_name: str) -> Optional[Entity]:
        assert self._conn
        row = self._conn.execute("SELECT * FROM entities WHERE qualified_name = ?", (qualified_name,)).fetchone()
        return _row_to_entity(row) if row else None

    def find_entities(
        self,
        name_glob: Optional[str] = None,
        kind: Optional[EntityKind] = None,
        file_path: Optional[str] = None,
        language: Optional[str] = None,
        limit: int = 50,
    ) -> list[Entity]:
        assert self._conn
        clauses, params = [], []
        if name_glob:
            clauses.append("name GLOB ?")
            params.append(name_glob)
        if kind:
            clauses.append("kind = ?")
            params.append(kind.value)
        if file_path:
            clauses.append("file_path GLOB ?")
            params.append(file_path)
        if language:
            clauses.append("language = ?")
            params.append(language)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(limit)
        rows = self._conn.execute(f"SELECT * FROM entities {where} LIMIT ?", params).fetchall()
        return [_row_to_entity(r) for r in rows]

    def search_fts(self, query: str, limit: int = 20) -> list[Entity]:
        assert self._conn
        # Build a safe FTS5 query: quote each word as a separate prefix term
        import re as _re

        words = [w for w in _re.split(r"[^\w]+", query.strip()) if w]
        if not words:
            return []
        fts_query = " OR ".join(f'"{w}"*' for w in words)
        try:
            rows = self._conn.execute(
                """
                SELECT e.* FROM entities e
                JOIN entities_fts f ON e.rowid = f.rowid
                WHERE entities_fts MATCH ?
                ORDER BY bm25(entities_fts)
                LIMIT ?
                """,
                (fts_query, limit),
            ).fetchall()
        except Exception:
            return []
        return [_row_to_entity(r) for r in rows]

    def get_neighbors(
        self,
        entity_id: str,
        direction: str = "both",  # "out", "in", "both"
        kinds: Optional[list[RelationKind]] = None,
        limit: int = 50,
    ) -> list[tuple[Relation, Entity]]:
        """Return (relation, neighbor_entity) pairs."""
        assert self._conn
        kind_filter = ""
        params: list = []

        if kinds:
            placeholders = ",".join("?" * len(kinds))
            kind_filter = f"AND r.kind IN ({placeholders})"
            params.extend(k.value for k in kinds)

        results = []

        _ENTITY_COLS = (
            "e.id as e_id, e.kind as e_kind, e.name as e_name, "
            "e.qualified_name as e_qualified_name, e.file_path as e_file_path, "
            "e.line_start as e_line_start, e.line_end as e_line_end, "
            "e.signature as e_signature, e.docstring as e_docstring, "
            "e.language as e_language, e.extra as e_extra"
        )

        if direction in ("out", "both"):
            q = f"""
            SELECT r.id, r.source_id, r.target_id, r.kind, r.file_path, r.line, r.weight, r.extra,
                   {_ENTITY_COLS}
            FROM relations r
            JOIN entities e ON e.id = r.target_id
            WHERE r.source_id = ? {kind_filter}
            LIMIT ?
            """
            rows = self._conn.execute(q, [entity_id] + params + [limit]).fetchall()
            for row in rows:
                results.append((_row_to_relation(row), _row_to_entity(row, prefix="e_")))

        if direction in ("in", "both"):
            q = f"""
            SELECT r.id, r.source_id, r.target_id, r.kind, r.file_path, r.line, r.weight, r.extra,
                   {_ENTITY_COLS}
            FROM relations r
            JOIN entities e ON e.id = r.source_id
            WHERE r.target_id = ? {kind_filter}
            LIMIT ?
            """
            rows = self._conn.execute(q, [entity_id] + params + [limit]).fetchall()
            for row in rows:
                results.append((_row_to_relation(row), _row_to_entity(row, prefix="e_")))

        return results

    def get_callers(self, entity_id: str, limit: int = 20) -> list[Entity]:
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT e.* FROM relations r
            JOIN entities e ON e.id = r.source_id
            WHERE r.target_id = ? AND r.kind = 'calls'
            ORDER BY r.weight DESC
            LIMIT ?
            """,
            (entity_id, limit),
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def get_callees(self, entity_id: str, limit: int = 20) -> list[Entity]:
        assert self._conn
        rows = self._conn.execute(
            """
            SELECT e.* FROM relations r
            JOIN entities e ON e.id = r.target_id
            WHERE r.source_id = ? AND r.kind = 'calls'
            ORDER BY r.weight DESC
            LIMIT ?
            """,
            (entity_id, limit),
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def get_file_entities(self, file_path: str) -> list[Entity]:
        assert self._conn
        rows = self._conn.execute(
            "SELECT * FROM entities WHERE file_path = ? ORDER BY line_start",
            (file_path,),
        ).fetchall()
        return [_row_to_entity(r) for r in rows]

    def get_stats(self) -> dict:
        assert self._conn
        entity_count = self._conn.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
        relation_count = self._conn.execute("SELECT COUNT(*) FROM relations").fetchone()[0]
        lang_rows = self._conn.execute(
            "SELECT language, COUNT(*) as c FROM entities WHERE kind IN ('file','config_file') GROUP BY language"
        ).fetchall()
        languages = {r["language"] or "unknown": r["c"] for r in lang_rows}
        file_count = self._conn.execute(
            "SELECT COUNT(*) FROM entities WHERE kind IN ('file','config_file')"
        ).fetchone()[0]
        return {
            "entities": entity_count,
            "relations": relation_count,
            "files": file_count,
            "languages": languages,
            "repo_root": self.get_meta("repo_root") or "",
            "last_updated": self.get_meta("last_updated") or "",
            "schema_version": self.get_meta("schema_version") or SCHEMA_VERSION,
        }

    def get_sql_tables(self) -> list[Entity]:
        return self.find_entities(kind=EntityKind.SQL_TABLE, limit=500)

    def get_api_routes(self) -> list[Entity]:
        return self.find_entities(kind=EntityKind.API_ROUTE, limit=500)

    def subgraph(self, entity_id: str, depth: int = 2) -> tuple[list[Entity], list[Relation]]:
        """BFS-collect entities and relations within `depth` hops."""
        assert self._conn
        visited_entities: dict[str, Entity] = {}
        visited_relations: list[Relation] = []
        frontier = {entity_id}

        for _ in range(depth):
            if not frontier:
                break
            next_frontier = set()
            for eid in frontier:
                if eid in visited_entities:
                    continue
                e = self.get_entity(eid)
                if e:
                    visited_entities[eid] = e
                out_rows = self._conn.execute("SELECT * FROM relations WHERE source_id = ?", (eid,)).fetchall()
                in_rows = self._conn.execute("SELECT * FROM relations WHERE target_id = ?", (eid,)).fetchall()
                for row in out_rows + in_rows:
                    rel = _row_to_relation(row)
                    visited_relations.append(rel)
                    next_frontier.add(row["source_id"])
                    next_frontier.add(row["target_id"])
            frontier = next_frontier - set(visited_entities.keys())

        return list(visited_entities.values()), visited_relations


# ── Row → Model helpers ───────────────────────────────────────────────────────


def _row_to_entity(row: sqlite3.Row, prefix: str = "") -> Entity:
    d = dict(row)
    if prefix:
        # Strip prefix (e.g. "e_id" -> "id") for aliased JOIN columns
        d = {k[len(prefix) :]: v for k, v in d.items() if k.startswith(prefix)}
    return Entity(
        id=d["id"],
        kind=EntityKind(d["kind"]),
        name=d["name"],
        qualified_name=d["qualified_name"],
        file_path=d["file_path"],
        line_start=d["line_start"],
        line_end=d["line_end"],
        signature=d.get("signature"),
        docstring=d.get("docstring"),
        language=d.get("language"),
        extra=json.loads(d.get("extra") or "{}"),
    )


def _row_to_relation(row: sqlite3.Row) -> Relation:
    d = dict(row)
    return Relation(
        id=d["id"],
        source_id=d["source_id"],
        target_id=d["target_id"],
        kind=RelationKind(d["kind"]),
        file_path=d.get("file_path"),
        line=d.get("line"),
        weight=d.get("weight", 1.0),
        extra=json.loads(d.get("extra") or "{}"),
    )
