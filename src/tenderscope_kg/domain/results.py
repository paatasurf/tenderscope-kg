"""
TenderScope Knowledge Graph — Import result summary.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ImportResult:
    """Summary returned by every importer."""

    importer: str
    entities_created: int = 0
    entities_updated: int = 0
    relations_created: int = 0
    relations_updated: int = 0
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    elapsed_s: float = 0.0

    def to_dict(self) -> dict:
        return {
            "importer": self.importer,
            "entities_created": self.entities_created,
            "entities_updated": self.entities_updated,
            "relations_created": self.relations_created,
            "relations_updated": self.relations_updated,
            "errors": self.errors,
            "warnings": self.warnings,
            "elapsed_s": round(self.elapsed_s, 3),
        }
