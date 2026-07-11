
"""Phase 2.1 Production Validation -- Graph confidence impact analysis (read-only).

This script computes a synthetic confidence score for every COMPANY node in a
TenderScope knowledge graph and simulates what would happen if CI/EDE only
consumed companies above a confidence threshold.

Confidence is derived from:
  - external identifiers (BC Registry, BN, GST, scraper_id, etc.)
  - ALIAS_OF edges carrying IdentityEvidence
  - SAME_AS merge candidates
  - business activity edges (AWARDED_TO, SUBMITTED_BID, etc.)
  - source diversity

Run against a graph database snapshot:

    cd tenderscope-kg
    python scripts/prototype_graph_confidence.py /path/to/graph.db

If no path is supplied, it uses the local .tkg/graph.db. For PostgreSQL:

    export DATABASE_URL="postgresql://..."
    python scripts/prototype_graph_confidence.py

The script never writes to the database.
"""

from __future__ import annotations

import json
import os
import sys
from collections import Counter
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.domain.kinds import EXTERNAL_ID_KEYS
from tenderscope_kg.repository import open_repository
from tenderscope_kg.repository._base import IdentityEvidence


CONFIDENCE_THRESHOLDS = (0.25, 0.5, 0.7, 0.85)
HISTOGRAM_BUCKETS = [0.0, 0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]


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
    histogram: dict[str, int] = field(default_factory=dict)
    percentile: dict[str, float] = field(default_factory=dict)
    recommended_threshold: float = 0.0
    recommended_threshold_rationale: str = ""
    confidence_scores: list[CompanyConfidence] = field(default_factory=list)
    threshold_impacts: list[ThresholdImpact] = field(default_factory=list)
    formula: dict[str, float] = field(default_factory=dict)
    generated_at: str = ""


def _external_id_count(entity):
    attrs = entity.attributes or {}
    return sum(1 for key in EXTERNAL_ID_KEYS.values() if attrs.get(key))


def _evidence_confidence(neighbor_rel):
    if neighbor_rel.attributes:
        try:
            ev = IdentityEvidence.from_dict(neighbor_rel.attributes)
            return float(ev.confidence or neighbor_rel.confidence or 0.5)
        except Exception:
            pass
    return float(neighbor_rel.confidence or 0.5)


def _score_sources(sources):
    return min(len(sources) / 3.0, 1.0)


def compute_company_confidence(repo, uid):
    entity = repo.get(uid)
    aliases = repo.get_neighbors(uid, direction="in", kinds=[BizRelationKind.ALIAS_OF])
    same_as = repo.get_neighbors(uid, direction="both", kinds=[BizRelationKind.SAME_AS])

    biz_kinds = {
        BizRelationKind.AWARDED_TO,
        BizRelationKind.SUBMITTED_BID,
        BizRelationKind.PARTICIPATED_IN,
        BizRelationKind.CONTRACTED_FOR,
        BizRelationKind.APPLIED_FOR,
    }
    out_edges = repo.get_neighbors(uid, direction="out")
    in_edges = repo.get_neighbors(uid, direction="in")
    relationships = [(rel, ent) for rel, ent in (out_edges + in_edges) if rel.kind in biz_kinds]

    sources = set()
    if entity.source:
        sources.add(entity.source)
    for rel, _ in aliases + same_as + relationships:
        if rel.source:
            sources.add(rel.source)

    id_score = min(_external_id_count(entity) * 0.25, 1.0)
    alias_score = min(sum(_evidence_confidence(rel) for rel, _ in aliases) / 2.0, 1.0)
    same_as_score = min(sum(_evidence_confidence(rel) for rel, _ in same_as) / 2.0, 1.0)
    relationship_score = min(len(relationships) / 10.0, 1.0)
    source_score = _score_sources(sources)

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


def _percentile(values, pct):
    if not values:
        return 0.0
    sorted_vals = sorted(values)
    k = (len(sorted_vals) - 1) * (pct / 100.0)
    f = int(k)
    c = min(f + 1, len(sorted_vals) - 1)
    if f == c:
        return round(sorted_vals[f], 3)
    return round(sorted_vals[f] + (k - f) * (sorted_vals[c] - sorted_vals[f]), 3)


def _recommend_threshold(distribution, percentile):
    """Recommend the lowest threshold that keeps at least 80% of companies."""
    p80 = percentile.get("p80", 0.5)
    # Prefer a clean threshold near p80 but not below p50.
    p50 = percentile.get("p50", 0.5)
    if p80 >= 0.5:
        return round(max(p80, 0.5), 2)
    return round(max(p50, 0.25), 2)


def analyze_graph(db_path: Path) -> GraphConfidenceReport:
    repo = open_repository(db_path)

    report = GraphConfidenceReport(
        graph_path=str(db_path),
        generated_at=datetime.now(timezone.utc).isoformat(),
        formula={
            "external_ids": 0.30,
            "aliases": 0.25,
            "same_as_candidates": 0.10,
            "business_relationships": 0.20,
            "source_diversity": 0.15,
        },
    )

    company_uids = []
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

    scores = []
    for uid in company_uids:
        try:
            scores.append(compute_company_confidence(repo, uid))
        except Exception as exc:
            print(f"WARN: failed to score {uid}: {exc}", file=sys.stderr)

    scores.sort(key=lambda c: c.confidence, reverse=True)
    report.confidence_scores = scores

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

    histogram = Counter()
    for s in scores:
        for i in range(len(HISTOGRAM_BUCKETS) - 1):
            low, high = HISTOGRAM_BUCKETS[i], HISTOGRAM_BUCKETS[i + 1]
            if low <= s.confidence < high or (high == 1.0 and s.confidence == 1.0):
                key = f"{low:.1f}-{high:.1f}"
                histogram[key] += 1
                break
    report.histogram = {k: histogram[k] for k in [f"{HISTOGRAM_BUCKETS[i]:.1f}-{HISTOGRAM_BUCKETS[i+1]:.1f}" for i in range(len(HISTOGRAM_BUCKETS)-1)]}

    values = [s.confidence for s in scores]
    report.percentile = {
        "p50": _percentile(values, 50),
        "p90": _percentile(values, 90),
        "p95": _percentile(values, 95),
        "p99": _percentile(values, 99),
    }

    report.recommended_threshold = _recommend_threshold(report.score_distribution, report.percentile)
    report.recommended_threshold_rationale = (
        f"Threshold {report.recommended_threshold} is chosen to retain the majority of "
        f"legitimate companies while excluding the lowest-confidence tail. It is near the "
        f"p80 ({report.percentile['p80']}) and never below p50. Tune after reviewing excluded samples."
    )

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

    return report


def main():
    if len(sys.argv) > 1:
        db_path = Path(sys.argv[1])
    else:
        db_path = REPO_ROOT / ".tkg" / "graph.db"

    if not db_path.exists() and not os.environ.get("DATABASE_URL"):
        print(f"Graph database not found: {db_path}", file=sys.stderr)
        print("Usage: python scripts/prototype_graph_confidence.py <path/to/graph.db>", file=sys.stderr)
        sys.exit(1)

    report = analyze_graph(db_path)
    print(json.dumps(asdict(report), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
