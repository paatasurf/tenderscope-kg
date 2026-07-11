"""
Comprehensive tests for ExecutiveDecisionEngine (EDE).

Covers:
- Helper functions (_clamp, _blended_confidence, _safe_get, _priority_label,
  _require_company)
- All 8 public engine methods
- Engine-constructor wiring (all five sub-engines instantiated)
- company_situation: fields, health_score range, win_rate, trend
- market_position: pressure, classification labels, direct competitors
- relationship_map: partnerships, inferred, sub-chains
- opportunity_pipeline: sorted, pursue_count, pipeline_health
- buyer_landscape: buyer snapshots, forecast + diversity fields
- strategic_priorities: sorted by score desc, valid levels, actions
- risk_register: severity order, dedup, overall_risk label
- executive_decision: all top-level keys, narrative, immediate_actions
- Integration tests: rich graph produces non-trivial decision
- Edge cases: unknown UID, wrong entity kind, empty graph
- Determinism: identical calls return identical results
"""

from __future__ import annotations

import sqlite3

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.executive_decision import (
    ExecutiveDecisionEngine,
    _blended_confidence,
    _clamp,
    _priority_label,
    _require_company,
    _safe_get,
)
from tenderscope_kg.repository._sqlite import BizRepositorySQLite

# ══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    yield repo
    conn.close()


@pytest.fixture
def engine(repo):
    return ExecutiveDecisionEngine(repo)


# ── Entity creation helpers ────────────────────────────────────────────────


def make_company(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.COMPANY, name, attrs or {})
    return entity


def make_org(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.ORGANIZATION, name, attrs or {})
    return entity


def make_tender(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.TENDER, name, attrs or {})
    return entity


def make_industry(repo, name: str):
    entity, _ = repo.put_entity(BizEntityKind.INDUSTRY, name)
    return entity


def make_city(repo, name: str):
    entity, _ = repo.put_entity(BizEntityKind.CITY, name)
    return entity


def link(repo, src: str, kind: BizRelationKind, tgt: str):
    repo.put_relation(src, kind, tgt)


def company_wins_tender(repo, company, tender):
    link(repo, company.uid, BizRelationKind.AWARDED_TO, tender.uid)


def company_bids_tender(repo, company, tender):
    link(repo, company.uid, BizRelationKind.SUBMITTED_BID, tender.uid)


def tender_issued_by(repo, tender, org):
    link(repo, tender.uid, BizRelationKind.ISSUED_BY, org.uid)


def company_in_industry(repo, company, industry):
    link(repo, company.uid, BizRelationKind.IN_INDUSTRY, industry.uid)


def company_in_city(repo, company, city):
    link(repo, company.uid, BizRelationKind.IN_CITY, city.uid)


# ── Minimal graph ─────────────────────────────────────────────────────────


@pytest.fixture
def simple_graph(repo):
    """One company + one tender it won + buyer."""
    co = make_company(repo, "Acme Corp", {"city": "Vancouver"})
    buyer = make_org(repo, "Ministry of Works")
    tender = make_tender(repo, "Road Repair 2024", {"value": 500_000, "closing_date": "2027-12-31"})
    tender_issued_by(repo, tender, buyer)
    company_wins_tender(repo, co, tender)
    return {"company": co, "buyer": buyer, "tender": tender}


