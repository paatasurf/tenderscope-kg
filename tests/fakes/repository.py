"""
FakeBizRepository — in-memory test double for unit tests.

UNIT TESTS ONLY.  Never used in production or integration tests.

This implementation intentionally mirrors the behavioral contract of
BizRepository exactly, using only Python dicts/lists and no storage
dependencies.  It is the reference for "what the contract means" at the
unit-test layer.

Usage in tests:
    from tests.fakes.repository import FakeBizRepository

    @pytest.fixture
    def repo():
        return FakeBizRepository()
"""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Generator, Optional

from tenderscope_kg.repository._base import BizRepository
from tenderscope_kg.domain import (
    BizEntity,
    BizEntityKind,
    BizRelation,
    BizRelationKind,
    UID_PREFIXES,
    canonicalize,
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _relation_id(source_uid: str, kind: str, target_uid: str) -> str:
    h = hashlib.sha256(f"{source_uid}:{kind}:{target_uid}".encode()).hexdigest()
    return h[:16]


class FakeBizRepository(BizRepository):
    """
    Pure in-memory BizRepository for unit tests.

    All state lives in instance dicts.  Reset between tests by creating a
    new instance (cheap — no I/O).
    """

    def __init__(self) -> None:
        self._entities: dict[str, BizEntity] = {}
        self._relations: dict[str, BizRelation] = {}
        self._history: dict[str, list[dict]] = {}
        self._sequences: dict[str, int] = {}

    # ── UID allocation ────────────────────────────────────────────────────────

    def _next_uid(self, kind: BizEntityKind) -> str:
        prefix = UID_PREFIXES[kind.value]
        val = self._sequences.get(prefix, 0) + 1
        self._sequences[prefix] = val
        return f"{prefix}-{val:08d}"

    # ── BizRepository interface ───────────────────────────────────────────────

    @contextmanager
    def transaction(self) -> Generator:
        import copy
        snapshot_entities = copy.deepcopy(self._entities)
        snapshot_relations = copy.deepcopy(self._relations)
        snapshot_history = copy.deepcopy(self._history)
        snapshot_sequences = dict(self._sequences)
        try:
            yield
        except Exception:
            self._entities = snapshot_entities
            self._relations = snapshot_relations
            self._history = snapshot_history
            self._sequences = snapshot_sequences
            raise

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
        attrs = attributes or {}
        now = _now()

        existing = self.find_by_canonical(kind, canonical)
        created = existing is None

        if created:
            new_uid = uid or self._next_uid(kind)
            entity = BizEntity(
                uid=new_uid,
                kind=kind,
                name=name,
                canonical_name=canonical,
                attributes=dict(attrs),
                source=source,
                confidence=confidence,
                created_at=now,
                updated_at=now,
            )
            self._entities[new_uid] = entity
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
            self._entities[entity.uid] = entity

        if write_history:
            entry = {
                "changed_at": now,
                "changed_by": source,
                "snapshot": entity.to_full(),
            }
            self._history.setdefault(entity.uid, []).append(entry)

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
        rel_id = _relation_id(source_uid, kind.value, target_uid)
        now = _now()
        attrs = attributes or {}
        created = rel_id not in self._relations

        if not created:
            existing = self._relations[rel_id]
            confidence = max(confidence, existing.confidence)
            source = source or existing.source

        rel = BizRelation(
            id=rel_id,
            source_uid=source_uid,
            target_uid=target_uid,
            kind=kind,
            confidence=confidence,
            source=source,
            attributes=attrs,
            valid_from=valid_from,
            valid_to=valid_to,
            created_at=now,
        )
        self._relations[rel_id] = rel
        return rel, created

    def bulk_put_entities(
        self,
        records,
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
        return self._entities.get(uid)

    def find_by_canonical(
        self, kind: BizEntityKind, canonical_name: str
    ) -> Optional[BizEntity]:
        for e in self._entities.values():
            if e.kind == kind and e.canonical_name == canonical_name:
                return e
        return None

    def find(
        self,
        kind: Optional[BizEntityKind] = None,
        name_like: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[BizEntity]:
        results = list(self._entities.values())
        if kind is not None:
            results = [e for e in results if e.kind == kind]
        if name_like is not None:
            needle = canonicalize(name_like)
            results = [e for e in results if needle in e.canonical_name]
        results.sort(key=lambda e: e.name)
        return results[offset: offset + limit]

    def search_fts(self, query: str, limit: int = 20) -> list[BizEntity]:
        if not query or not query.strip():
            return []
        words = query.strip().lower().split()
        results = []
        for e in self._entities.values():
            text = f"{e.name} {e.canonical_name} {e.attributes}".lower()
            if all(w in text for w in words):
                results.append(e)
        return results[:limit]

    def get_neighbors(
        self,
        uid: str,
        direction: str = "both",
        kinds: Optional[list[BizRelationKind]] = None,
        active_only: bool = False,
        limit: int = 100,
    ) -> list[tuple[BizRelation, BizEntity]]:
        results: list[tuple[BizRelation, BizEntity]] = []
        for rel in self._relations.values():
            if active_only and rel.valid_to is not None:
                continue
            if kinds and rel.kind not in kinds:
                continue

            neighbour_uid: Optional[str] = None
            if direction in ("out", "both") and rel.source_uid == uid:
                neighbour_uid = rel.target_uid
            elif direction in ("in", "both") and rel.target_uid == uid:
                neighbour_uid = rel.source_uid

            if neighbour_uid and neighbour_uid in self._entities:
                results.append((rel, self._entities[neighbour_uid]))

        return results[:limit]

    def get_relations_between(
        self, source_uid: str, target_uid: str
    ) -> list[BizRelation]:
        return [
            r for r in self._relations.values()
            if r.source_uid == source_uid and r.target_uid == target_uid
        ]

    def entity_history(self, uid: str) -> list[dict]:
        return list(self._history.get(uid, []))

    def get_stats(self) -> dict:
        by_kind: dict[str, int] = {}
        for e in self._entities.values():
            k = e.kind.value if hasattr(e.kind, "value") else e.kind
            by_kind[k] = by_kind.get(k, 0) + 1
        return {
            "entities": len(self._entities),
            "relations": len(self._relations),
            "by_kind": by_kind,
        }

    def rebuild_fts(self) -> None:
        pass
