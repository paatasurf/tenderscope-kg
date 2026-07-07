"""
TenderScope Knowledge Graph — Domain layer.

Pure domain objects with zero external dependencies.
Nothing in this package imports from storage, engines, or importers.
"""
from __future__ import annotations

from .entities import BizEntity, BizRelation
from .kinds import (
    BizEntityKind,
    BizRelationKind,
    UID_PREFIXES,
    PREFIX_TO_KIND,
    canonicalize,
)
from .results import ImportResult

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
