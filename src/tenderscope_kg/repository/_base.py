"""
TenderScope Knowledge Graph — BizRepository abstract interface.

This module defines the behavioral contract that ALL repository implementations
must satisfy.  It imports only from the domain layer.

WHAT BELONGS HERE
-----------------
Public methods that engines, query engines, and importers call.
All inputs and outputs are domain objects or primitive Python types.

WHAT DOES NOT BELONG HERE
--------------------------
- attach(), connect(), close(), setup_schema()     → implementation concerns
- next_uid(), peek_next_uid()                      → internal sequencing detail
- Any method accepting or returning a sqlite3.Connection, SQLAlchemy Session,
  cursor, or any storage-library object
- Backend lifecycle or health management

THE CONTRACT IS THE SPECIFICATION
----------------------------------
Correct behavior is defined by this interface and by the repository contract
test suite at tests/repository_contract/.

Neither BizRepositorySQLite nor BizRepositoryPG defines correctness
independently.  Both must conform to this contract.

IMPLEMENTATIONS
---------------
BizRepositorySQLite  — REFERENCE implementation.  Local development, contract
                        tests, deterministic regression testing, offline snapshots.
                        NEVER in production.
BizRepositoryPG      — PRODUCTION implementation.  PostgreSQL with graph schema.
FakeBizRepository    — TEST ARTIFACT.  In-memory.  Unit tests only.
                        Lives in tests/fakes/. Not shipped in this package.
"""
from __future__ import annotations

import sqlite3
from abc import ABC, abstractmethod
from contextlib import AbstractContextManager, contextmanager
from typing import Optional

from ..domain import BizEntity, BizEntityKind, BizRelation, BizRelationKind


class BizRepository(ABC):
    """
    Storage-agnostic interface for the TenderScope business knowledge graph.

    All inputs and outputs are BizEntity, BizRelation, or primitive Python
    types.  No storage-library types cross this boundary in either direction.
    """

    # ── Write operations ──────────────────────────────────────────────────────

    @abstractmethod
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
        """
        Upsert an entity.

        Deduplication key: (kind, canonical_name) where canonical_name is
        domain.canonicalize(name).

        On first insert: assigns a stable UID of the form PREFIX-XXXXXXXX.
        The UID never changes on subsequent updates.

        On update: merges attributes (incoming values win for overlapping keys),
        updates name to the latest value, raises confidence to max(existing, incoming).

        Returns (entity, created) where created=True only on first insert.
        If write_history=True, appends a snapshot row to entity_history.

        Idempotent: calling twice with identical arguments returns created=False
        on the second call.
        """

    @abstractmethod
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
        """
        Upsert a relation.

        Deduplication key: (source_uid, kind, target_uid).
        Relation id is derived deterministically from this triple.

        On update: confidence is raised to max(existing, incoming),
        valid_to is updated to incoming value, source preserved if incoming is None.

        Returns (relation, created) where created=True only on first insert.
        Idempotent.
        """

    @abstractmethod
    def bulk_put_entities(
        self,
        records: list[dict],
        source: Optional[str] = None,
        write_history: bool = False,
    ) -> tuple[int, int]:
        """
        Efficient batch upsert for large imports.

        Each record dict must contain: kind (str), name (str).
        Optional keys: attributes (dict), confidence (float), uid (str).

        Returns (entities_created, entities_updated).
        Implementations should use backend-specific bulk paths for performance.
        """

    # ── Read operations ───────────────────────────────────────────────────────

    @abstractmethod
    def get(self, uid: str) -> Optional[BizEntity]:
        """Return entity by UID, or None if not found."""

    @abstractmethod
    def find_by_canonical(
        self,
        kind: BizEntityKind,
        canonical_name: str,
    ) -> Optional[BizEntity]:
        """
        Exact lookup by (kind, canonical_name).
        canonical_name must already be normalized via domain.canonicalize().
        Returns None if no match.
        """

    @abstractmethod
    def find(
        self,
        kind: Optional[BizEntityKind] = None,
        name_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BizEntity]:
        """
        Filtered entity listing with pagination.
        name_like: substring match on canonical_name (case-insensitive).
        Results are ordered by name ascending.
        """

    @abstractmethod
    def search_fts(self, query: str, limit: int = 20) -> list[BizEntity]:
        """
        Full-text search over name, canonical_name, and attribute values.

        CONTRACT (all backends must satisfy):
        - Entities whose name contains the query words MUST appear in results.
        - Result count does not exceed limit.
        - Returns [] for empty or whitespace-only query.

        NOT CONTRACTED (may differ between backends):
        - Result ordering (FTS5 vs tsvector rank differently).
        - Stemming behavior.

        Callers must not depend on ordering for correctness.
        """

    @abstractmethod
    def get_neighbors(
        self,
        uid: str,
        direction: str = "both",
        kinds: Optional[list[BizRelationKind]] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[tuple[BizRelation, BizEntity]]:
        """
        Return (relation, neighbour_entity) pairs within one hop.

        direction: "out" | "in" | "both"
          "out"  — relations where uid is source_uid
          "in"   — relations where uid is target_uid
          "both" — union of out and in

        kinds: if provided, filter to these relation types only.
        active_only: if True, exclude relations where valid_to IS NOT NULL.
        limit: maximum pairs returned.

        The BizEntity in each pair is the NEIGHBOUR, not the requested uid.
        Result ordering is not contracted.
        """

    @abstractmethod
    def get_relations_between(
        self,
        source_uid: str,
        target_uid: str,
    ) -> list[BizRelation]:
        """
        Return all direct relations between two known nodes (both directions).
        Returns [] if no relations exist.
        """

    @abstractmethod
    def entity_history(self, uid: str) -> list[dict]:
        """
        Return all historical snapshots for an entity, oldest first.
        Each entry: {changed_at: str, changed_by: str|None, snapshot: dict}.
        Returns [] if uid not found or no history recorded.
        """

    @abstractmethod
    def get_stats(self) -> dict:
        """
        Return aggregate counts for monitoring and health checks.

        Required keys:
            entities: int           — total entity count
            relations: int          — total relation count
            by_kind: dict[str,int]  — entity counts keyed by kind value

        Additional keys are implementation-defined.
        """

    # ── FTS maintenance ───────────────────────────────────────────────────────

    @abstractmethod
    def rebuild_fts(self) -> None:
        """
        Rebuild the full-text search index.

        Must be called after bulk_put_entities() for backends where FTS is
        not updated incrementally (SQLite FTS5 non-content table).

        For backends with live FTS (PostgreSQL tsvector triggers), this is
        a documented no-op — implementations must accept the call without error.
        """

    # ── Transactions ──────────────────────────────────────────────────────────

    @abstractmethod
    def transaction(self) -> AbstractContextManager:
        """
        Opaque context manager for atomic multi-operation batches.

        All put_entity and put_relation calls within the block succeed or
        fail together.  On exception, all changes within the block are
        rolled back.

        IMPORTANT — what this is NOT:
        - It does not yield a connection, cursor, or session object.
        - Callers must not interact with storage primitives inside the block.
        - The internal type of the returned context manager is an
          implementation detail.

        Usage:
            with repo.transaction():
                repo.put_entity(...)
                repo.put_relation(...)
            # committed on clean exit, rolled back on exception

        Nesting behavior is implementation-defined.
        """
