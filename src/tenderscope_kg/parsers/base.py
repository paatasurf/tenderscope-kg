"""Abstract base class for all language parsers."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ParseResult:
    entities: list  # list[Entity]
    relations: list  # list[Relation] — unresolved targets stored as qualified names in extra


class BaseParser(ABC):
    def __init__(self, file_path: str, source: str):
        self.file_path = file_path
        self.source = source
        self.ext = Path(file_path).suffix.lower()
        self.language: str = "unknown"

    @abstractmethod
    def can_parse(self) -> bool:
        """Return True if this parser handles the given file."""

    @abstractmethod
    def parse(self) -> ParseResult:
        """Extract entities and relations from the source."""

    def _module_name(self) -> str:
        """Convert file_path to dotted module name."""
        p = Path(self.file_path)
        parts = list(p.with_suffix("").parts)
        if parts and parts[0] in (".", "src"):
            parts = parts[1:]
        return ".".join(parts)
