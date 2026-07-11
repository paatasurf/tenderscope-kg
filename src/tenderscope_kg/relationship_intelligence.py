"""
TenderScope Intelligence Engine — Relationship Intelligence Engine (RIE).

Answers WHY two business entities are connected, not just that they are.

Design principles
-----------------
* **Inference-first**: derives new, non-stored relationships from graph
  structure (shared buyers, shared competitors, subcontractor chains,
  recurring partnerships, industry clusters, geographic clusters).
* **Weighted evidence**: every inferred relationship carries a numeric
  ``strength`` (0.0–1.0) computed from the number, quality, and type of
  supporting graph edges.
* **Explainable**: every result ships with ``evidence_paths`` — a list of
  human-readable hop chains that justify each inference.
* **Confidence-scored**: every inference includes a ``confidence`` value
  derived from evidence weight and relation quality.
* **Composable**: each method returns an independent dict; ``explain``
  assembles the complete picture.
* **Read-only**: no writes to the graph, no schema changes.  Runs on top
  of the existing BizRepository.

Public API
----------
  explain(uid_a, uid_b)                → full WHY explanation of the connection
  relationship_strength(uid_a, uid_b)  → numeric strength + breakdown
  shortest_path(uid_a, uid_b)          → BFS path with relation labels
  infer_relationships(uid)             → all inferred indirect relationships for one entity
  shared_buyers(uid_a, uid_b)          → orgs that commissioned both
  shared_competitors(uid_a, uid_b)     → companies that competed against both
  subcontractor_chains(uid)            → detect subcontracting patterns
  recurring_partnerships(uid)          → companies co-appearing ≥ N times
  industry_clusters(industry_uid)      → all companies in a cluster
  geographic_clusters(city_or_prov)    → companies co-located
  organization_influence(org_uid)      → buyer's influence on the company network
"""

from __future__ import annotations

import re
from collections import defaultdict, deque
from typing import Any, Optional

from .domain import BizEntityKind, BizRelationKind
from .repository._base import BizRepository

# ── Constants ─────────────────────────────────────────────────────────────────

# Weight per relation type when computing relationship strength.
# Higher = more meaningful evidence of a business relationship.
_REL_WEIGHTS: dict[str, float] = {
    BizRelationKind.AWARDED_TO.value: 1.0,
    BizRelationKind.SUBMITTED_BID.value: 0.7,
    BizRelationKind.PARTICIPATED_IN.value: 0.6,
    BizRelationKind.WORKS_WITH.value: 0.9,
    BizRelationKind.CONTRACTED_FOR.value: 0.8,
    BizRelationKind.APPLIED_FOR.value: 0.5,
    BizRelationKind.ISSUED_BY.value: 0.7,
    BizRelationKind.ISSUES.value: 0.7,
    BizRelationKind.IN_INDUSTRY.value: 0.4,
    BizRelationKind.IN_CITY.value: 0.3,
    BizRelationKind.IN_PROVINCE.value: 0.2,
    BizRelationKind.EMPLOYS.value: 0.6,
    BizRelationKind.EMPLOYED_BY.value: 0.6,
    BizRelationKind.OWNS.value: 0.8,
    BizRelationKind.OWNED_BY.value: 0.8,
    BizRelationKind.PARENT_OF.value: 0.8,
    BizRelationKind.SUBSIDIARY_OF.value: 0.8,
    BizRelationKind.MEMBER_OF.value: 0.5,
    BizRelationKind.RELATED_TO.value: 0.4,
    BizRelationKind.LICENSED_BY.value: 0.5,
}
_DEFAULT_WEIGHT = 0.3

# Relation kinds that represent strong direct business ties
_STRONG_KINDS = {
    BizRelationKind.AWARDED_TO,
    BizRelationKind.WORKS_WITH,
    BizRelationKind.CONTRACTED_FOR,
    BizRelationKind.OWNS,
    BizRelationKind.PARENT_OF,
    BizRelationKind.SUBSIDIARY_OF,
}

# Relation kinds traversed when looking for paths
_TRAVERSAL_KINDS: list[BizRelationKind] = list(BizRelationKind)


# ── Internal helpers ──────────────────────────────────────────────────────────


def _rel_weight(kind_value: str) -> float:
    return _REL_WEIGHTS.get(kind_value, _DEFAULT_WEIGHT)


def _strength_from_evidence(evidence: list[dict]) -> float:
    """
    Aggregate raw evidence weights into a 0–1 strength score.
    Uses diminishing returns: each additional piece of evidence
    contributes less than the previous one.
    """
    if not evidence:
        return 0.0
    weights = sorted(
        [_rel_weight(e.get("relation", "")) for e in evidence],
        reverse=True,
    )
    score = 0.0
    for i, w in enumerate(weights):
        score += w * (0.7**i)  # geometric decay
    return round(min(1.0, score), 4)


def _confidence_from_strength(strength: float, evidence_count: int) -> float:
    """
    Confidence = blend of strength score and evidence volume.
    At least 3 pieces of evidence required for full confidence.
    """
    volume_factor = min(1.0, evidence_count / 3.0)
    return round(min(1.0, 0.5 * strength + 0.5 * volume_factor), 4)