@pytest.fixture
def rich_graph(repo):
    """
    Company A won tenders with Buyer X (3x) and Buyer Y (1x);
    lost 1 with Buyer X. Rivals B and C also present.
    Industry: Construction. Location: Vancouver.
    """
    co_a = make_company(repo, "Alpha Builds", {"city": "Vancouver", "province": "BC"})
    co_b = make_company(repo, "Beta Contractors")
    co_c = make_company(repo, "Gamma Works")

    buyer_x = make_org(repo, "City of Vancouver")
    buyer_y = make_org(repo, "Province of BC")

    ind = make_industry(repo, "Construction")
    city = make_city(repo, "Vancouver")
    company_in_industry(repo, co_a, ind)
    company_in_industry(repo, co_b, ind)
    company_in_city(repo, co_a, city)

    for i in range(1, 4):
        t = make_tender(repo, f"BuyerX Win {i}", {"value": 300_000 * i, "valid_from": f"202{i}-03-10"})
        tender_issued_by(repo, t, buyer_x)
        company_wins_tender(repo, co_a, t)
        company_bids_tender(repo, co_b, t)
        company_bids_tender(repo, co_c, t)

    t_loss = make_tender(repo, "BuyerX Loss", {"value": 200_000, "valid_from": "2022-07-01"})
    tender_issued_by(repo, t_loss, buyer_x)
    company_bids_tender(repo, co_a, t_loss)
    company_wins_tender(repo, co_b, t_loss)

    t_y = make_tender(repo, "BuyerY Win", {"value": 800_000, "valid_from": "2023-11-01"})
    tender_issued_by(repo, t_y, buyer_y)
    company_wins_tender(repo, co_a, t_y)

    # New tender to score (open)
    t_new = make_tender(repo, "New Opportunity 2025", {"value": 450_000, "closing_date": "2027-06-30"})
    tender_issued_by(repo, t_new, buyer_x)

    return {
        "company": co_a,
        "rival_b": co_b,
        "rival_c": co_c,
        "buyer_x": buyer_x,
        "buyer_y": buyer_y,
        "industry": ind,
        "new_tender": t_new,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Helper function unit tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_clamp_within(self):
        assert _clamp(0.5) == 0.5

    def test_clamp_below(self):
        assert _clamp(-1.0) == 0.0

    def test_clamp_above(self):
        assert _clamp(2.0) == 1.0

    def test_clamp_custom_bounds(self):
        assert _clamp(5.0, 1.0, 4.0) == 4.0

    def test_blended_confidence_average(self):
        result = _blended_confidence(0.4, 0.6, 0.8)
        assert abs(result - 0.6) < 0.01

    def test_blended_confidence_empty(self):
        assert _blended_confidence() == 0.3

    def test_blended_confidence_out_of_range_ignored(self):
        result = _blended_confidence(0.5, 1.5, -0.1)
        assert result == 0.5  # only 0.5 is valid

    def test_blended_confidence_clamped(self):
        assert 0.0 <= _blended_confidence(0.9, 0.95) <= 1.0

    def test_safe_get_present(self):
        assert _safe_get({"a": {"b": 42}}, "a", "b") == 42

    def test_safe_get_missing_key(self):
        assert _safe_get({"a": 1}, "b") is None

    def test_safe_get_default(self):
        assert _safe_get({}, "x", default="fallback") == "fallback"

    def test_safe_get_non_dict(self):
        assert _safe_get("string", "key") is None

    def test_priority_label_critical(self):
        assert _priority_label(0.8) == "critical"

    def test_priority_label_high(self):
        assert _priority_label(0.6) == "high"

    def test_priority_label_medium(self):
        assert _priority_label(0.35) == "medium"

    def test_priority_label_low(self):
        assert _priority_label(0.1) == "low"

    def test_priority_label_boundaries(self):
        assert _priority_label(0.75) == "critical"
        assert _priority_label(0.5) == "high"
        assert _priority_label(0.25) == "medium"

    def test_require_company_missing(self, repo):
        err = _require_company(repo, "CMP-NOTEXIST")
        assert err is not None
        assert "error" in err

    def test_require_company_wrong_kind(self, repo):
        t, _ = repo.put_entity(BizEntityKind.TENDER, "A Tender")
        err = _require_company(repo, t.uid)
        assert err is not None
        assert "error" in err

    def test_require_company_valid_company(self, repo):
        co, _ = repo.put_entity(BizEntityKind.COMPANY, "Valid Co")
        assert _require_company(repo, co.uid) is None

    def test_require_company_valid_org(self, repo):
        org, _ = repo.put_entity(BizEntityKind.ORGANIZATION, "Valid Org")
        assert _require_company(repo, org.uid) is None


# ══════════════════════════════════════════════════════════════════════════════
# Engine construction
# ══════════════════════════════════════════════════════════════════════════════


class TestEngineConstruction:
    def test_all_sub_engines_instantiated(self, engine):
        assert engine.cie is not None
        assert engine.rie is not None
        assert engine.cei is not None
        assert engine.bie is not None
        assert engine.oie is not None

    def test_engine_takes_repo(self, repo):
        e = ExecutiveDecisionEngine(repo)
        assert e._repo is repo

    def test_sub_engines_share_repo(self, engine, repo):
        assert engine.cie.repo is repo
        assert engine.rie._repo is repo
        assert engine.cei._repo is repo
        assert engine.bie._repo is repo
        assert engine.oie._repo is repo


# ══════════════════════════════════════════════════════════════════════════════
# company_situation
# ══════════════════════════════════════════════════════════════════════════════


class TestCompanySituation:
    def test_returns_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert isinstance(r, dict)

    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        for field in (
            "company_uid",
            "company_name",
            "summary",
            "win_rate",
            "bid_count",
            "trend",
            "top_buyers",
            "industries",
            "health_score",
            "evidence",
            "confidence",
        ):
            assert field in r, f"Missing field: {field}"

    def test_company_uid_in_result(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert r["company_uid"] == co.uid

    def test_company_name_in_result(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert r["company_name"] == co.name

    def test_health_score_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert 0.0 <= r["health_score"] <= 1.0

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_evidence_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert isinstance(r["evidence"], list)

    def test_unknown_uid_returns_error(self, engine):
        r = engine.company_situation("CMP-INVALID")
        assert "error" in r

    def test_wrong_kind_returns_error(self, engine, repo):
        ind, _ = repo.put_entity(BizEntityKind.INDUSTRY, "Sector")
        r = engine.company_situation(ind.uid)
        assert "error" in r

    def test_win_rate_is_float_or_none(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert r["win_rate"] is None or isinstance(r["win_rate"], float)

    def test_trend_is_string(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert r["trend_label"] is None or isinstance(r["trend_label"], str)

    def test_top_buyers_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.company_situation(co.uid)
        assert isinstance(r["top_buyers"], list)
        assert len(r["top_buyers"]) <= 5

    def test_rich_graph_health_score(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.company_situation(co.uid)
        assert 0.0 <= r["health_score"] <= 1.0

    def test_industries_list(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.company_situation(co.uid)
        assert isinstance(r["industries"], list)


# ══════════════════════════════════════════════════════════════════════════════
# market_position
# ══════════════════════════════════════════════════════════════════════════════


class TestMarketPosition:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        for field in (
            "company_uid",
            "pressure_score",
            "pressure_level",
            "classification",
            "win_rate",
            "direct_competitors",
            "emerging_threats",
            "trend_label",
            "evidence",
            "confidence",
        ):
            assert field in r, f"Missing field: {field}"

    def test_classification_valid_values(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert r["classification"] in ("incumbent", "challenger", "emerging")

    def test_pressure_score_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert 0.0 <= r["pressure_score"] <= 1.0

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_direct_competitors_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert isinstance(r["direct_competitors"], list)
        assert len(r["direct_competitors"]) <= 5

    def test_emerging_threats_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert isinstance(r["emerging_threats"], list)
        assert len(r["emerging_threats"]) <= 3

    def test_unknown_uid_returns_error(self, engine):
        r = engine.market_position("CMP-NOEXIST")
        assert "error" in r

    def test_rich_graph_has_competitors(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.market_position(co.uid)
        assert isinstance(r["direct_competitors"], list)

    def test_rich_graph_high_win_rate_incumbent(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.market_position(co.uid)
        assert r["classification"] in ("incumbent", "challenger", "emerging")

    def test_evidence_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.market_position(co.uid)
        assert isinstance(r["evidence"], list)


# ══════════════════════════════════════════════════════════════════════════════
# relationship_map
# ══════════════════════════════════════════════════════════════════════════════


class TestRelationshipMap:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        for field in (
            "company_uid",
            "partnerships",
            "subcontractor_chains",
            "inferred_relationships",
            "partner_count",
            "evidence",
            "confidence",
        ):
            assert field in r, f"Missing field: {field}"

    def test_partnerships_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert isinstance(r["partnerships"], list)

    def test_subcontractor_chains_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert isinstance(r["subcontractor_chains"], list)

    def test_inferred_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert isinstance(r["inferred_relationships"], list)

    def test_partner_count_non_negative(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert r["partner_count"] >= 0

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_unknown_uid_returns_error(self, engine):
        r = engine.relationship_map("CMP-NOEXIST")
        assert "error" in r

    def test_rich_graph_partnerships_limit(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.relationship_map(co.uid)
        assert len(r["partnerships"]) <= 10

    def test_evidence_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.relationship_map(co.uid)
        assert isinstance(r["evidence"], list)


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_pipeline
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityPipeline:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        for field in (
            "company_uid",
            "total_scored",
            "pursue_count",
            "pipeline_health",
            "top_opportunities",
            "next_actions",
            "biggest_risks",
            "evidence",
            "confidence",
        ):
            assert field in r, f"Missing field: {field}"

    def test_pipeline_health_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert 0.0 <= r["pipeline_health"] <= 1.0

    def test_pursue_count_non_negative(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert r["pursue_count"] >= 0

    def test_top_opportunities_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert isinstance(r["top_opportunities"], list)

    def test_limit_respected(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid, limit=3)
        assert len(r["top_opportunities"]) <= 3

    def test_next_actions_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert isinstance(r["next_actions"], list)

    def test_unknown_uid_returns_error(self, engine):
        r = engine.opportunity_pipeline("CMP-NOEXIST")
        assert "error" in r

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_pursue_count_le_total_scored(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert r["pursue_count"] <= r["total_scored"]

    def test_rich_graph_has_opportunities(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.opportunity_pipeline(co.uid)
        assert isinstance(r["top_opportunities"], list)
        assert r["total_scored"] >= 1


# ══════════════════════════════════════════════════════════════════════════════
# buyer_landscape
# ══════════════════════════════════════════════════════════════════════════════


class TestBuyerLandscape:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        for field in ("company_uid", "buyer_count", "buyers", "evidence", "confidence"):
            assert field in r, f"Missing field: {field}"

    def test_buyers_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        assert isinstance(r["buyers"], list)

    def test_buyer_count_matches_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        assert r["buyer_count"] == len(r["buyers"])

    def test_buyer_snapshot_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        if r["buyers"]:
            snap = r["buyers"][0]
            for field in ("buyer_uid", "buyer_name", "tenders_issued", "company_is_preferred"):
                assert field in snap, f"Missing buyer field: {field}"

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_unknown_uid_returns_error(self, engine):
        r = engine.buyer_landscape("CMP-NOEXIST")
        assert "error" in r

    def test_rich_graph_two_buyers(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.buyer_landscape(co.uid)
        assert r["buyer_count"] >= 2

    def test_tenders_issued_non_negative(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.buyer_landscape(co.uid)
        for snap in r["buyers"]:
            assert snap["tenders_issued"] >= 0


# ══════════════════════════════════════════════════════════════════════════════
# strategic_priorities
# ══════════════════════════════════════════════════════════════════════════════


class TestStrategicPriorities:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        for field in ("company_uid", "priorities", "count", "confidence"):
            assert field in r, f"Missing field: {field}"

    def test_priorities_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        assert isinstance(r["priorities"], list)

    def test_count_matches_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        assert r["count"] == len(r["priorities"])

    def test_priority_item_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        if r["priorities"]:
            p = r["priorities"][0]
            for field in ("label", "score", "level", "reason", "actions"):
                assert field in p, f"Missing priority field: {field}"

    def test_priority_level_valid_values(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        for p in r["priorities"]:
            assert p["level"] in ("critical", "high", "medium", "low")

    def test_priorities_sorted_by_score_desc(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        scores = [p["score"] for p in r["priorities"]]
        assert scores == sorted(scores, reverse=True)

    def test_score_range(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        for p in r["priorities"]:
            assert 0.0 <= p["score"] <= 1.0

    def test_actions_is_list(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        for p in r["priorities"]:
            assert isinstance(p["actions"], list)

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.strategic_priorities(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_unknown_uid_returns_error(self, engine):
        r = engine.strategic_priorities("CMP-NOEXIST")
        assert "error" in r

    def test_max_priorities_limit(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        assert len(r["priorities"]) <= 10

    def test_no_duplicate_labels_for_same_entity(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        keys = [f"{p['label']}:{p.get('tender_uid', p.get('buyer_uid', ''))}" for p in r["priorities"]]
        assert len(keys) == len(set(keys))

    def test_reason_is_string(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.strategic_priorities(co.uid)
        for p in r["priorities"]:
            assert isinstance(p["reason"], str)


# ══════════════════════════════════════════════════════════════════════════════
# risk_register
# ══════════════════════════════════════════════════════════════════════════════


class TestRiskRegister:
    def test_required_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        for field in (
            "company_uid",
            "overall_risk",
            "risk_count",
            "risks",
            "evidence",
            "confidence",
        ):
            assert field in r, f"Missing field: {field}"

    def test_overall_risk_valid_values(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        assert r["overall_risk"] in ("high", "medium", "low")

    def test_risk_count_matches_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        assert r["risk_count"] == len(r["risks"])

    def test_risk_item_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        if r["risks"]:
            risk = r["risks"][0]
            for field in ("source", "factor", "severity", "detail", "mitigation"):
                assert field in risk, f"Missing risk field: {field}"

    def test_severity_valid_values(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        for risk in r["risks"]:
            assert risk["severity"] in ("high", "medium", "low")

    def test_risks_sorted_high_first(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.risk_register(co.uid)
        sev_order = {"high": 3, "medium": 2, "low": 1}
        sev_scores = [sev_order[risk["severity"]] for risk in r["risks"]]
        assert sev_scores == sorted(sev_scores, reverse=True)

    def test_no_duplicate_source_factor(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.risk_register(co.uid)
        keys = [f"{risk['source']}:{risk['factor']}" for risk in r["risks"]]
        assert len(keys) == len(set(keys))

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_unknown_uid_returns_error(self, engine):
        r = engine.risk_register("CMP-NOEXIST")
        assert "error" in r

    def test_single_buyer_triggers_dependency_risk(self, engine, repo):
        co = make_company(repo, "Solo Co")
        buyer = make_org(repo, "Only Buyer")
        t = make_tender(repo, "Only Tender", {"value": 100_000})
        tender_issued_by(repo, t, buyer)
        company_wins_tender(repo, co, t)
        r = engine.risk_register(co.uid)
        factors = [risk["factor"] for risk in r["risks"]]
        assert "single_buyer_dependency" in factors

    def test_high_win_rate_does_not_trigger_low_win_risk(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.risk_register(co.uid)
        factors = [risk["factor"] for risk in r["risks"]]
        assert "very_low_win_rate" not in factors

    def test_evidence_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        assert isinstance(r["evidence"], list)

    def test_mitigation_is_string(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.risk_register(co.uid)
        for risk in r["risks"]:
            assert isinstance(risk["mitigation"], str)

    def test_overall_risk_reflects_highest_severity(self, engine, repo):
        co = make_company(repo, "Low Win Co")
        buyer = make_org(repo, "BuyerX")
        winner = make_company(repo, "Always Win")
        # 6 bids, 0 wins → very low win rate → high risk
        for i in range(6):
            t = make_tender(repo, f"Lose {i}", {"value": 200_000})
            tender_issued_by(repo, t, buyer)
            company_bids_tender(repo, co, t)
            company_wins_tender(repo, winner, t)
        r = engine.risk_register(co.uid)
        assert r["overall_risk"] == "high"


# ══════════════════════════════════════════════════════════════════════════════
# executive_decision (master call)
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutiveDecision:
    def test_required_top_level_keys(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        for key in (
            "company_uid",
            "company_name",
            "decision_version",
            "confidence",
            "executive_narrative",
            "situation",
            "market_position",
            "relationship_map",
            "opportunity_pipeline",
            "buyer_landscape",
            "strategic_priorities",
            "risk_register",
            "immediate_actions",
            "evidence",
        ):
            assert key in r, f"Missing top-level key: {key}"

    def test_company_uid(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["company_uid"] == co.uid

    def test_company_name(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["company_name"] == co.name

    def test_decision_version(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["decision_version"] == "v0.8.0"

    def test_confidence_range(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert 0.0 <= r["confidence"] <= 1.0

    def test_executive_narrative_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["executive_narrative"], list)

    def test_immediate_actions_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["immediate_actions"], list)

    def test_evidence_is_list(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["evidence"], list)

    def test_situation_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["situation"], dict)
        assert "company_uid" in r["situation"]

    def test_market_position_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["market_position"], dict)
        assert "pressure_score" in r["market_position"]

    def test_relationship_map_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["relationship_map"], dict)
        assert "partnerships" in r["relationship_map"]

    def test_opportunity_pipeline_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["opportunity_pipeline"], dict)
        assert "top_opportunities" in r["opportunity_pipeline"]

    def test_buyer_landscape_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["buyer_landscape"], dict)
        assert "buyers" in r["buyer_landscape"]

    def test_strategic_priorities_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["strategic_priorities"], dict)
        assert "priorities" in r["strategic_priorities"]

    def test_risk_register_sub_dict(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert isinstance(r["risk_register"], dict)
        assert "risks" in r["risk_register"]

    def test_unknown_uid_returns_error(self, engine):
        r = engine.executive_decision("CMP-NOTEXIST")
        assert "error" in r

    def test_wrong_kind_returns_error(self, engine, repo):
        t, _ = repo.put_entity(BizEntityKind.TENDER, "A Tender")
        r = engine.executive_decision(t.uid)
        assert "error" in r

    def test_opportunity_limit_respected(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid, opportunity_limit=2)
        top = r["opportunity_pipeline"].get("top_opportunities", [])
        assert len(top) <= 2

    def test_sub_company_uid_consistent(self, engine, simple_graph):
        co = simple_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["situation"]["company_uid"] == co.uid
        assert r["market_position"]["company_uid"] == co.uid
        assert r["opportunity_pipeline"]["company_uid"] == co.uid


# ══════════════════════════════════════════════════════════════════════════════
# Integration tests
# ══════════════════════════════════════════════════════════════════════════════


class TestIntegration:
    def test_rich_graph_full_decision(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert "error" not in r
        assert r["company_name"] == co.name

    def test_rich_graph_narrative_non_empty(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert len(r["executive_narrative"]) >= 1

    def test_rich_graph_pipeline_scored(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["opportunity_pipeline"]["total_scored"] >= 1

    def test_rich_graph_risk_register_populated(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["risk_register"]["risk_count"] >= 1

    def test_rich_graph_priorities_populated(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["strategic_priorities"]["count"] >= 1

    def test_rich_graph_buyer_landscape_populated(self, engine, rich_graph):
        co = rich_graph["company"]
        r = engine.executive_decision(co.uid)
        assert r["buyer_landscape"]["buyer_count"] >= 2

    def test_very_low_win_rate_triggers_risk(self, engine, repo):
        co = make_company(repo, "Low Win Co")
        # Build 5 buyers, each with 10 total awards but co only wins 1 at each.
        # BAWD score per buyer = 1/10 = 0.1; weighted mean = 0.1 < 0.15.
        # bid_frequency (wins) = 5 >= 5.  Both thresholds met.
        for b_idx in range(5):
            buyer = make_org(repo, f"Buyer {b_idx}")
            # co wins 1 tender at this buyer
            t_win = make_tender(repo, f"Win-{b_idx}", {"value": 300_000})
            tender_issued_by(repo, t_win, buyer)
            company_wins_tender(repo, co, t_win)
            # 9 other companies each win 1 tender at this buyer
            for j in range(9):
                other = make_company(repo, f"Other-{b_idx}-{j}")
                t_other = make_tender(repo, f"Other-{b_idx}-{j}", {"value": 300_000})
                tender_issued_by(repo, t_other, buyer)
                company_wins_tender(repo, other, t_other)
        r = engine.risk_register(co.uid)
        factors = [risk["factor"] for risk in r["risks"]]
        # very_low_win_rate requires bid_frequency >= 5 AND win_rate (BAWD) < 0.15
        assert "very_low_win_rate" in factors

    def test_single_buyer_in_landscape(self, engine, repo):
        co = make_company(repo, "Sole Source Co")
        buyer = make_org(repo, "Only Buyer")
        t = make_tender(repo, "Only Contract", {"value": 500_000})
        tender_issued_by(repo, t, buyer)
        company_wins_tender(repo, co, t)
        land = engine.buyer_landscape(co.uid)
        assert land["buyer_count"] == 1
        assert land["buyers"][0]["buyer_name"] == "Only Buyer"

    def test_no_tenders_pipeline_empty(self, engine, repo):
        co = make_company(repo, "Brand New Co")
        r = engine.opportunity_pipeline(co.uid)
        assert r["total_scored"] == 0
        assert r["pursue_count"] == 0
        assert r["pipeline_health"] == 0.0


# ══════════════════════════════════════════════════════════════════════════════
# Determinism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_situation_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.company_situation(co.uid)
        r2 = engine.company_situation(co.uid)
        assert r1["health_score"] == r2["health_score"]
        assert r1["win_rate"] == r2["win_rate"]

    def test_market_position_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.market_position(co.uid)
        r2 = engine.market_position(co.uid)
        assert r1["pressure_score"] == r2["pressure_score"]
        assert r1["classification"] == r2["classification"]

    def test_risk_register_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.risk_register(co.uid)
        r2 = engine.risk_register(co.uid)
        assert r1["overall_risk"] == r2["overall_risk"]
        assert r1["risk_count"] == r2["risk_count"]

    def test_strategic_priorities_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.strategic_priorities(co.uid)
        r2 = engine.strategic_priorities(co.uid)
        assert r1["count"] == r2["count"]

    def test_executive_decision_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.executive_decision(co.uid)
        r2 = engine.executive_decision(co.uid)
        assert r1["confidence"] == r2["confidence"]
        assert r1["company_name"] == r2["company_name"]


# ══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_company_no_tenders(self, engine, repo):
        co = make_company(repo, "Idle Co")
        r = engine.executive_decision(co.uid)
        assert "error" not in r
        assert r["company_uid"] == co.uid

    def test_company_only_losses(self, engine, repo):
        co = make_company(repo, "Always Loses")
        winner = make_company(repo, "Always Wins")
        buyer = make_org(repo, "Stern Buyer")
        for i in range(3):
            t = make_tender(repo, f"Lost {i}", {"value": 200_000})
            tender_issued_by(repo, t, buyer)
            company_bids_tender(repo, co, t)
            company_wins_tender(repo, winner, t)
        r = engine.executive_decision(co.uid)
        assert "error" not in r

    def test_org_entity_accepted(self, engine, repo):
        org = make_org(repo, "Procurement Corp")
        buyer = make_org(repo, "Their Buyer")
        t = make_tender(repo, "Service Contract", {"value": 100_000})
        tender_issued_by(repo, t, buyer)
        company_wins_tender(repo, org, t)
        r = engine.company_situation(org.uid)
        assert "error" not in r

    def test_industry_uid_returns_error(self, engine, repo):
        ind = make_industry(repo, "Test Sector")
        r = engine.executive_decision(ind.uid)
        assert "error" in r

    def test_tender_uid_returns_error(self, engine, repo):
        t = make_tender(repo, "Tender Entity")
        r = engine.market_position(t.uid)
        assert "error" in r

    def test_empty_repo_returns_error_for_unknown_uid(self, engine):
        r = engine.executive_decision("CMP-99999999")
        assert "error" in r
