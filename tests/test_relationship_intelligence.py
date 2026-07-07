"""
Tests for RelationshipIntelligenceEngine.

Uses in-memory SQLite graphs built with BizRepository directly.
No file I/O, no external dependencies.
"""
from __future__ import annotations

import sqlite3

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.repository._base import BizRepository
from tenderscope_kg.repository._sqlite import BizRepositorySQLite
from tenderscope_kg.relationship_intelligence import (
    RelationshipIntelligenceEngine,
    _confidence_from_strength,
    _path_str,
    _rel_weight,
    _strength_from_evidence,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def repo() -> BizRepositorySQLite:
    """Fresh in-memory BizRepositorySQLite."""
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    return repo


@pytest.fixture
def rie(repo: BizRepository) -> RelationshipIntelligenceEngine:
    return RelationshipIntelligenceEngine(repo)


# ── Shared graph builder helpers ──────────────────────────────────────────────

def make_company(repo: BizRepository, name: str) -> str:
    e, _ = repo.put_entity(BizEntityKind.COMPANY, name)
    return e.uid


def make_tender(repo: BizRepository, name: str, attrs: dict | None = None) -> str:
    e, _ = repo.put_entity(BizEntityKind.TENDER, name, attributes=attrs or {})
    return e.uid


def make_org(repo: BizRepository, name: str) -> str:
    e, _ = repo.put_entity(BizEntityKind.ORGANIZATION, name)
    return e.uid


def make_industry(repo: BizRepository, name: str) -> str:
    e, _ = repo.put_entity(BizEntityKind.INDUSTRY, name)
    return e.uid


def make_city(repo: BizRepository, name: str) -> str:
    e, _ = repo.put_entity(BizEntityKind.CITY, name)
    return e.uid


def make_province(repo: BizRepository, name: str) -> str:
    e, _ = repo.put_entity(BizEntityKind.PROVINCE, name)
    return e.uid


def link(repo: BizRepository, src: str, kind: BizRelationKind, tgt: str,
         confidence: float = 1.0, attrs: dict | None = None) -> None:
    repo.put_relation(src, kind, tgt, confidence=confidence, attributes=attrs or {})


# ── Utility / helper unit tests ───────────────────────────────────────────────

class TestHelpers:
    def test_rel_weight_known(self):
        assert _rel_weight("awarded_to") == 1.0

    def test_rel_weight_unknown(self):
        assert _rel_weight("nonexistent_kind") == 0.3

    def test_strength_empty(self):
        assert _strength_from_evidence([]) == 0.0

    def test_strength_single_high(self):
        s = _strength_from_evidence([{"relation": "awarded_to"}])
        assert s == 1.0

    def test_strength_multiple_decays(self):
        ev = [{"relation": "awarded_to"}] * 5
        s = _strength_from_evidence(ev)
        assert 0.0 < s <= 1.0

    def test_strength_capped_at_one(self):
        ev = [{"relation": "awarded_to"}] * 100
        s = _strength_from_evidence(ev)
        assert s == 1.0

    def test_confidence_zero_strength(self):
        c = _confidence_from_strength(0.0, 0)
        assert c == 0.0

    def test_confidence_increases_with_evidence(self):
        c1 = _confidence_from_strength(0.5, 1)
        c3 = _confidence_from_strength(0.5, 3)
        assert c3 > c1

    def test_confidence_capped(self):
        c = _confidence_from_strength(1.0, 100)
        assert c == 1.0

    def test_path_str_single(self):
        path = [{"uid": "A", "name": "Alpha"}]
        assert "Alpha" in _path_str(path)

    def test_path_str_multi(self):
        path = [
            {"uid": "A", "name": "Alpha"},
            {"uid": "B", "name": "Beta", "via": "awarded_to"},
        ]
        s = _path_str(path)
        assert "Alpha" in s
        assert "Beta" in s
        assert "awarded_to" in s


# ── explain() ────────────────────────────────────────────────────────────────

class TestExplain:
    def test_missing_uid_a(self, rie: RelationshipIntelligenceEngine):
        r = rie.explain("CMP-MISSING", "CMP-OTHER")
        assert "error" in r

    def test_missing_uid_b(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        r = rie.explain(a, "CMP-MISSING")
        assert "error" in r

    def test_direct_relation_detected(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.explain(a, b)
        assert r["direct_relations"]
        assert r["direct_relations"][0]["kind"] == "works_with"

    def test_shared_buyer_detected(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        org = make_org(repo, "City Hall")
        t1 = make_tender(repo, "T-Alpha")
        t2 = make_tender(repo, "T-Beta")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        r = rie.explain(a, b)
        assert r["shared_buyers"]
        assert any(sb["uid"] == org for sb in r["shared_buyers"])

    def test_shared_industry(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        ind = make_industry(repo, "Construction")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.explain(a, b)
        assert any(i["uid"] == ind for i in r["shared_industries"])

    def test_shared_location(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        city = make_city(repo, "Vancouver")
        link(repo, a, BizRelationKind.IN_CITY, city)
        link(repo, b, BizRelationKind.IN_CITY, city)
        r = rie.explain(a, b)
        assert any(l["uid"] == city for l in r["shared_locations"])

    def test_explanation_text_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.explain(a, b)
        assert isinstance(r["explanation_text"], str)
        assert len(r["explanation_text"]) > 0

    def test_explanation_no_connection_text(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Isolated")
        r = rie.explain(a, b)
        assert "No direct or indirect" in r["explanation_text"]

    def test_strength_nonzero_with_evidence(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.AWARDED_TO,
             make_tender(repo, "T1"))
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.explain(a, b)
        assert r["relationship_strength"] > 0

    def test_evidence_paths_list(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.explain(a, b)
        assert isinstance(r["evidence_paths"], list)
        assert len(r["evidence_paths"]) >= 1

    def test_co_appearances(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        t = make_tender(repo, "SharedTender")
        link(repo, a, BizRelationKind.AWARDED_TO, t)
        link(repo, b, BizRelationKind.SUBMITTED_BID, t)
        r = rie.explain(a, b)
        assert any(ca["uid"] == t for ca in r["recurring_co_appearances"])


# ── relationship_strength() ───────────────────────────────────────────────────

class TestRelationshipStrength:
    def test_missing_entity(self, rie: RelationshipIntelligenceEngine):
        r = rie.relationship_strength("CMP-MISSING", "CMP-OTHER")
        assert "error" in r

    def test_zero_strength_no_connection(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Isolated")
        r = rie.relationship_strength(a, b)
        assert r["relationship_strength"] == 0.0
        assert r["confidence"] == 0.0

    def test_breakdown_keys_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        r = rie.relationship_strength(a, b)
        for key in ("direct_relations", "shared_buyers", "shared_competitors",
                    "shared_industries", "shared_locations", "co_appearances"):
            assert key in r["breakdown"]

    def test_strength_with_direct_link(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.AWARDED_TO, b)
        r = rie.relationship_strength(a, b)
        assert r["relationship_strength"] > 0
        assert r["breakdown"]["direct_relations"]["count"] == 1

    def test_strength_increases_with_more_evidence(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b1 = make_company(repo, "Beta1")
        b2 = make_company(repo, "Beta2")
        link(repo, a, BizRelationKind.WORKS_WITH, b1)
        r1 = rie.relationship_strength(a, b1)
        ind = make_industry(repo, "Tech")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b2, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, a, BizRelationKind.WORKS_WITH, b2)
        r2 = rie.relationship_strength(a, b2)
        assert r2["relationship_strength"] >= r1["relationship_strength"]


# ── shortest_path() ───────────────────────────────────────────────────────────

class TestShortestPath:
    def test_same_entity(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.shortest_path(a, b)
        assert r["found"]
        assert r["hop_count"] == 1

    def test_missing_start(self, rie: RelationshipIntelligenceEngine):
        r = rie.shortest_path("CMP-MISSING", "CMP-OTHER")
        assert "error" in r

    def test_direct_path(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.shortest_path(a, b)
        assert r["found"]
        assert r["hop_count"] == 1
        assert r["path"][1]["via"] == "works_with"

    def test_two_hop_path(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        mid = make_company(repo, "Middle")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, mid)
        link(repo, mid, BizRelationKind.WORKS_WITH, b)
        r = rie.shortest_path(a, b)
        assert r["found"]
        assert r["hop_count"] == 2

    def test_no_path_found(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Isolated")
        r = rie.shortest_path(a, b, max_depth=3)
        assert not r["found"]
        assert r["hop_count"] == 0

    def test_path_string_present(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        r = rie.shortest_path(a, b)
        assert isinstance(r["path_string"], str)
        assert "Alpha" in r["path_string"]

    def test_hop_weights_present(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        link(repo, a, BizRelationKind.AWARDED_TO, b)
        r = rie.shortest_path(a, b)
        assert r["found"]
        assert "via_weight" in r["path"][1]


# ── infer_relationships() ─────────────────────────────────────────────────────

class TestInferRelationships:
    def test_missing_entity(self, rie: RelationshipIntelligenceEngine):
        r = rie.infer_relationships("CMP-MISSING")
        assert "error" in r

    def test_empty_graph(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Lonely")
        r = rie.infer_relationships(a)
        assert r["inferred_count"] == 0
        assert r["shared_buyer_links"] == []

    def test_shared_buyer_link_inferred(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        org = make_org(repo, "Gov Dept")
        t1 = make_tender(repo, "T-Alpha-2")
        t2 = make_tender(repo, "T-Beta-2")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        r = rie.infer_relationships(a)
        assert any(x["uid"] == b for x in r["shared_buyer_links"])

    def test_subcontractor_hint_inferred(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        sub = make_company(repo, "Subco")
        winner = make_company(repo, "BigCo")
        tender = make_tender(repo, "T-Big")
        link(repo, sub, BizRelationKind.PARTICIPATED_IN, tender)
        link(repo, winner, BizRelationKind.AWARDED_TO, tender)
        r = rie.infer_relationships(sub)
        assert any(x["uid"] == winner for x in r["subcontractor_hints"])

    def test_partnership_hint_requires_min2(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        t1 = make_tender(repo, "T-P1")
        t2 = make_tender(repo, "T-P2")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, b, BizRelationKind.AWARDED_TO, t1)
        link(repo, a, BizRelationKind.AWARDED_TO, t2)
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        r = rie.infer_relationships(a)
        assert any(x["uid"] == b for x in r["partnership_hints"])

    def test_partnership_hint_single_event_excluded(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha2")
        b = make_company(repo, "Beta2")
        t1 = make_tender(repo, "T-Single")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, b, BizRelationKind.AWARDED_TO, t1)
        r = rie.infer_relationships(a)
        assert not any(x["uid"] == b for x in r["partnership_hints"])

    def test_industry_cluster_peer(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        ind = make_industry(repo, "Roofing")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.infer_relationships(a)
        assert any(x["uid"] == b for x in r["industry_cluster_peers"])

    def test_geographic_cluster_peer(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        city = make_city(repo, "Kelowna")
        link(repo, a, BizRelationKind.IN_CITY, city)
        link(repo, b, BizRelationKind.IN_CITY, city)
        r = rie.infer_relationships(a)
        assert any(x["uid"] == b for x in r["geographic_cluster_peers"])

    def test_all_inferences_have_strength(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        ind = make_industry(repo, "Plumbing")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.infer_relationships(a)
        for peer in r["industry_cluster_peers"]:
            assert "strength" in peer
            assert "confidence" in peer
            assert "evidence_path" in peer

    def test_limit_respected(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        ind = make_industry(repo, "Big Industry")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        for i in range(10):
            co = make_company(repo, f"Peer-{i}")
            link(repo, co, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.infer_relationships(a, limit=3)
        assert len(r["industry_cluster_peers"]) <= 3


# ── shared_buyers() ───────────────────────────────────────────────────────────

class TestSharedBuyers:
    def test_no_shared_buyers(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        r = rie.shared_buyers(a, b)
        assert r["shared_buyer_count"] == 0

    def test_shared_buyer_found(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        org = make_org(repo, "Ministry")
        t1 = make_tender(repo, "T-SB1")
        t2 = make_tender(repo, "T-SB2")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, b, BizRelationKind.SUBMITTED_BID, t2)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        r = rie.shared_buyers(a, b)
        assert r["shared_buyer_count"] == 1
        assert r["shared_buyers"][0]["uid"] == org

    def test_evidence_path_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        org = make_org(repo, "City")
        t1 = make_tender(repo, "T-SB3")
        t2 = make_tender(repo, "T-SB4")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        r = rie.shared_buyers(a, b)
        assert "evidence_path" in r["shared_buyers"][0]


# ── shared_competitors() ─────────────────────────────────────────────────────

class TestSharedCompetitors:
    def test_no_shared_competitors(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        r = rie.shared_competitors(a, b)
        assert r["shared_competitor_count"] == 0

    def test_shared_competitor_found(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        rival = make_company(repo, "Rival")
        t1 = make_tender(repo, "T-SC1")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, rival, BizRelationKind.SUBMITTED_BID, t1)
        t2 = make_tender(repo, "T-SC2")
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        link(repo, rival, BizRelationKind.SUBMITTED_BID, t2)
        r = rie.shared_competitors(a, b)
        assert rival in [c["uid"] for c in r["shared_competitors"]]


# ── subcontractor_chains() ────────────────────────────────────────────────────

class TestSubcontractorChains:
    def test_missing_entity(self, rie: RelationshipIntelligenceEngine):
        r = rie.subcontractor_chains("CMP-MISSING")
        assert "error" in r

    def test_no_chain(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        a = make_company(repo, "Isolated")
        r = rie.subcontractor_chains(a)
        assert r["chain_count"] == 0

    def test_chain_detected_via_works_with(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Prime")
        b = make_company(repo, "Sub1")
        c = make_company(repo, "Sub2")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        link(repo, b, BizRelationKind.WORKS_WITH, c)
        r = rie.subcontractor_chains(a, depth=2)
        uids_in_chains = {h["uid"] for chain in r["chains"] for h in chain["chain"]}
        assert b in uids_in_chains or c in uids_in_chains
        assert r["chain_count"] > 0

    def test_chain_has_evidence_path(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Prime2")
        b = make_company(repo, "Sub2")
        c = make_company(repo, "Sub3")
        link(repo, a, BizRelationKind.WORKS_WITH, b)
        link(repo, b, BizRelationKind.WORKS_WITH, c)
        r = rie.subcontractor_chains(a, depth=2)
        if r["chain_count"] > 0:
            assert "evidence_path" in r["chains"][0]


# ── recurring_partnerships() ──────────────────────────────────────────────────

class TestRecurringPartnerships:
    def test_missing_entity(self, rie: RelationshipIntelligenceEngine):
        r = rie.recurring_partnerships("CMP-MISSING")
        assert "error" in r

    def test_no_partnerships_single_event(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        t = make_tender(repo, "T-Rec1")
        link(repo, a, BizRelationKind.PARTICIPATED_IN, t)
        link(repo, b, BizRelationKind.PARTICIPATED_IN, t)
        r = rie.recurring_partnerships(a, min_count=2)
        assert r["partnership_count"] == 0

    def test_partnership_detected_multi_event(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        for i in range(3):
            t = make_tender(repo, f"T-Rec-{i}")
            link(repo, a, BizRelationKind.PARTICIPATED_IN, t)
            link(repo, b, BizRelationKind.PARTICIPATED_IN, t)
        r = rie.recurring_partnerships(a, min_count=2)
        assert any(p["uid"] == b for p in r["partnerships"])

    def test_partnership_strength_and_confidence(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        for i in range(3):
            t = make_tender(repo, f"T-RP-{i}")
            link(repo, a, BizRelationKind.PARTICIPATED_IN, t)
            link(repo, b, BizRelationKind.PARTICIPATED_IN, t)
        r = rie.recurring_partnerships(a, min_count=2)
        if r["partnerships"]:
            p = r["partnerships"][0]
            assert "strength" in p
            assert "confidence" in p
            assert "evidence_path" in p

    def test_min_count_respected(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        for i in range(2):
            t = make_tender(repo, f"T-MC-{i}")
            link(repo, a, BizRelationKind.PARTICIPATED_IN, t)
            link(repo, b, BizRelationKind.PARTICIPATED_IN, t)
        r3 = rie.recurring_partnerships(a, min_count=3)
        assert r3["partnership_count"] == 0
        r2 = rie.recurring_partnerships(a, min_count=2)
        assert r2["partnership_count"] >= 1


# ── industry_clusters() ───────────────────────────────────────────────────────

class TestIndustryClusters:
    def test_missing_industry(self, rie: RelationshipIntelligenceEngine):
        r = rie.industry_clusters("IND-MISSING")
        assert "error" in r

    def test_empty_cluster(self, rie: RelationshipIntelligenceEngine, repo: BizRepository):
        ind = make_industry(repo, "EmptyInd")
        r = rie.industry_clusters(ind)
        assert r["company_count"] == 0

    def test_cluster_members(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        ind = make_industry(repo, "Electrical")
        co1 = make_company(repo, "ElecCo1")
        co2 = make_company(repo, "ElecCo2")
        link(repo, co1, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, co2, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.industry_clusters(ind)
        uids = [c["uid"] for c in r["companies"]]
        assert co1 in uids
        assert co2 in uids

    def test_tender_count_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        ind = make_industry(repo, "Plumbing2")
        co = make_company(repo, "PlumbCo")
        t = make_tender(repo, "T-Plumb")
        link(repo, co, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, co, BizRelationKind.AWARDED_TO, t)
        r = rie.industry_clusters(ind)
        assert r["companies"][0]["tender_count"] == 1

    def test_ranked_by_tender_count(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        ind = make_industry(repo, "Roofing2")
        co_big = make_company(repo, "BigRoofer")
        co_small = make_company(repo, "SmallRoofer")
        link(repo, co_big, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, co_small, BizRelationKind.IN_INDUSTRY, ind)
        for i in range(3):
            t = make_tender(repo, f"T-Roof-{i}")
            link(repo, co_big, BizRelationKind.AWARDED_TO, t)
        r = rie.industry_clusters(ind)
        assert r["companies"][0]["uid"] == co_big

    def test_evidence_path_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        ind = make_industry(repo, "HVAC")
        co = make_company(repo, "HVACCo")
        link(repo, co, BizRelationKind.IN_INDUSTRY, ind)
        r = rie.industry_clusters(ind)
        assert "evidence_path" in r["companies"][0]


# ── geographic_clusters() ────────────────────────────────────────────────────

class TestGeographicClusters:
    def test_missing_location(self, rie: RelationshipIntelligenceEngine):
        r = rie.geographic_clusters("CTY-MISSING")
        assert "error" in r

    def test_city_cluster(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        city = make_city(repo, "Victoria")
        co1 = make_company(repo, "VicCo1")
        co2 = make_company(repo, "VicCo2")
        link(repo, co1, BizRelationKind.IN_CITY, city)
        link(repo, co2, BizRelationKind.IN_CITY, city)
        r = rie.geographic_clusters(city)
        uids = [c["uid"] for c in r["companies"]]
        assert co1 in uids
        assert co2 in uids

    def test_province_cluster(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        prov = make_province(repo, "British Columbia")
        co = make_company(repo, "BCCo")
        link(repo, co, BizRelationKind.IN_PROVINCE, prov)
        r = rie.geographic_clusters(prov)
        assert r["location_kind"] == "province"
        assert any(c["uid"] == co for c in r["companies"])

    def test_evidence_path_present(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        city = make_city(repo, "Burnaby")
        co = make_company(repo, "BurnCo")
        link(repo, co, BizRelationKind.IN_CITY, city)
        r = rie.geographic_clusters(city)
        assert "evidence_path" in r["companies"][0]

    def test_sorted_alphabetically(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        city = make_city(repo, "Nanaimo")
        link(repo, make_company(repo, "Zebra Corp"), BizRelationKind.IN_CITY, city)
        link(repo, make_company(repo, "Alpha Corp"), BizRelationKind.IN_CITY, city)
        r = rie.geographic_clusters(city)
        names = [c["name"] for c in r["companies"]]
        assert names == sorted(names)

    def test_name_lookup(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        city = make_city(repo, "Penticton")
        co = make_company(repo, "PenCo")
        link(repo, co, BizRelationKind.IN_CITY, city)
        r = rie.geographic_clusters("Penticton")
        assert r["location_name"] == "Penticton"


# ── organization_influence() ─────────────────────────────────────────────────

class TestOrganizationInfluence:
    def test_missing_org(self, rie: RelationshipIntelligenceEngine):
        r = rie.organization_influence("ORG-MISSING")
        assert "error" in r

    def test_org_no_tenders(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        org = make_org(repo, "EmptyOrg")
        r = rie.organization_influence(org)
        assert r["tender_count"] == 0
        assert r["company_count"] == 0
        assert r["influence_score"] == 0.0

    def test_org_with_tenders_and_companies(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        org = make_org(repo, "City Of Vancouver")
        t1 = make_tender(repo, "T-Inf1")
        t2 = make_tender(repo, "T-Inf2")
        co1 = make_company(repo, "Co1")
        co2 = make_company(repo, "Co2")
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        link(repo, co1, BizRelationKind.AWARDED_TO, t1)
        link(repo, co2, BizRelationKind.AWARDED_TO, t2)
        r = rie.organization_influence(org)
        assert r["tender_count"] == 2
        assert r["company_count"] == 2
        assert r["influence_score"] > 0.0

    def test_influence_score_proportional(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        org = make_org(repo, "Big Ministry")
        for i in range(10):
            t = make_tender(repo, f"T-Inf-Big-{i}")
            co = make_company(repo, f"Co-Inf-{i}")
            link(repo, t, BizRelationKind.ISSUED_BY, org)
            link(repo, co, BizRelationKind.AWARDED_TO, t)
        r = rie.organization_influence(org)
        assert r["influence_score"] == 1.0

    def test_companies_list(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        org = make_org(repo, "Province")
        t = make_tender(repo, "T-Prov")
        co = make_company(repo, "ProvCo")
        link(repo, t, BizRelationKind.ISSUED_BY, org)
        link(repo, co, BizRelationKind.AWARDED_TO, t)
        r = rie.organization_influence(org)
        assert r["companies"][0]["uid"] == co
        assert "evidence_path" in r["companies"][0]


# ── Integration: full explain → strength consistency ─────────────────────────

class TestIntegration:
    def test_explain_strength_consistent(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        org = make_org(repo, "RegionalGov")
        t1 = make_tender(repo, "T-Int1")
        t2 = make_tender(repo, "T-Int2")
        ind = make_industry(repo, "Paving")
        city = make_city(repo, "Abbotsford")
        link(repo, a, BizRelationKind.AWARDED_TO, t1)
        link(repo, t1, BizRelationKind.ISSUED_BY, org)
        link(repo, b, BizRelationKind.AWARDED_TO, t2)
        link(repo, t2, BizRelationKind.ISSUED_BY, org)
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, a, BizRelationKind.IN_CITY, city)
        link(repo, b, BizRelationKind.IN_CITY, city)
        explain = rie.explain(a, b)
        strength = rie.relationship_strength(a, b)
        assert abs(explain["relationship_strength"] - strength["relationship_strength"]) < 0.001

    def test_full_explain_keys(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        r = rie.explain(a, b)
        for key in (
            "uid_a", "uid_b", "direct_relations", "shortest_path",
            "shared_buyers", "shared_competitors", "shared_industries",
            "shared_locations", "recurring_co_appearances",
            "relationship_strength", "confidence", "evidence_count",
            "explanation_text", "evidence_paths",
        ):
            assert key in r, f"Missing key: {key}"

    def test_path_through_tender(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        t = make_tender(repo, "T-Bridge")
        link(repo, a, BizRelationKind.AWARDED_TO, t)
        link(repo, b, BizRelationKind.SUBMITTED_BID, t)
        r = rie.shortest_path(a, b)
        assert r["found"]
        assert r["hop_count"] == 2

    def test_infer_then_explain_coherent(
        self, rie: RelationshipIntelligenceEngine, repo: BizRepository
    ):
        a = make_company(repo, "Alpha")
        b = make_company(repo, "Beta")
        ind = make_industry(repo, "Landscaping")
        link(repo, a, BizRelationKind.IN_INDUSTRY, ind)
        link(repo, b, BizRelationKind.IN_INDUSTRY, ind)
        infer = rie.infer_relationships(a)
        explain = rie.explain(a, b)
        assert any(p["uid"] == b for p in infer["industry_cluster_peers"])
        assert any(i["uid"] == ind for i in explain["shared_industries"])
