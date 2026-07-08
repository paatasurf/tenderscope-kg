"""
TenderScope Knowledge Graph — Business domain entities.

Pure dataclasses.  Zero imports from storage, engines, or external libraries.

Identity model
--------------
The COMPANY entity is the primary business identity across the entire platform.

  company_uid   — immutable, permanent, never reused
  canonical_name — write-once dedup key; never changed after first insert
  name           — mutable display name (DBA, legal name, current trading name)
  attributes     — all metadata including external identifiers (see EXTERNAL_ID_KEYS)

Everything else (names, addresses, phone numbers, websites, aliases, legal
names, DBA names, BC Registry numbers, BN, DUNS, LEI, LinkedIn, etc.) is
metadata stored in attributes or attached via ALIAS_OF / SAME_AS relations.

Use CompanyIdentity for a structured read-only view of a canonical company
with all its identifiers, names, and aliases in one place.
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
        d: dict = {
            "uid": self.uid,
            "kind": self.kind.value if hasattr(self.kind, "value") else self.kind,
            "name": self.name,
            "canonical_name": self.canonical_name,
            "source": self.source,
            "confidence": self.confidence,
        }
        if self.attributes.get("scraper_id") is not None:
            d["scraper_id"] = self.attributes["scraper_id"]
        return d

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


@dataclass
class IdentityEvidence:
    """
    Structured confidence + explanation for an identity match decision.

    Stored as the 'attributes' dict on ALIAS_OF and SAME_AS relations so
    every merge decision is auditable and reversible.

    Fields
    ------
    confidence : float, 0–1
        How certain we are that this match is correct.
        1.0 = certain (same scraper canonical_company_id)
        0.9 = very high (exact name match after canonicalization)
        0.7 = high (fuzzy name match + same city)
        0.5 = plausible (fuzzy name match only)
        <0.5 = weak / speculative
    reason : str
        Short machine-readable reason code, e.g.:
          "canonical_id_match", "exact_name", "fuzzy_name_city",
          "bc_registry_match", "business_number_match", "domain_match"
    explanation : str
        Human-readable sentence explaining the match.
    evidence : list[dict]
        Supporting facts.  Each dict has keys:
          field   — which field matched (e.g. "canonical_name", "id_bc_registry")
          value   — the matched value
          source  — which dataset provided it
    source : str
        Which importer or pipeline produced this decision.
    decided_at : str | None
        ISO 8601 timestamp of the decision.
    """
    confidence: float
    reason: str
    explanation: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source: Optional[str] = None
    decided_at: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "confidence": self.confidence,
            "reason": self.reason,
            "explanation": self.explanation,
            "evidence": self.evidence,
            "source": self.source,
            "decided_at": self.decided_at,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "IdentityEvidence":
        return cls(
            confidence=float(d.get("confidence", 1.0)),
            reason=d.get("reason", ""),
            explanation=d.get("explanation", ""),
            evidence=d.get("evidence", []),
            source=d.get("source"),
            decided_at=d.get("decided_at"),
        )


@dataclass
class CompanyIdentity:
    """
    Typed read-only view of a canonical COMPANY and all its identity records.

    This is NOT a storage entity — it is assembled by the query layer from
    the graph and returned to callers that need a complete identity picture.

    All relationships in the graph and all future datasets must reference
    company_uid, never company names.

    Fields
    ------
    company_uid : str
        Immutable permanent identity.  Never changes.
    display_name : str
        Current display name (may change over time).
    canonical_name : str
        Write-once normalization key.  Used for dedup only.
    aliases : list[dict]
        All known alias names.  Each dict: {uid, name, confidence, reason,
        evidence, source}.
    external_ids : dict[str, str]
        Known external identifiers keyed by EXTERNAL_ID_KEYS values, e.g.:
          {"id_bc_registry": "BC1234567", "id_business_number": "123456789"}
    attributes : dict
        All other metadata (city, province, phone, email, website, scores…).
    merge_candidates : list[dict]
        SAME_AS neighbours.  Each dict: {uid, name, confidence, reason,
        evidence}.  These are companies the pipeline believes may be the
        same real entity, pending human confirmation.
    source : str | None
        Which importer created the canonical record.
    confidence : float
        Data quality confidence of the canonical record itself.
    """
    company_uid: str
    display_name: str
    canonical_name: str
    aliases: list[dict[str, Any]] = field(default_factory=list)
    external_ids: dict[str, str] = field(default_factory=dict)
    attributes: dict[str, Any] = field(default_factory=dict)
    merge_candidates: list[dict[str, Any]] = field(default_factory=list)
    source: Optional[str] = None
    confidence: float = 1.0

    def to_dict(self) -> dict:
        return {
            "company_uid":       self.company_uid,
            "display_name":      self.display_name,
            "canonical_name":    self.canonical_name,
            "aliases":           self.aliases,
            "external_ids":      self.external_ids,
            "attributes":        self.attributes,
            "merge_candidates":  self.merge_candidates,
            "source":            self.source,
            "confidence":        self.confidence,
        }
