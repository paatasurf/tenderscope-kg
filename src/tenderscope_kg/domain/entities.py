"""
TenderScope Knowledge Graph — Business domain entities.

Pure dataclasses.  Zero imports from storage, engines, or external libraries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class BizEntity:
    """A node in the business knowledge graph."""

    uid: str                                  # e.g. "CMP-00000001"
    kind: Any                                 # BizEntityKind (avoid circular import)
    name: str                                 # display name
    canonical_name: str                       # normalised for dedup (see canonicalize())
    attributes: dict[str, Any] = field(default_factory=dict)
    source: Optional[str] = None             # which importer created this
    confidence: float = 1.0                  # 0–1 data-quality score
    created_at: Optional[str] = None         # ISO 8601
    updated_at: Optional[str] = None         # ISO 8601

    def to_summary(self) -> dict:
        return {
            "uid": self.uid,
            "kind": self.kind.value if hasattr(self.kind, "value") else self.kind,
            "name": self.name,
            "canonical_name": self.canonical_name,
            "source": self.source,
            "confidence": self.confidence,
        }

    def to_full(self) -> dict:
        return {
            "uid": self.uid,
            "kind": self.kind.value if hasattr(self.kind, "value") else self.kind,
            "name": self.name,
            "canonical_name": self.canonical_name,
            "attributes": self.attributes,
            "source": self.source,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class BizRelation:
    """A typed edge in the business knowledge graph."""

    id: str                                   # sha256[:16] of (source_uid+kind+target_uid)
    source_uid: str
    target_uid: str
    kind: Any                                 # BizRelationKind
    confidence: float = 1.0
    source: Optional[str] = None             # importer name
    attributes: dict[str, Any] = field(default_factory=dict)
    valid_from: Optional[str] = None         # ISO 8601
    valid_to: Optional[str] = None           # ISO 8601; None = still valid
    created_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "source_uid": self.source_uid,
            "target_uid": self.target_uid,
            "kind": self.kind.value if hasattr(self.kind, "value") else self.kind,
            "confidence": self.confidence,
            "source": self.source,
            "attributes": self.attributes,
            "valid_from": self.valid_from,
            "valid_to": self.valid_to,
        }
