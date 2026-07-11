"""
Shared engine factory for all transports (MCP, REST, CLI).

Both the MCP server and the REST server instantiate engines through
``build_engines()``.  This guarantees a single business-logic
implementation regardless of how the service is accessed.

Rules
-----
- No business logic lives here.  This file only wires together
  existing engines.
- Do not add request-handling code, HTTP concerns, or MCP protocol
  details to this module.
- Both transports receive the same ``EngineSet`` instance.
"""

from __future__ import annotations

from dataclasses import dataclass

from .biz_query_engine import BizQueryEngine
from .buyer_intelligence import BuyerIntelligenceEngine
from .company_intelligence import CompanyIntelligenceEngine
from .competitive_intelligence import CompetitiveIntelligenceEngine
from .executive_decision import ExecutiveDecisionEngine
from .opportunity_intelligence import OpportunityIntelligenceEngine
from .relationship_intelligence import RelationshipIntelligenceEngine
from .repository._base import BizRepository


@dataclass(frozen=True)
class EngineSet:
    """
    The complete set of intelligence engines built over one repository.

    Frozen so that transports cannot accidentally replace an engine
    after construction.
    """

    biz: BizQueryEngine
    cie: CompanyIntelligenceEngine
    rie: RelationshipIntelligenceEngine
    cei: CompetitiveIntelligenceEngine
    bie: BuyerIntelligenceEngine
    oie: OpportunityIntelligenceEngine
    ede: ExecutiveDecisionEngine

    def health(self) -> dict:
        """
        Aggregate readiness check for the EngineSet.

        Returns per-engine status (all present engines report ``ok``) and a
        repository connectivity probe via ``biz.graph_statistics()``.
        """
        engine_statuses: dict[str, str] = {}
        for name in ("biz", "cie", "rie", "cei", "bie", "oie", "ede"):
            engine = getattr(self, name)
            engine_statuses[name] = "ok" if engine is not None else "down"

        try:
            self.biz.graph_statistics()
            repo_status = "ok"
        except Exception as exc:  # noqa: BLE001
            repo_status = f"down: {exc}"

        overall = "ok" if repo_status == "ok" and all(s == "ok" for s in engine_statuses.values()) else "degraded"
        return {
            "status": overall,
            "repository": repo_status,
            "engines": engine_statuses,
        }


def build_engines(repo: BizRepository) -> EngineSet:
    """
    Construct all intelligence engines from a single repository instance.

    This is the one place in the codebase where engines are wired to a
    repository.  MCP server, REST server, and CLI all call this function.
    """
    return EngineSet(
        biz=BizQueryEngine(repo),
        cie=CompanyIntelligenceEngine(repo),
        rie=RelationshipIntelligenceEngine(repo),
        cei=CompetitiveIntelligenceEngine(repo),
        bie=BuyerIntelligenceEngine(repo),
        oie=OpportunityIntelligenceEngine(repo),
        ede=ExecutiveDecisionEngine(repo),
    )
