"""Prototype impact analysis for graph confidence-based CI/EDE gating (read-only).

This script computes a synthetic confidence score for every COMPANY node in a
TenderScope knowledge graph and simulates what would happen if CI/EDE only
consumed companies above a confidence threshold.

The confidence formula is a prototype and can be tuned. It currently weights:
  - external identifiers (BC Registry, BN, GST, scraper_id, etc.)
  - ALIAS_OF / SAME_AS edges carrying IdentityEvidence
  - business relationships (AWARDED_TO, SUBMITTED_BID, PARTICIPATED_IN)
  - source diversity

Run against a graph database:

    python scripts/prototype_graph_confidence.py /path/to/graph.db

If no path is supplied, it uses the local `.tkg/graph.db` in the repo root.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

# Ensure repo src is importable
REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.domain.kinds import EXTERNAL_ID_KEYS
from tenderscope_kg.repository import open_repository
from tenderscope_kg.repository._base import IdentityEvidence


CONFIDENCE_THRESHOLDS = (0.25, 0.5, 0.7, 0.85)


@dataclass
class CompanyConfidence:
    uid: str
    name: str
    canonical_name: str
    confidence: float
    score_breakdown: dict[str, float] = field(default_factory=dict)
    alias_count: int = 0
    external_id_count: int = 0
    relationship_count: int = 0
    sources: list[str] = field(default_factory=list)


@dataclass
class ThresholdImpact:
    threshold: float
    companies_above: int
    companies_below: int
    pct_above: float
    sample_excluded: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class GraphConfidenceReport:
    graph_path: str
    total_company_nodes: int = 0
    score_distribution: dict[str, int] = field(default_factory=dict)
    confidence_scores: list[CompanyConfidence] = field(default_factory=list)
    threshold_impacts: list[ThresholdImpact] = field(default_factory=list)
    formula: dict[str, float] = field(default_factory=dict)


def _external_id_count(entity: Any) -> int:
    attrs = entity.attributes or {}
    return sum(1 for key in EXTERNAL_ID_KEYS.values() if attrs.get(key))


def _score_sources(sources: set[str]) -> float:
    """Reward evidence coming from multiple independent importers/pipelines."""
    return min(len(sources) / 3.0, 1.0)


def _evidence_confidence(neighbor_rel) -> float:
    """Extract confidence from IdentityEvidence if present, else relation confidence."""
    if neighbor_rel.attributes:
        try:
            ev = IdentityEvidence.from_dict(neighbor_rel.attributes)
            return float(ev.confidence or neighbor_rel.confidence or 0.5)
        except Exception:
            pass
    return float(neighbor_rel.confidence or 0.5)


def compute_company_confidence(repo: Any, uid: str) -> CompanyConfidence:
    entity = repo.get(uid)
    aliases: list[tuple[Any, Any]] = repo.get_neighbors(
        uid, direction="in", kinds=[BizRelationKind.ALIAS_OF]
    )
    same_as: list[tuple[Any, Any]] = repo.get_neighbors(
        uid, direction="both", kinds=[BizRelationKind.SAME_AS]
    )

    # Business activity edges
    biz_kinds = {
        BizRelationKind.AWARDED_TO,
        BizRelationKind.SUBMITTED_BID,
        BizRelationKind.PARTICIPATED_IN,
        BizRelationKind.CONTRACTED_FOR,
        BizRelationKind.APPLIED_FOR,
    }
    out_edges = repo.get_neighbors(uid, direction="out")
    in_edges = repo.get_neighbors(uid, direction="in")
    relationships = [
        (rel, ent)
        for rel, ent in (out_edges + in_edges)
        if rel.kind in biz_kinds
    ]

    sources: set[str] = set()
    if entity.source:
        sources.add(entity.source)
    for rel, _ in aliases + same_as + relationships:
        if rel.source:
            sources.add(rel.source)

    # Component scores
    id_score = min(_external_id_count(entity) * 0.25, 1.0)
    alias_score = min(sum(_evidence_confidence(rel) for rel, _ in aliases) / 2.0, 1.0)
    same_as_score = min(sum(_evidence_confidence(rel) for rel, _ in same_as) / 2.0, 1.0)
    relationship_score = min(len(relationships) / 10.0, 1.0)
    source_score = _score_sources(sources)

    # Weighted blend (prototype weights)
    weights = {
        "external_ids": 0.30,
        "aliases": 0.25,
        "same_as_candidates": 0.10,
        "business_relationships": 0.20,
        "source_diversity": 0.15,
    }
    confidence = (
        weights["external_ids"] * id_score
        + weights["aliases"] * alias_score
        + weights["same_as_candidates"] * same_as_score
        + weights["business_relationships"] * relationship_score
        + weights["source_diversity"] * source_score
    )

    return CompanyConfidence(
        uid=uid,
        name=entity.name,
        canonical_name=entity.canonical_name,
        confidence=round(confidence, 3),
        score_breakdown={
            "external_ids": round(id_score, 3),
            "aliases": round(alias_score, 3),
            "same_as_candidates": round(same_as_score, 3),
            "business_relationships": round(relationship_score, 3),
            "source_diversity": round(source_score, 3),
        },
        alias_count=len(aliases),
        external_id_count=_external_id_count(entity),
        relationship_count=len(relationships),
        sources=sorted(sources),
    )


def analyze_graph(db_path: Path) -> GraphConfidenceReport:
    repo = open_repository(db_path)

    report = GraphConfidenceReport(graph_path=str(db_path))
    report.formula = {
        "external_ids": 0.30,
        "aliases": 0.25,
        "same_as_candidates": 0.10,
        "business_relationships": 0.20,
        "source_diversity": 0.15,
    }

    try:
        # List all company nodes in batches
        company_uids: list[str] = []
        offset = 0
        while True:
            batch = repo.find(kind=BizEntityKind.COMPANY, limit=1000, offset=offset)
            if not batch:
                break
            company_uids.extend(ent.uid for ent in batch)
            offset += len(batch)
            if len(batch) < 1000:
                break
        report.total_company_nodes = len(company_uids)

        scores: list[CompanyConfidence] = []
        for uid in company_uids:
            try:
                scores.append(compute_company_confidence(repo, uid))
            except Exception as exc:
                print(f"WARN: failed to score {uid}: {exc}", file=sys.stderr)

        # Sort by confidence descending
        scores.sort(key=lambda c: c.confidence, reverse=True)
        report.confidence_scores = scores

        # Distribution buckets
        buckets = Counter()
        for s in scores:
            if s.confidence >= 0.8:
                buckets["0.8-1.0"] += 1
            elif s.confidence >= 0.5:
                buckets["0.5-0.8"] += 1
            elif s.confidence >= 0.25:
                buckets["0.25-0.5"] += 1
            else:
                buckets["0.0-0.25"] += 1
        report.score_distribution = dict(buckets)

        # Threshold simulation
        for threshold in CONFIDENCE_THRESHOLDS:
            above = [s for s in scores if s.confidence >= threshold]
            below = [s for s in scores if s.confidence < threshold]
            pct = (len(above) / len(scores) * 100) if scores else 0.0
            report.threshold_impacts.append(
                ThresholdImpact(
                    threshold=threshold,
                    companies_above=len(above),
                    companies_below=len(below),
                    pct_above=round(pct, 2),
                    sample_excluded=[
                        {
                            "uid": s.uid,
                            "name": s.name,
                            "confidence": s.confidence,
                            "breakdown": s.score_breakdown,
                        }
                        for s in below[:10]
                    ],
                )
            )

    finally:
        pass  # SQLite repository has no close(); connection closes at process exit

    return report


def main() -> None:
    if len(sys.argv) > 1:
        db_path = Path(sys.argv[1])
    else:
        db_path = REPO_ROOT / ".tkg" / "graph.db"

    if not db_path.exists():
        print(f"Graph database not found: {db_path}", file=sys.stderr)
        print("Usage: python scripts/prototype_graph_confidence.py <path/to/graph.db>", file=sys.stderr)
        sys.exit(1)

    report = analyze_graph(db_path)
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
