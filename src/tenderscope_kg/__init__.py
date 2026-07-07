"""
TenderScope Intelligence Engine.

Three complementary layers in one package:

Code Graph (v1)
    GraphDB + Indexer + QueryEngine
    Understands source-code structure: files, classes, functions,
    API routes, SQL tables, config keys, pipelines.

Business Graph (v2)
    BizRepository + BizQueryEngine + importers
    Understands the business domain: companies, tenders, permits,
    contracts, persons, addresses, licenses, etc.

Company Intelligence Engine (v3)
    CompanyIntelligenceEngine
    Aggregates graph relations into complete, explainable company profiles.

Relationship Intelligence Engine (v4)
    RelationshipIntelligenceEngine
    Reasons about WHY two entities are connected: infers indirect
    relationships, calculates weighted evidence strength, detects
    clusters, subcontractor chains, and recurring partnerships.

Competitive Intelligence Engine (v5)
    CompetitiveIntelligenceEngine
    Analyses competitive dynamics: direct/emerging competitors, co-bidders,
    buyer preferences, market concentration (HHI), market share by any
    dimension, dominant suppliers, challenger detection, win/loss rates,
    growth trends, competitor rankings, and competitive pressure scores.

Buyer Intelligence Engine (v6)
    BuyerIntelligenceEngine
    Profiles procurement organisations: complete supplier rosters, preferred
    suppliers, supplier loyalty and diversity scores, buying patterns,
    procurement seasonality, preferred industries, preferred contract sizes,
    average procurement value, average bidder counts, award concentration,
    buyer competitiveness scores, year-by-year timelines, and cadence-based
    tender forecasting.

Opportunity Intelligence Engine (v7)
    OpportunityIntelligenceEngine
    Scores every tender (0–100) from the perspective of a given company and
    produces executive-quality recommendations (Strong Pursue / Pursue /
    Strategic Investment / Monitor / Ignore).  Every recommendation includes
    a full score breakdown, graph evidence, assumptions, weak-evidence flags,
    missing-information gaps, step-by-step reasoning chain, opportunity
    timeline, risk analysis, portfolio impact, similar historical
    opportunities, and a CEO-dashboard executive summary.

Executive Decision Engine (v8)
    ExecutiveDecisionEngine
    The single orchestration layer that combines all five intelligence engines
    into one executive-quality decision package.  Produces situational
    awareness, market position, relationship map, opportunity pipeline, buyer
    landscape, ranked strategic priorities, a consolidated risk register, an
    executive narrative, and immediate actions.  Zero graph reads of its own
    — all data flows through the public APIs of CIE, RIE, CeI, BIE, and OIE.

All layers share one SQLite file and one connection.
"""
__version__ = "0.8.0"

from .db import GraphDB
from .indexer import Indexer
from .query_engine import QueryEngine
from .domain import BizEntity, BizEntityKind, BizRelation, BizRelationKind
from .repository._base import BizRepository
from .biz_query_engine import BizQueryEngine
from .company_intelligence import CompanyIntelligenceEngine
from .relationship_intelligence import RelationshipIntelligenceEngine
from .competitive_intelligence import CompetitiveIntelligenceEngine
from .buyer_intelligence import BuyerIntelligenceEngine
from .opportunity_intelligence import OpportunityIntelligenceEngine
try:
    from .executive_decision import ExecutiveDecisionEngine
except ModuleNotFoundError:
    ExecutiveDecisionEngine = None  # type: ignore[assignment,misc]

__all__ = [
    # Code graph
    "GraphDB",
    "Indexer",
    "QueryEngine",
    # Business graph
    "BizEntity",
    "BizEntityKind",
    "BizRelation",
    "BizRelationKind",
    "BizRepository",
    "BizQueryEngine",
    "CompanyIntelligenceEngine",
    "RelationshipIntelligenceEngine",
    "CompetitiveIntelligenceEngine",
    "BuyerIntelligenceEngine",
    "OpportunityIntelligenceEngine",
    "ExecutiveDecisionEngine",
]
