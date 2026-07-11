"""
TenderScope Knowledge Graph — biz_models backward-compatibility shim.

The authoritative definitions have moved to tenderscope_kg.domain.
This module re-exports everything so that existing code using:

    from tenderscope_kg.biz_models import BizEntityKind, BizRelationKind, ...

continues to work without modification.

New code should import directly from tenderscope_kg.domain.
"""

from __future__ import annotations

from .domain.entities import BizEntity, BizRelation
from .domain.kinds import (
    PREFIX_TO_KIND,
    UID_PREFIXES,
    BizEntityKind,
    BizRelationKind,
    canonicalize,
)
from .domain.results import ImportResult

__all__ = [
    "BizEntity",
    "BizRelation",
    "BizEntityKind",
    "BizRelationKind",
    "UID_PREFIXES",
    "PREFIX_TO_KIND",
    "canonicalize",
    "ImportResult",
]