def _path_str(hops: list[dict]) -> str:
    """Render a hop chain to a human-readable string."""
    parts = []
    for i, hop in enumerate(hops):
        uid = hop.get("uid", "?")
        name = hop.get("name", uid)
        via = hop.get("via")
        if i == 0:
            parts.append(f"{name} [{uid}]")
        else:
            parts.append(f"--[{via}]--> {name} [{uid}]")
    return " ".join(parts)


def _require_entity(repo: BizRepository, uid: str) -> tuple[Any, Optional[dict]]:
    """Return (entity, None) or (None, error_dict)."""
    e = repo.get(uid)
    if not e:
        return None, {"error": f"Entity not found: {uid}"}
    return e, None


# ── Main class ────────────────────────────────────────────────────────────────


class RelationshipIntelligenceEngine:
    """
    Graph reasoning layer: WHY are two entities connected?

    All methods are read-only.  Instantiate with an open BizRepository.
    """

    def __init__(self, repo: BizRepository) -> None:
        self._repo = repo

    # ──────────────────────────────────────────────────────────────────────────
    # Core explain / strength API
    # ──────────────────────────────────────────────────────────────────────────

    def explain(self, uid_a: str, uid_b: str) -> dict:
        """
        Complete explanation of why uid_a and uid_b are connected.

        Returns:
          uid_a, uid_b — entity summaries
          direct_relations — any edges that directly link them
          shortest_path — BFS path (may go through intermediaries)
          shared_buyers — buyer orgs commissioning both
          shared_competitors — companies competing against both
          shared_industries — common industry nodes
          shared_locations — common city/province nodes
          recurring_co_appearances — tenders/permits where both appear
          relationship_strength — weighted 0–1 score
          confidence — confidence in the inferred connection
          explanation_text — natural-language summary
          evidence_paths — all supporting hop chains
        """
        entity_a, err = _require_entity(self._repo, uid_a)
        if err:
            return err
        entity_b, err = _require_entity(self._repo, uid_b)
        if err:
            return err

        direct = self._direct_relations(uid_a, uid_b)
        path_result = self._bfs_path(uid_a, uid_b, max_depth=6)
        buyers = self._shared_buyers_impl(uid_a, uid_b)
        competitors = self._shared_competitors_impl(uid_a, uid_b)
        industries = self._shared_industries(uid_a, uid_b)
        locations = self._shared_locations(uid_a, uid_b)
        co_appear = self._recurring_co_appearances(uid_a, uid_b)

        # Collect all evidence items across signal types
        all_evidence: list[dict] = (
            [{"relation": r["kind"], "uid": uid_b, "name": entity_b.name} for r in direct]
            + [{"relation": "shared_buyer", "uid": b["uid"], "name": b["name"]} for b in buyers]
            + [{"relation": "shared_competitor", "uid": c["uid"], "name": c["name"]} for c in competitors]
            + [{"relation": "shared_industry", "uid": i["uid"], "name": i["name"]} for i in industries]
            + [{"relation": "shared_location", "uid": loc["uid"], "name": loc["name"]} for loc in locations]
            + [{"relation": "co_appeared", "uid": ca["uid"], "name": ca["name"]} for ca in co_appear]
        )

        strength = _strength_from_evidence(all_evidence)
        confidence = _confidence_from_strength(strength, len(all_evidence))

        explanation_text = self._build_explanation_text(
            entity_a.name,
            entity_b.name,
            direct,
            buyers,
            competitors,
            industries,
            locations,
            co_appear,
            path_result,
        )

        # Collect evidence paths
        evidence_paths: list[str] = []
        for r in direct:
            evidence_paths.append(f"{entity_a.name} [{uid_a}] --[{r['kind']}]--> {entity_b.name} [{uid_b}]")
        if path_result:
            evidence_paths.append(_path_str(path_result))
        for b in buyers:
            evidence_paths.append(
                f"{entity_a.name} --[awarded_to/submitted]--> tender "
                f"--[issued_by]--> {b['name']} [{b['uid']}] "
                f"<--[issued_by]-- tender <--[awarded_to/submitted]-- {entity_b.name}"
            )
        for i in industries:
            evidence_paths.append(
                f"{entity_a.name} --[in_industry]--> {i['name']} [{i['uid']}] <--[in_industry]-- {entity_b.name}"
            )
        for loc in locations:
            evidence_paths.append(
                f"{entity_a.name} --[in_city/in_province]--> {loc['name']} [{loc['uid']}] "
                f"<--[in_city/in_province]-- {entity_b.name}"
            )

        return {
            "uid_a": entity_a.to_summary(),
            "uid_b": entity_b.to_summary(),
            "direct_relations": direct,
            "shortest_path": path_result,
            "shared_buyers": buyers,
            "shared_competitors": competitors,
            "shared_industries": industries,
            "shared_locations": locations,
            "recurring_co_appearances": co_appear,
            "relationship_strength": strength,
            "confidence": confidence,
            "evidence_count": len(all_evidence),
            "explanation_text": explanation_text,
            "evidence_paths": evidence_paths,
        }

    def relationship_strength(self, uid_a: str, uid_b: str) -> dict:
        """
        Numeric relationship strength between two entities with full breakdown.

        Returns strength (0–1), confidence, and per-signal contribution.
        """
        entity_a, err = _require_entity(self._repo, uid_a)
        if err:
            return err
        entity_b, err = _require_entity(self._repo, uid_b)
        if err:
            return err

        direct = self._direct_relations(uid_a, uid_b)
        buyers = self._shared_buyers_impl(uid_a, uid_b)
        competitors = self._shared_competitors_impl(uid_a, uid_b)
        industries = self._shared_industries(uid_a, uid_b)
        locations = self._shared_locations(uid_a, uid_b)
        co_appear = self._recurring_co_appearances(uid_a, uid_b)

        # Per-signal strength
        def _sig_strength(items, rel_key):
            evs = [{"relation": rel_key} for _ in items]
            return _strength_from_evidence(evs)

        direct_strength = _strength_from_evidence([{"relation": r["kind"]} for r in direct])
        breakdown = {
            "direct_relations": {
                "count": len(direct),
                "strength": direct_strength,
                "items": direct,
            },
            "shared_buyers": {
                "count": len(buyers),
                "strength": _sig_strength(buyers, "shared_buyer"),
                "items": [{"uid": b["uid"], "name": b["name"]} for b in buyers],
            },
            "shared_competitors": {
                "count": len(competitors),
                "strength": _sig_strength(competitors, "shared_competitor"),
                "items": [{"uid": c["uid"], "name": c["name"]} for c in competitors],
            },
            "shared_industries": {
                "count": len(industries),
                "strength": _sig_strength(industries, "shared_industry"),
                "items": industries,
            },
            "shared_locations": {
                "count": len(locations),
                "strength": _sig_strength(locations, "shared_location"),
                "items": locations,
            },
            "co_appearances": {
                "count": len(co_appear),
                "strength": _sig_strength(co_appear, "co_appeared"),
                "items": [{"uid": c["uid"], "name": c["name"]} for c in co_appear],
            },
        }

        all_evidence = (
            [{"relation": r["kind"]} for r in direct]
            + [{"relation": "shared_buyer"} for _ in buyers]
            + [{"relation": "shared_competitor"} for _ in competitors]
            + [{"relation": "shared_industry"} for _ in industries]
            + [{"relation": "shared_location"} for _ in locations]
            + [{"relation": "co_appeared"} for _ in co_appear]
        )
        strength = _strength_from_evidence(all_evidence)
        confidence = _confidence_from_strength(strength, len(all_evidence))

        return {
            "uid_a": uid_a,
            "name_a": entity_a.name,
            "uid_b": uid_b,
            "name_b": entity_b.name,
            "relationship_strength": strength,
            "confidence": confidence,
            "evidence_count": len(all_evidence),
            "breakdown": breakdown,
        }

    def shortest_path(
        self,
        uid_a: str,
        uid_b: str,
        max_depth: int = 8,
    ) -> dict:
        """
        BFS shortest path between any two business entities.

        Each hop includes the relation kind and both entity UIDs / names.
        Returns ``path`` (list of hop dicts) and ``path_string`` (human-readable).
        """
        entity_a, err = _require_entity(self._repo, uid_a)
        if err:
            return err
        entity_b, err = _require_entity(self._repo, uid_b)
        if err:
            return err

        path = self._bfs_path(uid_a, uid_b, max_depth=max_depth)
        if path is None:
            return {
                "uid_a": uid_a,
                "uid_b": uid_b,
                "found": False,
                "path": [],
                "path_string": f"No path found within {max_depth} hops",
                "hop_count": 0,
            }

        return {
            "uid_a": uid_a,
            "uid_b": uid_b,
            "found": True,
            "path": path,
            "path_string": _path_str(path),
            "hop_count": len(path) - 1,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Inference — indirect relationships
    # ──────────────────────────────────────────────────────────────────────────

    def infer_relationships(self, uid: str, limit: int = 50) -> dict:
        """
        Infer all indirect relationships for a given entity.

        Scans 2-hop neighbourhood and groups inferred connections by type:
          shared_buyer_links, shared_competitor_links,
          subcontractor_hints, partnership_hints,
          industry_cluster_peers, geographic_cluster_peers.

        Each inferred link includes strength, confidence, and evidence_path.
        """
        entity, err = _require_entity(self._repo, uid)
        if err:
            return err

        # Collect all 1-hop neighbours (outbound + inbound)
        direct_neighbors = self._repo.get_neighbors(uid, direction="both", limit=500)
        {nb.uid for _, nb in direct_neighbors}

        # ── Shared-buyer inferences ───────────────────────────────────────────
        # uid → (awarded_to/submitted_bid) → tender → (issued_by) → org
        # Other companies touching the same org are inferred partners/rivals.
        buyer_links: list[dict] = []
        tender_uids = {
            nb.uid
            for rel, nb in direct_neighbors
            if rel.kind in (BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID)
        }
        org_uids: set[str] = set()
        for t_uid in tender_uids:
            for rel, nb in self._repo.get_neighbors(
                t_uid, direction="out", kinds=[BizRelationKind.ISSUED_BY], limit=10
            ):
                org_uids.add(nb.uid)

        # Companies that also won tenders issued by the same orgs
        peer_counter: dict[str, list[str]] = defaultdict(list)  # peer_uid → [org names]
        for org_uid in org_uids:
            org_entity = self._repo.get(org_uid)
            org_name = org_entity.name if org_entity else org_uid
            for rel, tender in self._repo.get_neighbors(
                org_uid, direction="in", kinds=[BizRelationKind.ISSUED_BY], limit=200
            ):
                for rel2, co in self._repo.get_neighbors(
                    tender.uid,
                    direction="in",
                    kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
                    limit=50,
                ):
                    if co.uid != uid and co.kind == BizEntityKind.COMPANY:
                        peer_counter[co.uid].append(org_name)

        for peer_uid, orgs in sorted(peer_counter.items(), key=lambda x: -len(x[1]))[:limit]:
            peer = self._repo.get(peer_uid)
            if not peer:
                continue
            evs = [{"relation": "shared_buyer"} for _ in orgs]
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, len(orgs))
            buyer_links.append(
                {
                    "uid": peer_uid,
                    "name": peer.name,
                    "kind": "shared_buyer_link",
                    "shared_buyers": list(dict.fromkeys(orgs)),  # deduplicated
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[awarded_to]--> tender "
                        f"--[issued_by]--> {orgs[0]} <--[issued_by]-- tender "
                        f"<--[awarded_to]-- {peer.name} [{peer_uid}]"
                    ),
                }
            )

        # ── Subcontractor chain detection ─────────────────────────────────────
        # uid → (participated_in) → tender → (awarded_to) → another company
        # Implies the other company may have subcontracted uid.
        sub_hints: list[dict] = []
        participated_tender_uids = {
            nb.uid for rel, nb in direct_neighbors if rel.kind == BizRelationKind.PARTICIPATED_IN
        }
        sub_counter: dict[str, int] = defaultdict(int)
        for t_uid in participated_tender_uids:
            for rel2, winner in self._repo.get_neighbors(
                t_uid, direction="in", kinds=[BizRelationKind.AWARDED_TO], limit=50
            ):
                if winner.uid != uid and winner.kind == BizEntityKind.COMPANY:
                    sub_counter[winner.uid] += 1

        for co_uid, count in sorted(sub_counter.items(), key=lambda x: -x[1])[:limit]:
            co = self._repo.get(co_uid)
            if not co:
                continue
            evs = [{"relation": BizRelationKind.PARTICIPATED_IN.value}] * count
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, count)
            sub_hints.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "kind": "subcontractor_hint",
                    "co_tender_count": count,
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[participated_in]--> tender <--[awarded_to]-- {co.name} [{co_uid}]"
                    ),
                }
            )

        # ── Recurring partnership detection ───────────────────────────────────
        # Count how many unique tenders (awarded_to OR submitted_bid) overlap
        # with another company. ≥2 overlaps → recurring partnership hint.
        awarded_tender_uids = {
            nb.uid
            for rel, nb in direct_neighbors
            if rel.kind in _STRONG_KINDS or rel.kind == BizRelationKind.AWARDED_TO
        }
        partnership_counter: dict[str, list[str]] = defaultdict(list)
        for t_uid in awarded_tender_uids:
            tender_e = self._repo.get(t_uid)
            t_name = tender_e.name if tender_e else t_uid
            for rel2, co in self._repo.get_neighbors(
                t_uid,
                direction="in",
                kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.WORKS_WITH],
                limit=50,
            ):
                if co.uid != uid and co.kind == BizEntityKind.COMPANY:
                    partnership_counter[co.uid].append(t_name)

        partnership_hints: list[dict] = []
        for co_uid, tenders in sorted(partnership_counter.items(), key=lambda x: -len(x[1]))[:limit]:
            if len(tenders) < 2:
                continue
            co = self._repo.get(co_uid)
            if not co:
                continue
            evs = [{"relation": BizRelationKind.AWARDED_TO.value}] * len(tenders)
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, len(tenders))
            partnership_hints.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "kind": "recurring_partnership",
                    "shared_tender_count": len(tenders),
                    "shared_tenders": list(dict.fromkeys(tenders))[:10],
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[awarded_to]--> {tenders[0]} <--[awarded_to]-- {co.name} [{co_uid}]"
                    ),
                }
            )

        # ── Industry cluster peers ────────────────────────────────────────────
        industry_uids = {nb.uid for rel, nb in direct_neighbors if rel.kind == BizRelationKind.IN_INDUSTRY}
        ind_peers: dict[str, set[str]] = defaultdict(set)  # co_uid → set of industry names
        for ind_uid in industry_uids:
            ind_e = self._repo.get(ind_uid)
            ind_name = ind_e.name if ind_e else ind_uid
            for rel2, co in self._repo.get_neighbors(
                ind_uid, direction="in", kinds=[BizRelationKind.IN_INDUSTRY], limit=200
            ):
                if co.uid != uid and co.kind == BizEntityKind.COMPANY:
                    ind_peers[co.uid].add(ind_name)

        industry_cluster_peers: list[dict] = []
        for co_uid, ind_names in sorted(ind_peers.items(), key=lambda x: -len(x[1]))[:limit]:
            co = self._repo.get(co_uid)
            if not co:
                continue
            evs = [{"relation": BizRelationKind.IN_INDUSTRY.value}] * len(ind_names)
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, len(ind_names))
            industry_cluster_peers.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "kind": "industry_cluster_peer",
                    "shared_industries": sorted(ind_names),
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[in_industry]--> "
                        f"{next(iter(ind_names))} <--[in_industry]-- {co.name} [{co_uid}]"
                    ),
                }
            )

        # ── Geographic cluster peers ──────────────────────────────────────────
        loc_uids = {
            nb.uid for rel, nb in direct_neighbors if rel.kind in (BizRelationKind.IN_CITY, BizRelationKind.IN_PROVINCE)
        }
        geo_peers: dict[str, set[str]] = defaultdict(set)
        for loc_uid in loc_uids:
            loc_e = self._repo.get(loc_uid)
            loc_name = loc_e.name if loc_e else loc_uid
            for rel2, co in self._repo.get_neighbors(
                loc_uid,
                direction="in",
                kinds=[BizRelationKind.IN_CITY, BizRelationKind.IN_PROVINCE],
                limit=200,
            ):
                if co.uid != uid and co.kind == BizEntityKind.COMPANY:
                    geo_peers[co.uid].add(loc_name)

        geo_cluster_peers: list[dict] = []
        for co_uid, locs in sorted(geo_peers.items(), key=lambda x: -len(x[1]))[:limit]:
            co = self._repo.get(co_uid)
            if not co:
                continue
            evs = [{"relation": BizRelationKind.IN_CITY.value}] * len(locs)
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, len(locs))
            geo_cluster_peers.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "kind": "geographic_cluster_peer",
                    "shared_locations": sorted(locs),
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[in_city/in_province]--> "
                        f"{next(iter(locs))} <--[in_city/in_province]-- {co.name} [{co_uid}]"
                    ),
                }
            )

        total_inferred = (
            len(buyer_links)
            + len(sub_hints)
            + len(partnership_hints)
            + len(industry_cluster_peers)
            + len(geo_cluster_peers)
        )

        return {
            "uid": uid,
            "name": entity.name,
            "inferred_count": total_inferred,
            "shared_buyer_links": buyer_links,
            "subcontractor_hints": sub_hints,
            "partnership_hints": partnership_hints,
            "industry_cluster_peers": industry_cluster_peers,
            "geographic_cluster_peers": geo_cluster_peers,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Named signal methods (also usable standalone via MCP/CLI)
    # ──────────────────────────────────────────────────────────────────────────

    def shared_buyers(self, uid_a: str, uid_b: str) -> dict:
        """Return buyer organisations that commissioned both uid_a and uid_b."""
        entity_a, err = _require_entity(self._repo, uid_a)
        if err:
            return err
        entity_b, err = _require_entity(self._repo, uid_b)
        if err:
            return err

        buyers = self._shared_buyers_impl(uid_a, uid_b)
        return {
            "uid_a": uid_a,
            "name_a": entity_a.name,
            "uid_b": uid_b,
            "name_b": entity_b.name,
            "shared_buyer_count": len(buyers),
            "shared_buyers": buyers,
        }

    def shared_competitors(self, uid_a: str, uid_b: str) -> dict:
        """Return companies that competed on the same tenders as both uid_a and uid_b."""
        entity_a, err = _require_entity(self._repo, uid_a)
        if err:
            return err
        entity_b, err = _require_entity(self._repo, uid_b)
        if err:
            return err

        competitors = self._shared_competitors_impl(uid_a, uid_b)
        return {
            "uid_a": uid_a,
            "name_a": entity_a.name,
            "uid_b": uid_b,
            "name_b": entity_b.name,
            "shared_competitor_count": len(competitors),
            "shared_competitors": competitors,
        }

    def subcontractor_chains(self, uid: str, depth: int = 2) -> dict:
        """
        Detect likely subcontractor chains rooted at uid.

        A subcontractor chain means:
          uid → (awarded contract) → hires → sub1 → hires → sub2 …

        Since subcontractor relations are rarely explicit in procurement
        data, we infer them from PARTICIPATED_IN + WORKS_WITH patterns.
        """
        entity, err = _require_entity(self._repo, uid)
        if err:
            return err

        chains: list[dict] = []
        self._traverse_subcontractor(uid, entity.name, [], chains, depth, set())

        return {
            "uid": uid,
            "name": entity.name,
            "chain_count": len(chains),
            "chains": chains[:50],
        }

    def recurring_partnerships(self, uid: str, min_count: int = 2) -> dict:
        """
        Companies that appear alongside uid in ≥ min_count tenders/permits.

        A recurring partnership indicates a stable business relationship
        (preferred subcontractor, joint venture partner, etc.).
        """
        entity, err = _require_entity(self._repo, uid)
        if err:
            return err

        # Gather all tenders/projects uid participated in
        all_events = self._repo.get_neighbors(
            uid,
            direction="both",
            kinds=[
                BizRelationKind.AWARDED_TO,
                BizRelationKind.SUBMITTED_BID,
                BizRelationKind.PARTICIPATED_IN,
                BizRelationKind.CONTRACTED_FOR,
                BizRelationKind.APPLIED_FOR,
            ],
            limit=500,
        )

        # For each event, collect all other companies that also appear
        co_counter: dict[str, list[str]] = defaultdict(list)
        for rel, event in all_events:
            for rel2, co in self._repo.get_neighbors(
                event.uid,
                direction="both",
                kinds=[
                    BizRelationKind.AWARDED_TO,
                    BizRelationKind.SUBMITTED_BID,
                    BizRelationKind.PARTICIPATED_IN,
                ],
                limit=100,
            ):
                if co.uid != uid and co.kind == BizEntityKind.COMPANY:
                    co_counter[co.uid].append(event.name)

        partnerships: list[dict] = []
        for co_uid, events in sorted(co_counter.items(), key=lambda x: -len(x[1])):
            if len(events) < min_count:
                continue
            co = self._repo.get(co_uid)
            if not co:
                continue
            evs = [{"relation": BizRelationKind.AWARDED_TO.value}] * len(events)
            s = _strength_from_evidence(evs)
            c = _confidence_from_strength(s, len(events))
            partnerships.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "co_event_count": len(events),
                    "events": list(dict.fromkeys(events))[:20],
                    "strength": s,
                    "confidence": c,
                    "evidence_path": (
                        f"{entity.name} [{uid}] --[participated_in/awarded_to]--> "
                        f"{events[0]} <--[participated_in/awarded_to]-- {co.name} [{co_uid}]"
                    ),
                }
            )

        return {
            "uid": uid,
            "name": entity.name,
            "min_count": min_count,
            "partnership_count": len(partnerships),
            "partnerships": partnerships,
        }

    def industry_clusters(self, industry_uid: str, limit: int = 100) -> dict:
        """
        All companies in an industry cluster (same industry node).

        Returns ranked list with connection strength (companies with more
        tenders in this cluster rank higher).
        """
        ind = self._repo.get(industry_uid)
        if not ind:
            # Try name-based lookup
            results = self._repo.find(kind=BizEntityKind.INDUSTRY, name_like=industry_uid, limit=1)
            if not results:
                return {"error": f"Industry not found: {industry_uid}"}
            ind = results[0]
            industry_uid = ind.uid

        members = self._repo.get_neighbors(
            industry_uid, direction="in", kinds=[BizRelationKind.IN_INDUSTRY], limit=limit
        )

        companies: list[dict] = []
        for rel, co in members:
            if co.kind != BizEntityKind.COMPANY:
                continue
            awarded = self._repo.get_neighbors(co.uid, direction="out", kinds=[BizRelationKind.AWARDED_TO], limit=200)
            evs = [{"relation": BizRelationKind.AWARDED_TO.value} for _ in awarded]
            s = _strength_from_evidence(evs)
            companies.append(
                {
                    "uid": co.uid,
                    "name": co.name,
                    "tender_count": len(awarded),
                    "strength": s,
                    "evidence_path": (f"{co.name} [{co.uid}] --[in_industry]--> {ind.name} [{industry_uid}]"),
                }
            )

        companies.sort(key=lambda x: -x["tender_count"])

        return {
            "industry_uid": industry_uid,
            "industry_name": ind.name,
            "company_count": len(companies),
            "companies": companies,
        }

    def geographic_clusters(self, location: str, limit: int = 200) -> dict:
        """
        All companies in a geographic cluster (same city or province).

        ``location`` can be a UID (CTY-… or PRV-…) or a name.
        """
        loc_entity = self._repo.get(location)
        if not loc_entity:
            # Name lookup — try city first, then province
            for kind in (BizEntityKind.CITY, BizEntityKind.PROVINCE):
                results = self._repo.find(kind=kind, name_like=location, limit=1)
                if results:
                    loc_entity = results[0]
                    location = loc_entity.uid
                    break
        if not loc_entity:
            return {"error": f"Location not found: {location}"}

        is_city = loc_entity.kind == BizEntityKind.CITY
        rel_kind = BizRelationKind.IN_CITY if is_city else BizRelationKind.IN_PROVINCE

        members = self._repo.get_neighbors(loc_entity.uid, direction="in", kinds=[rel_kind], limit=limit)

        companies: list[dict] = []
        for rel, co in members:
            if co.kind != BizEntityKind.COMPANY:
                continue
            companies.append(
                {
                    "uid": co.uid,
                    "name": co.name,
                    "evidence_path": (
                        f"{co.name} [{co.uid}] --[{rel_kind.value}]--> {loc_entity.name} [{loc_entity.uid}]"
                    ),
                }
            )

        companies.sort(key=lambda x: x["name"])

        return {
            "location_uid": loc_entity.uid,
            "location_name": loc_entity.name,
            "location_kind": loc_entity.kind.value,
            "company_count": len(companies),
            "companies": companies,
        }

    def organization_influence(self, org_uid: str, limit: int = 100) -> dict:
        """
        Measure a buyer organisation's influence on the company network.

        Returns:
          - all companies it has commissioned (directly or via tenders)
          - total contract value issued
          - number of distinct companies affected
          - influence_score = min(1.0, 0.1 × company_count)
        """
        org, err = _require_entity(self._repo, org_uid)
        if err:
            return err

        # Tenders issued by this org
        issued_tenders = self._repo.get_neighbors(
            org_uid, direction="in", kinds=[BizRelationKind.ISSUED_BY], limit=limit
        )

        companies_seen: dict[str, dict] = {}
        total_value = 0.0

        for rel, tender in issued_tenders:
            awarded = self._repo.get_neighbors(
                tender.uid,
                direction="in",
                kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
                limit=100,
            )
            val = tender.attributes.get("contract_value") or rel.attributes.get("contract_value")
            if val:
                try:
                    total_value += float(re.sub(r"[^\d.]", "", str(val)))
                except ValueError:
                    pass
            for rel2, co in awarded:
                if co.kind == BizEntityKind.COMPANY and co.uid not in companies_seen:
                    companies_seen[co.uid] = {
                        "uid": co.uid,
                        "name": co.name,
                        "evidence_path": (
                            f"{co.name} [{co.uid}] --[{rel2.kind.value}]--> "
                            f"{tender.name} [{tender.uid}] "
                            f"--[issued_by]--> {org.name} [{org_uid}]"
                        ),
                    }

        company_count = len(companies_seen)
        influence_score = round(min(1.0, 0.1 * company_count), 4)

        return {
            "org_uid": org_uid,
            "org_name": org.name,
            "tender_count": len(issued_tenders),
            "company_count": company_count,
            "total_contract_value": round(total_value, 2),
            "influence_score": influence_score,
            "companies": list(companies_seen.values())[:limit],
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _direct_relations(self, uid_a: str, uid_b: str) -> list[dict]:
        """All direct graph edges between uid_a and uid_b (both directions)."""
        rels = self._repo.get_relations_between(uid_a, uid_b)
        rels += self._repo.get_relations_between(uid_b, uid_a)
        return [r.to_dict() for r in rels]

    def _shared_buyers_impl(self, uid_a: str, uid_b: str) -> list[dict]:
        """Buyer organisations linked to both uid_a and uid_b via tenders."""

        def _buyer_orgs(uid: str) -> set[str]:
            orgs: set[str] = set()
            for rel, tender in self._repo.get_neighbors(
                uid,
                direction="out",
                kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
                limit=500,
            ):
                for rel2, org in self._repo.get_neighbors(
                    tender.uid, direction="out", kinds=[BizRelationKind.ISSUED_BY], limit=10
                ):
                    orgs.add(org.uid)
            return orgs

        orgs_a = _buyer_orgs(uid_a)
        orgs_b = _buyer_orgs(uid_b)
        shared = orgs_a & orgs_b

        result: list[dict] = []
        for org_uid in shared:
            org = self._repo.get(org_uid)
            if not org:
                continue
            result.append(
                {
                    "uid": org_uid,
                    "name": org.name,
                    "kind": org.kind.value,
                    "evidence_path": (
                        f"{uid_a} --[awarded_to/submitted]--> tender --[issued_by]--> "
                        f"{org.name} [{org_uid}] <--[issued_by]-- tender <--[awarded_to/submitted]-- {uid_b}"
                    ),
                }
            )
        return result

    def _shared_competitors_impl(self, uid_a: str, uid_b: str) -> list[dict]:
        """Companies that competed on the same tenders as both uid_a and uid_b."""

        def _tender_uids(uid: str) -> set[str]:
            return {
                nb.uid
                for rel, nb in self._repo.get_neighbors(
                    uid,
                    direction="out",
                    kinds=[
                        BizRelationKind.AWARDED_TO,
                        BizRelationKind.SUBMITTED_BID,
                        BizRelationKind.PARTICIPATED_IN,
                    ],
                    limit=500,
                )
                if nb.kind == BizEntityKind.TENDER
            }

        def _companies_on_tenders(tender_uids: set[str], exclude: str) -> set[str]:
            cos: set[str] = set()
            for t_uid in tender_uids:
                for rel, co in self._repo.get_neighbors(
                    t_uid,
                    direction="in",
                    kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
                    limit=100,
                ):
                    if co.uid != exclude and co.kind == BizEntityKind.COMPANY:
                        cos.add(co.uid)
            return cos

        tenders_a = _tender_uids(uid_a)
        tenders_b = _tender_uids(uid_b)
        shared_tenders = tenders_a & tenders_b

        if not shared_tenders:
            # Fall back: companies competing on a's tenders ∩ b's tenders
            comps_a = _companies_on_tenders(tenders_a, uid_a)
            comps_b = _companies_on_tenders(tenders_b, uid_b)
            shared_uids = comps_a & comps_b
        else:
            shared_uids = _companies_on_tenders(shared_tenders, uid_a) | _companies_on_tenders(shared_tenders, uid_b)
            shared_uids -= {uid_a, uid_b}

        result: list[dict] = []
        for co_uid in shared_uids:
            co = self._repo.get(co_uid)
            if not co:
                continue
            result.append(
                {
                    "uid": co_uid,
                    "name": co.name,
                    "evidence_path": (
                        f"{uid_a} <--[awarded_to/submitted]-- tender "
                        f"--[awarded_to/submitted]--> {co.name} [{co_uid}] "
                        f"<--[awarded_to/submitted]-- tender --[awarded_to/submitted]--> {uid_b}"
                    ),
                }
            )
        return result

    def _shared_industries(self, uid_a: str, uid_b: str) -> list[dict]:
        """Industry nodes shared by both entities."""

        def _ind_uids(uid: str) -> set[str]:
            return {
                nb.uid
                for rel, nb in self._repo.get_neighbors(
                    uid,
                    direction="out",
                    kinds=[BizRelationKind.IN_INDUSTRY],
                    limit=50,
                )
            }

        shared = _ind_uids(uid_a) & _ind_uids(uid_b)
        result: list[dict] = []
        for ind_uid in shared:
            ind = self._repo.get(ind_uid)
            if ind:
                result.append({"uid": ind_uid, "name": ind.name})
        return result

    def _shared_locations(self, uid_a: str, uid_b: str) -> list[dict]:
        """City / province nodes shared by both entities."""

        def _loc_uids(uid: str) -> set[str]:
            return {
                nb.uid
                for rel, nb in self._repo.get_neighbors(
                    uid,
                    direction="out",
                    kinds=[BizRelationKind.IN_CITY, BizRelationKind.IN_PROVINCE],
                    limit=20,
                )
            }

        shared = _loc_uids(uid_a) & _loc_uids(uid_b)
        result: list[dict] = []
        for loc_uid in shared:
            loc = self._repo.get(loc_uid)
            if loc:
                result.append({"uid": loc_uid, "name": loc.name, "kind": loc.kind.value})
        return result

    def _recurring_co_appearances(self, uid_a: str, uid_b: str) -> list[dict]:
        """Tenders/permits/projects where both uid_a and uid_b appear."""

        def _event_uids(uid: str) -> set[str]:
            return {
                nb.uid
                for rel, nb in self._repo.get_neighbors(
                    uid,
                    direction="both",
                    kinds=[
                        BizRelationKind.AWARDED_TO,
                        BizRelationKind.SUBMITTED_BID,
                        BizRelationKind.PARTICIPATED_IN,
                        BizRelationKind.APPLIED_FOR,
                        BizRelationKind.CONTRACTED_FOR,
                    ],
                    limit=500,
                )
            }

        shared = _event_uids(uid_a) & _event_uids(uid_b)
        result: list[dict] = []
        for ev_uid in shared:
            ev = self._repo.get(ev_uid)
            if ev:
                result.append({"uid": ev_uid, "name": ev.name, "kind": ev.kind.value})
        return result

    def _bfs_path(
        self,
        start: str,
        goal: str,
        max_depth: int = 8,
    ) -> Optional[list[dict]]:
        """
        BFS from start → goal, returning a list of hop dicts, or None.
        Each hop dict has: uid, name, kind, via (relation kind from previous hop).
        """
        start_entity = self._repo.get(start)
        if not start_entity:
            return None

        visited: set[str] = {start}
        queue: deque[tuple[str, list[dict]]] = deque(
            [(start, [{"uid": start, "name": start_entity.name, "kind": start_entity.kind.value}])]
        )

        while queue:
            current_uid, path = queue.popleft()
            if len(path) > max_depth + 1:
                break
            neighbours = self._repo.get_neighbors(current_uid, direction="both", limit=200)
            for rel, nb in neighbours:
                hop = {
                    "uid": nb.uid,
                    "name": nb.name,
                    "kind": nb.kind.value,
                    "via": rel.kind.value,
                    "via_weight": _rel_weight(rel.kind.value),
                }
                if nb.uid == goal:
                    return path + [hop]
                if nb.uid not in visited:
                    visited.add(nb.uid)
                    queue.append((nb.uid, path + [hop]))
        return None

    def _traverse_subcontractor(
        self,
        uid: str,
        name: str,
        chain: list[dict],
        result: list[dict],
        depth: int,
        visited: set[str],
    ) -> None:
        """Recursively explore PARTICIPATED_IN + WORKS_WITH chains."""
        if depth == 0 or uid in visited:
            return
        visited.add(uid)
        neighbours = self._repo.get_neighbors(
            uid,
            direction="both",
            kinds=[
                BizRelationKind.PARTICIPATED_IN,
                BizRelationKind.WORKS_WITH,
                BizRelationKind.CONTRACTED_FOR,
            ],
            limit=50,
        )
        for rel, nb in neighbours:
            if nb.kind != BizEntityKind.COMPANY or nb.uid in visited:
                continue
            new_chain = chain + [{"uid": nb.uid, "name": nb.name, "via": rel.kind.value}]
            if len(new_chain) >= 2:
                result.append(
                    {
                        "chain": new_chain,
                        "length": len(new_chain),
                        "evidence_path": " → ".join(f"{h['name']} [{h['uid']}]" for h in new_chain),
                    }
                )
            self._traverse_subcontractor(nb.uid, nb.name, new_chain, result, depth - 1, visited)

    @staticmethod
    def _build_explanation_text(
        name_a: str,
        name_b: str,
        direct: list[dict],
        buyers: list[dict],
        competitors: list[dict],
        industries: list[dict],
        locations: list[dict],
        co_appear: list[dict],
        path: Optional[list[dict]],
    ) -> str:
        parts: list[str] = []

        if direct:
            kinds = ", ".join(r["kind"] for r in direct[:3])
            parts.append(f"They are directly linked via: {kinds}.")

        if co_appear:
            parts.append(f"They co-appear in {len(co_appear)} shared tender(s)/event(s).")

        if buyers:
            bnames = ", ".join(b["name"] for b in buyers[:3])
            more = f" (+{len(buyers) - 3} more)" if len(buyers) > 3 else ""
            parts.append(f"They share {len(buyers)} common buyer(s): {bnames}{more}.")

        if industries:
            inames = ", ".join(i["name"] for i in industries[:3])
            parts.append(f"Both operate in: {inames}.")

        if locations:
            lnames = ", ".join(loc["name"] for loc in locations[:3])
            parts.append(f"Both are located in: {lnames}.")

        if competitors:
            parts.append(f"They face {len(competitors)} common competitor(s).")

        if path and not direct:
            parts.append(f"Shortest graph path: {len(path) - 1} hop(s) — {_path_str(path)}.")

        if not parts:
            parts.append(f"No direct or indirect connection found between {name_a} and {name_b} in the current graph.")
        else:
            parts.insert(0, f"{name_a} and {name_b} are connected because:")

        return " ".join(parts)
