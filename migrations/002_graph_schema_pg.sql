-- =============================================================================
-- Migration 002: PostgreSQL graph schema for TenderScope Knowledge Graph
-- =============================================================================
-- Run with:
--   psql $PG_DSN -f migrations/002_graph_schema_pg.sql
-- Or via the CLI helper:
--   python -m tenderscope_kg.repository._postgres --migrate $PG_DSN
--
-- Idempotent: safe to re-run (IF NOT EXISTS / ON CONFLICT throughout).
-- All objects live in the 'graph' schema.
-- =============================================================================

-- ── Schema ────────────────────────────────────────────────────────────────────

CREATE SCHEMA IF NOT EXISTS graph;

-- ── UID counters ──────────────────────────────────────────────────────────────
-- One row per entity-kind prefix (CMP, TEN, PER, …).
-- Atomically incremented via INSERT … ON CONFLICT DO UPDATE RETURNING next_val.

CREATE TABLE IF NOT EXISTS graph.graph_uid_map (
    prefix   TEXT   PRIMARY KEY,
    next_val BIGINT NOT NULL DEFAULT 1
);

-- ── Entity nodes ──────────────────────────────────────────────────────────────
-- fts_vector is a GENERATED ALWAYS STORED column so PostgreSQL updates it
-- automatically on every INSERT/UPDATE — no trigger required, rebuild_fts()
-- is a no-op.

CREATE TABLE IF NOT EXISTS graph.biz_entities (
    uid            TEXT             PRIMARY KEY,
    kind           TEXT             NOT NULL,
    name           TEXT             NOT NULL,
    canonical_name TEXT             NOT NULL,
    attributes     JSONB            NOT NULL DEFAULT '{}'::jsonb,
    source         TEXT,
    confidence     DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    created_at     TEXT             NOT NULL,
    updated_at     TEXT             NOT NULL,
    fts_vector     TSVECTOR         GENERATED ALWAYS AS (
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

-- GIN index powers ts_rank() FTS queries with O(log n) lookup.
CREATE INDEX IF NOT EXISTS idx_pg_biz_ent_fts
    ON graph.biz_entities USING GIN (fts_vector);

-- ── Relation edges ────────────────────────────────────────────────────────────
-- id = sha256[:16](source_uid + ':' + kind + ':' + target_uid)
-- Identical hash algorithm as BizRepositorySQLite for cross-backend UID parity.

CREATE TABLE IF NOT EXISTS graph.biz_relations (
    id          TEXT             PRIMARY KEY,
    source_uid  TEXT             NOT NULL
                    REFERENCES graph.biz_entities(uid) ON DELETE CASCADE,
    target_uid  TEXT             NOT NULL
                    REFERENCES graph.biz_entities(uid) ON DELETE CASCADE,
    kind        TEXT             NOT NULL,
    confidence  DOUBLE PRECISION NOT NULL DEFAULT 1.0,
    source      TEXT,
    attributes  JSONB            NOT NULL DEFAULT '{}'::jsonb,
    valid_from  TEXT,
    valid_to    TEXT,
    created_at  TEXT             NOT NULL
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

-- ── Entity history (append-only) ──────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS graph.biz_entity_history (
    id         BIGSERIAL PRIMARY KEY,
    uid        TEXT  NOT NULL,
    snapshot   JSONB NOT NULL,
    changed_by TEXT,
    changed_at TEXT  NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pg_biz_hist_uid
    ON graph.biz_entity_history (uid);
