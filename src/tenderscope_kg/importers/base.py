"""
Base importer interface.

Every importer must subclass BaseImporter and implement run().
No importer should need to know about storage internals — that is
the BizRepository's responsibility.

Adding a new importer never requires architecture changes:
1. Subclass BaseImporter
2. Implement run()
3. Register it with the CLI/MCP layer if desired

That's it.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from typing import Any

from ..domain.results import ImportResult
from ..repository._base import BizRepository


class BaseImporter(ABC):
    """
    Abstract base for all TenderScope Intelligence Engine importers.

    Subclasses must set ``name`` as a class attribute and implement ``run()``.
    """

    #: Unique identifier for this importer, used in provenance tracking
    name: str = "base"

    def __init__(self, repo: BizRepository, source_tag: str = "importer", **options: Any) -> None:
        self.repo = repo
        self.source_tag = source_tag
        self.options = options

    @abstractmethod
    def run(self) -> ImportResult:
        """
        Execute the import.  Must return an ImportResult.
        Implementations should be idempotent — running twice must not
        create duplicate entities or relations.
        """

    def _make_result(self) -> ImportResult:
        return ImportResult(importer=self.name)

    def _timed_run(self) -> ImportResult:
        """Convenience wrapper: measures elapsed time and stamps the result."""
        t0 = time.perf_counter()
        result = self.run()
        result.elapsed_s = time.perf_counter() - t0
        return result
