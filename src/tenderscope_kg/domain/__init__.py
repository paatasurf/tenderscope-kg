"""
TenderScope Knowledge Graph — Domain layer.

Pure domain objects with zero external dependencies.
Nothing in this package imports from storage, engines, or importers.
"""

from __future__ import annotations

from .entities import BizEntity, BizRelation, CompanyIdentity, IdentityEvidence
from .kinds import (
    EXTERNAL_ID_KEYS,
    PREFIX_TO_KIND,
    UID_PREFIXES,
    BizEntityKind,
    BizRelationKind,
    canonicalize,
)
from .results import ImportResult

__all__ = [
    "BizEntity",
    "BizRelation",
    "IdentityEvidence",
    "CompanyIdentity",
    "BizEntityKind",
    "BizRelationKind",
    "UID_PREFIXES",
    "PREFIX_TO_KIND",
    "EXTERNAL_ID_KEYS",
    "canonicalize",
    "ImportResult",
]
