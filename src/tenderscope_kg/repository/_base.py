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

from abc import ABC, abstractmethod
from contextlib import AbstractContextManager
from typing import Optional

from ..domain import (
    EXTERNAL_ID_KEYS,
    BizEntity,
    BizEntityKind,
    BizRelation,
    BizRelationKind,
    CompanyIdentity,
    IdentityEvidence,
    canonicalize,
)


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

    def resolve_company_uid(
        self,
        name: str,
        source: Optional[str] = None,
        attributes: Optional[dict] = None,
    ) -> BizEntity:
        """
        Resolve a company name to a canonical COMPANY entity.

        Resolution order
        ----------------
        1. Exact (kind=COMPANY, canonical_name) lookup — fast path.
        2. Exact (kind=COMPANY_ALIAS, canonical_name) lookup → follow
           ALIAS_OF edge → return the canonical COMPANY.
        3. Create a new COMPANY entity and return it.

        This is the single entry point that ALL importers and future
        dataset loaders must call when they only have a company name.
        It guarantees:
          - No duplicate COMPANY nodes are created for known aliases.
          - Every relation is attached to the immutable canonical UID.
          - Names are metadata; the UID is the permanent identity.

        The returned entity is always kind=COMPANY (never COMPANY_ALIAS).
        Callers must use entity.uid — never the name — when creating
        relations.
        """
        canon = canonicalize(name)

        # 1. Already a canonical COMPANY?
        existing = self.find_by_canonical(BizEntityKind.COMPANY, canon)
        if existing is not None:
            return existing

        # 2. A known alias? Resolve to canonical.
        alias = self.find_by_canonical(BizEntityKind.COMPANY_ALIAS, canon)
        if alias is not None:
            resolved = self.resolve_alias(alias.uid)
            if resolved is not None and resolved.kind == BizEntityKind.COMPANY:
                return resolved

        # 3. Genuinely unknown — create a new canonical COMPANY.
        # write_history=True so every new company has a creation audit record.
        entity, _ = self.put_entity(
            kind=BizEntityKind.COMPANY,
            name=name,
            attributes=attributes or {},
            source=source,
            write_history=True,
        )
        return entity

    def resolve_alias(self, uid: str) -> Optional[BizEntity]:
        """
        If uid belongs to a COMPANY_ALIAS entity, follow its ALIAS_OF edge
        and return the canonical COMPANY entity.

        If uid already belongs to a canonical entity (any kind other than
        COMPANY_ALIAS), return that entity unchanged.

        Returns None if uid is not found.

        This is the platform-wide identity-resolution primitive.  All
        importers and query engines must call this before creating any
        business relation so that no edge ever points to a COMPANY_ALIAS
        unless it is itself an ALIAS_OF edge.

        Contract (all backends):
          - O(1) or O(hops) — must not do a full-table scan.
          - Idempotent: resolve_alias(canonical_uid) == get(canonical_uid).
          - Chains of aliases (alias → alias → canonical) are fully resolved.
        """
        entity = self.get(uid)
        if entity is None:
            return None
        if entity.kind != BizEntityKind.COMPANY_ALIAS:
            return entity
        neighbours = self.get_neighbors(
            uid,
            direction="out",
            kinds=[BizRelationKind.ALIAS_OF],
        )
        for _rel, neighbour in neighbours:
            return self.resolve_alias(neighbour.uid)
        return entity

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
    def find_by_attribute(
        self,
        key: str,
        value: object,
        kind: Optional[BizEntityKind] = None,
        limit: int = 10,
    ) -> list[BizEntity]:
        """
        Return entities whose attributes JSONB contains {key: value}.

        Used for external-ID lookups such as scraper_id → graph UID.
        Results are not ordered (implementation-defined).
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

    # ── Identity layer ─────────────────────────────────────────────────────────

    def attach_identifier(
        self,
        company_uid: str,
        id_key: str,
        id_value: str,
        source: Optional[str] = None,
    ) -> BizEntity:
        """
        Attach an external identifier to a canonical COMPANY entity.

        id_key must be one of the values in EXTERNAL_ID_KEYS (e.g.
        'id_bc_registry', 'id_business_number', 'id_duns').  Using keys
        from EXTERNAL_ID_KEYS ensures consistent naming across all importers.

        The identifier is merged into the entity's attributes dict.  It is
        pure metadata — it never changes company_uid or canonical_name.

        Returns the updated entity.

        Example
        -------
            repo.attach_identifier(
                company_uid="CMP-00000001",
                id_key=EXTERNAL_ID_KEYS["bc_registry"],
                id_value="BC1234567",
                source="bc_registry_importer",
            )
        """
        entity = self.get(company_uid)
        if entity is None:
            raise KeyError(f"Company not found: {company_uid}")
        if entity.kind != BizEntityKind.COMPANY:
            raise ValueError(
                f"{company_uid} is kind={entity.kind.value}, attach_identifier only applies to COMPANY entities"
            )
        updated, _ = self.put_entity(
            kind=BizEntityKind.COMPANY,
            name=entity.name,
            attributes={id_key: id_value},
            source=source,
            write_history=False,
        )
        return updated

    def company_identity(self, company_uid: str) -> Optional[CompanyIdentity]:
        """
        Return a complete CompanyIdentity view for a canonical COMPANY.

        Assembles: the canonical entity, all ALIAS_OF neighbours, all
        external identifiers from attributes, and all SAME_AS neighbours.

        Returns None if company_uid is not found or is not a COMPANY.

        This is the primary read API for the identity layer.  Callers that
        need to display, export, or compare a company's full identity record
        should use this instead of assembling the pieces manually.
        """
        entity = self.get(company_uid)
        if entity is None or entity.kind != BizEntityKind.COMPANY:
            return None

        # ── Collect aliases (COMPANY_ALIAS nodes pointing in via ALIAS_OF) ──
        aliases: list[dict] = []
        for rel, neighbour in self.get_neighbors(
            company_uid,
            direction="in",
            kinds=[BizRelationKind.ALIAS_OF],
        ):
            ev = (
                IdentityEvidence.from_dict(rel.attributes)
                if rel.attributes
                else IdentityEvidence(
                    confidence=rel.confidence,
                    reason="alias_of",
                    explanation=f"{neighbour.name} is an alias of {entity.name}",
                )
            )
            aliases.append(
                {
                    "uid": neighbour.uid,
                    "name": neighbour.name,
                    "confidence": ev.confidence,
                    "reason": ev.reason,
                    "explanation": ev.explanation,
                    "evidence": ev.evidence,
                    "source": rel.source,
                }
            )

        # ── Extract external identifiers from attributes ──────────────────
        known_id_values = set(EXTERNAL_ID_KEYS.values())
        external_ids = {k: v for k, v in entity.attributes.items() if k in known_id_values}
        other_attrs = {k: v for k, v in entity.attributes.items() if k not in known_id_values}

        # ── Collect SAME_AS merge candidates ─────────────────────────────
        merge_candidates: list[dict] = []
        for rel, neighbour in self.get_neighbors(
            company_uid,
            direction="both",
            kinds=[BizRelationKind.SAME_AS],
        ):
            if neighbour.kind != BizEntityKind.COMPANY:
                continue
            ev = (
                IdentityEvidence.from_dict(rel.attributes)
                if rel.attributes
                else IdentityEvidence(
                    confidence=rel.confidence,
                    reason="same_as",
                    explanation=f"{entity.name} may be the same company as {neighbour.name}",
                )
            )
            merge_candidates.append(
                {
                    "uid": neighbour.uid,
                    "name": neighbour.name,
                    "confidence": ev.confidence,
                    "reason": ev.reason,
                    "explanation": ev.explanation,
                    "evidence": ev.evidence,
                }
            )

        return CompanyIdentity(
            company_uid=entity.uid,
            display_name=entity.name,
            canonical_name=entity.canonical_name,
            aliases=aliases,
            external_ids=external_ids,
            attributes=other_attrs,
            merge_candidates=merge_candidates,
            source=entity.source,
            confidence=entity.confidence,
        )

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
