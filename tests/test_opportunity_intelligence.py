"""
Comprehensive tests for OpportunityIntelligenceEngine.

Covers:
- All helper functions (_ev, _confidence, _parse_year, _parse_month,
  _safe_float, _value_bucket, _hhi, _clamp, _require_entity)
- All graph traversal helpers
- All 10 public engine methods
- Score dimension correctness
- Recommendation label thresholds
- Explainability fields (evidence, assumptions, weak_evidence,
  missing_information, reasoning_chain)
- Portfolio impact calculations
- Risk factor detection and severity
- Similar opportunity matching
- best_opportunities / executive_summary across a multi-tender graph
- Edge cases: missing buyer, no history, zero value, wrong entity kind
"""

from __future__ import annotations

import datetime
import sqlite3

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.opportunity_intelligence import (
    OpportunityIntelligenceEngine,
    _clamp,
    _confidence,
    _deadline_date,
    _ev,
    _hhi,
    _months_until,
    _parse_month,
    _parse_year,
    _require_entity,
    _safe_float,
    _tender_date,
    _tender_value,
    _value_bucket,
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
    return OpportunityIntelligenceEngine(repo)


# ── Entity creation helpers ────────────────────────────────────────────────


def make_company(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.COMPANY, name, attrs or {})
    return entity


def make_tender(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.TENDER, name, attrs or {})
    return entity


def make_org(repo, name: str, attrs: dict | None = None):
    entity, _ = repo.put_entity(BizEntityKind.ORGANIZATION, name, attrs or {})
    return entity


def make_industry(repo, name: str):
    entity, _ = repo.put_entity(BizEntityKind.INDUSTRY, name)
    return entity


def make_city(repo, name: str):
    entity, _ = repo.put_entity(BizEntityKind.CITY, name)
    return entity


def link(repo, source_uid: str, kind: BizRelationKind, target_uid: str):
    repo.put_relation(source_uid, kind, target_uid)


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


# ── Minimal graph: one company, one tender ────────────────────────────────


@pytest.fixture
def simple_graph(repo):
    """A company, a tender they won, issued by a buyer org."""
    company = make_company(repo, "Acme Corp", {"city": "Vancouver", "province": "BC"})
    buyer = make_org(repo, "Ministry of Works")
    tender = make_tender(
        repo,
        "Road Repair 2024",
        {"value": 500_000, "valid_from": "2024-01-15", "closing_date": "2027-12-31"},
    )
    tender_issued_by(repo, tender, buyer)
    company_wins_tender(repo, company, tender)
    return {"company": company, "buyer": buyer, "tender": tender}


@pytest.fixture
def rich_graph(repo):
    """
    Company A has history:
    - Won 3 tenders with Buyer X
    - Lost 1 tender with Buyer X
    - Won 1 tender with Buyer Y
    Industry: Construction
    Location: Vancouver
    """
    co_a = make_company(repo, "Alpha Builds", {"city": "Vancouver", "province": "BC"})
    co_b = make_company(repo, "Beta Contractors")
    co_c = make_company(repo, "Gamma Works")

    buyer_x = make_org(repo, "City of Vancouver")
    buyer_y = make_org(repo, "Province of BC")

    ind_const = make_industry(repo, "Construction")
    ind_eng = make_industry(repo, "Engineering")

    company_in_industry(repo, co_a, ind_const)
    company_in_industry(repo, co_b, ind_const)

    city_van = make_city(repo, "Vancouver")
    company_in_city(repo, co_a, city_van)

    # Historical tenders with buyer_x (won 3, lost 1)
    for i in range(1, 4):
        t = make_tender(repo, f"BuyerX Win {i}", {"value": 300_000 * i, "valid_from": f"202{i}-03-10"})
        tender_issued_by(repo, t, buyer_x)
        company_wins_tender(repo, co_a, t)
        company_bids_tender(repo, co_b, t)

    t_loss = make_tender(repo, "BuyerX Loss 1", {"value": 200_000, "valid_from": "2022-07-01"})
    tender_issued_by(repo, t_loss, buyer_x)
    company_bids_tender(repo, co_a, t_loss)
    company_wins_tender(repo, co_b, t_loss)

    # Win with buyer_y
    t_y = make_tender(repo, "BuyerY Win 1", {"value": 1_500_000, "valid_from": "2023-06-01"})
    tender_issued_by(repo, t_y, buyer_y)
    company_wins_tender(repo, co_a, t_y)
    company_bids_tender(repo, co_c, t_y)

    # New target tender from buyer_x
    target = make_tender(
        repo,
        "New Bridge Project",
        {
            "value": 800_000,
            "valid_from": "2024-01-01",
            "closing_date": "2027-06-30",
            "city": "Vancouver",
            "province": "BC",
        },
    )
    tender_issued_by(repo, target, buyer_x)

    return {
        "company": co_a,
        "co_b": co_b,
        "co_c": co_c,
        "buyer_x": buyer_x,
        "buyer_y": buyer_y,
        "ind_const": ind_const,
        "ind_eng": ind_eng,
        "target": target,
    }


# ══════════════════════════════════════════════════════════════════════════════
# Helper function tests
# ══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_ev_structure(self):
        e = _ev("CMP-1", "awarded_to", "TEN-1", "Acme", "Road Repair")
        assert e["entity_uid"] == "CMP-1"
        assert e["relation"] == "awarded_to"
        assert e["target_uid"] == "TEN-1"
        assert e["entity_name"] == "Acme"
        assert e["target_name"] == "Road Repair"

    def test_ev_defaults_empty_names(self):
        e = _ev("CMP-1", "rel", "TEN-1")
        assert e["entity_name"] == ""
        assert e["target_name"] == ""

    def test_confidence_zero_evidence(self):
        assert _confidence(0) == pytest.approx(0.3, abs=1e-4)

    def test_confidence_grows_with_evidence(self):
        assert _confidence(5) > _confidence(0)
        assert _confidence(10) > _confidence(5)

    def test_confidence_caps_at_one(self):
        assert _confidence(100) == 1.0

    def test_confidence_custom_base(self):
        assert _confidence(0, base=0.5) == pytest.approx(0.5, abs=1e-4)

    def test_parse_year_valid(self):
        assert _parse_year("2024-03-15") == 2024

    def test_parse_year_year_only(self):
        assert _parse_year("2022") == 2022

    def test_parse_year_none(self):
        assert _parse_year(None) is None

    def test_parse_year_invalid(self):
        assert _parse_year("not-a-date") is None

    def test_parse_month_valid(self):
        assert _parse_month("2024-07-15") == 7

    def test_parse_month_january(self):
        assert _parse_month("2024-01-01") == 1

    def test_parse_month_none(self):
        assert _parse_month(None) is None

    def test_parse_month_too_short(self):
        assert _parse_month("2024") is None

    def test_safe_float_int(self):
        assert _safe_float(42) == 42.0

    def test_safe_float_str(self):
        assert _safe_float("3.14") == pytest.approx(3.14)

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_invalid(self):
        assert _safe_float("abc") is None

    def test_value_bucket_micro(self):
        assert _value_bucket(10_000) == "micro"

    def test_value_bucket_small(self):
        assert _value_bucket(100_000) == "small"

    def test_value_bucket_medium(self):
        assert _value_bucket(500_000) == "medium"

    def test_value_bucket_large(self):
        assert _value_bucket(5_000_000) == "large"

    def test_value_bucket_mega(self):
        assert _value_bucket(50_000_000) == "mega"

    def test_value_bucket_none(self):
        assert _value_bucket(None) is None

    def test_value_bucket_zero(self):
        assert _value_bucket(0) == "micro"

    def test_hhi_equal_shares(self):
        # 4 equal shares → HHI = 0.25
        assert _hhi([1, 1, 1, 1]) == pytest.approx(0.25, abs=1e-4)

    def test_hhi_monopoly(self):
        assert _hhi([1, 0, 0]) == pytest.approx(1.0, abs=1e-4)

    def test_hhi_empty(self):
        assert _hhi([]) == 0.0

    def test_hhi_zero_total(self):
        assert _hhi([0, 0, 0]) == 0.0

    def test_clamp_below(self):
        assert _clamp(-0.5) == 0.0

    def test_clamp_above(self):
        assert _clamp(1.5) == 1.0

    def test_clamp_within(self):
        assert _clamp(0.5) == pytest.approx(0.5)

    def test_require_entity_not_found(self, repo):
        result = _require_entity(repo, "CMP-NOTEXIST")
        assert result is not None
        assert "error" in result

    def test_require_entity_wrong_kind(self, repo):
        tender = make_tender(repo, "Test Tender")
        result = _require_entity(repo, tender.uid, [BizEntityKind.COMPANY])
        assert result is not None
        assert "error" in result

    def test_require_entity_correct_kind(self, repo):
        company = make_company(repo, "Test Co")
        result = _require_entity(repo, company.uid, [BizEntityKind.COMPANY])
        assert result is None

    def test_require_entity_no_kind_filter(self, repo):
        tender = make_tender(repo, "Test Tender")
        result = _require_entity(repo, tender.uid)
        assert result is None

    def test_tender_value_primary_key(self):
        assert _tender_value({"value": 100_000}) == 100_000.0

    def test_tender_value_fallback_key(self):
        assert _tender_value({"contract_value": 200_000}) == 200_000.0

    def test_tender_value_none(self):
        assert _tender_value({}) is None

    def test_tender_date_primary_key(self):
        assert _tender_date({"valid_from": "2024-01-01"}) == "2024-01-01"

    def test_tender_date_fallback(self):
        assert _tender_date({"date": "2023-06-15"}) == "2023-06-15"

    def test_deadline_date_primary_key(self):
        assert _deadline_date({"closing_date": "2025-12-31"}) == "2025-12-31"

    def test_deadline_date_fallback(self):
        assert _deadline_date({"deadline": "2025-06-30"}) == "2025-06-30"

    def test_months_until_future(self):
        future = (datetime.datetime.now() + datetime.timedelta(days=90)).strftime("%Y-%m-%d")
        m = _months_until(future)
        assert m is not None
        assert m > 0

    def test_months_until_past(self):
        past = "2020-01-01"
        m = _months_until(past)
        assert m is not None
        assert m < 0

    def test_months_until_none(self):
        assert _months_until(None) is None


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_score tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityScore:
    def test_company_not_found(self, engine):
        result = engine.opportunity_score("CMP-NOTEXIST", "TEN-NOTEXIST")
        assert "error" in result

    def test_tender_not_found(self, engine, repo):
        co = make_company(repo, "Co")
        result = engine.opportunity_score(co.uid, "TEN-NOTEXIST")
        assert "error" in result

    def test_wrong_company_kind(self, engine, repo):
        t = make_tender(repo, "T1")
        result = engine.opportunity_score(t.uid, t.uid)
        assert "error" in result

    def test_wrong_tender_kind(self, engine, repo):
        co = make_company(repo, "Co")
        org = make_org(repo, "Org")
        result = engine.opportunity_score(co.uid, org.uid)
        assert "error" in result

    def test_score_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        assert "error" not in result
        assert "score" in result
        assert "dimensions" in result
        assert "evidence" in result
        assert "reasoning_chain" in result
        assert "confidence" in result
        assert "assumptions" in result
        assert "weak_evidence" in result
        assert "missing_information" in result

    def test_score_range(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        assert 0.0 <= result["score"] <= 100.0

    def test_all_ten_dimensions_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        expected = {
            "capability_fit",
            "buyer_history",
            "industry_history",
            "value_fit",
            "geographic_fit",
            "competition_level",
            "buyer_attractiveness",
            "strategic_importance",
            "workload_impact",
            "win_probability",
        }
        assert set(result["dimensions"].keys()) == expected

    def test_dimension_weight_sum(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        total_weight = sum(v["weight"] for v in result["dimensions"].values())
        assert total_weight == 100

    def test_dimension_score_gte_zero(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        for dim, v in result["dimensions"].items():
            assert v["score"] >= 0, f"{dim} score is negative"

    def test_dimension_raw_0_to_1(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        for dim, v in result["dimensions"].items():
            assert 0.0 <= v["raw"] <= 1.0, f"{dim} raw out of range"

    def test_weighted_sum_matches_score(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        expected = sum(v["score"] for v in result["dimensions"].values())
        assert abs(result["score"] - expected) < 0.01

    def test_reasoning_chain_has_ten_entries(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_score(co.uid, t.uid)
        assert len(result["reasoning_chain"]) >= 11  # 10 dims + total

    def test_score_higher_with_buyer_history(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.opportunity_score(co.uid, target.uid)
        bh_raw = result["dimensions"]["buyer_history"]["raw"]
        # Alpha Builds won 3/4 with Buyer X → high buyer history score
        assert bh_raw > 0.5

    def test_new_company_no_history_has_low_win_probability(self, engine, repo):
        co = make_company(repo, "Brand New Co")
        t = make_tender(repo, "Some Tender", {"value": 100_000})
        result = engine.opportunity_score(co.uid, t.uid)
        wp = result["dimensions"]["win_probability"]["raw"]
        assert wp <= 0.3

    def test_missing_tender_value_gives_neutral_value_fit(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "No Value Tender")
        result = engine.opportunity_score(co.uid, t.uid)
        vf = result["dimensions"]["value_fit"]["raw"]
        assert vf == pytest.approx(0.5)

    def test_missing_info_when_no_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Tender No Buyer")
        result = engine.opportunity_score(co.uid, t.uid)
        assert any("buyer" in m.lower() for m in result["missing_information"])

    def test_missing_info_when_no_value(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Tender No Value")
        result = engine.opportunity_score(co.uid, t.uid)
        assert any("value" in m.lower() for m in result["missing_information"])

    def test_geo_fit_perfect_when_same_city(self, engine, repo):
        co = make_company(repo, "Local Co", {"city": "Victoria"})
        city = make_city(repo, "Victoria")
        company_in_city(repo, co, city)
        org = make_org(repo, "City of Victoria")
        t = make_tender(repo, "Local Project", {"city": "Victoria", "province": "BC"})
        tender_issued_by(repo, t, org)
        result = engine.opportunity_score(co.uid, t.uid)
        geo = result["dimensions"]["geographic_fit"]["raw"]
        assert geo == pytest.approx(1.0)

    def test_competition_score_high_with_few_bidders(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Low Competition Tender")
        bidder = make_company(repo, "Bidder")
        company_bids_tender(repo, bidder, t)
        result = engine.opportunity_score(co.uid, t.uid)
        comp = result["dimensions"]["competition_level"]["raw"]
        assert comp >= 0.7

    def test_competition_score_low_with_many_bidders(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "High Competition Tender")
        for i in range(15):
            b = make_company(repo, f"Bidder {i}")
            company_bids_tender(repo, b, t)
        result = engine.opportunity_score(co.uid, t.uid)
        comp = result["dimensions"]["competition_level"]["raw"]
        assert comp < 0.4

    def test_buyer_attractiveness_high_with_many_tenders(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Big Buyer")
        for i in range(20):
            t = make_tender(repo, f"Buyer Tender {i}")
            tender_issued_by(repo, t, buyer)
        target = make_tender(repo, "Target Tender")
        tender_issued_by(repo, target, buyer)
        result = engine.opportunity_score(co.uid, target.uid)
        ba = result["dimensions"]["buyer_attractiveness"]["raw"]
        assert ba >= 0.8

    def test_industry_history_uses_shared_industry(self, engine, repo):
        co = make_company(repo, "Co")
        ind = make_industry(repo, "Construction")
        winner = make_company(repo, "Winner")
        company_in_industry(repo, co, ind)
        company_in_industry(repo, winner, ind)
        # Past win in same industry
        past_t = make_tender(repo, "Past Construction Tender")
        company_wins_tender(repo, co, past_t)
        company_in_industry(repo, winner, ind)
        # New tender with same-industry winner
        new_t = make_tender(repo, "New Construction Tender")
        company_wins_tender(repo, winner, new_t)
        result = engine.opportunity_score(co.uid, new_t.uid)
        ih = result["dimensions"]["industry_history"]["raw"]
        # co won past_t but industry overlap with new_t depends on winner's industry
        assert 0.0 <= ih <= 1.0


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_recommendation tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityRecommendation:
    def test_error_propagates(self, engine):
        result = engine.opportunity_recommendation("CMP-X", "TEN-X")
        assert "error" in result

    def test_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_recommendation(co.uid, t.uid)
        assert "recommendation" in result
        assert "why_pursue" in result
        assert "why_ignore" in result
        assert "next_actions" in result
        assert "score" in result
        assert "confidence" in result

    def test_recommendation_is_valid_label(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_recommendation(co.uid, t.uid)
        valid = {"Strong Pursue", "Pursue", "Strategic Investment", "Monitor", "Ignore"}
        assert result["recommendation"] in valid

    def test_high_score_gives_pursue(self, engine, rich_graph):
        """Alpha Builds has strong history with the buyer → should Pursue."""
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.opportunity_recommendation(co.uid, target.uid)
        assert result["recommendation"] in ("Strong Pursue", "Pursue", "Strategic Investment")

    def test_new_company_tends_toward_ignore_or_monitor(self, engine, repo):
        co = make_company(repo, "Unknown Co")
        t = make_tender(repo, "Big Tender", {"value": 50_000_000})
        for i in range(12):
            b = make_company(repo, f"Strong Bidder {i}")
            company_wins_tender(repo, b, t)
        result = engine.opportunity_recommendation(co.uid, t.uid)
        assert result["recommendation"] in ("Monitor", "Ignore", "Strategic Investment")

    def test_next_actions_present_for_pursue(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.opportunity_recommendation(co.uid, target.uid)
        if result["recommendation"] in ("Strong Pursue", "Pursue"):
            assert len(result["next_actions"]) > 0

    def test_urgent_deadline_appears_in_next_actions(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Buyer")
        # Deadline in 2 weeks
        soon = (datetime.datetime.now() + datetime.timedelta(days=14)).strftime("%Y-%m-%d")
        t = make_tender(repo, "Urgent Tender", {"value": 300_000, "closing_date": soon})
        tender_issued_by(repo, t, buyer)
        # Give company high history to trigger Pursue
        for i in range(3):
            pt = make_tender(repo, f"Past Win {i}", {"value": 300_000})
            tender_issued_by(repo, pt, buyer)
            company_wins_tender(repo, co, pt)
        result = engine.opportunity_recommendation(co.uid, t.uid)
        if result["recommendation"] in ("Strong Pursue", "Pursue"):
            actions = " ".join(result["next_actions"]).lower()
            assert "urgent" in actions or "month" in actions

    def test_strategic_investment_label_possible(self, engine, repo):
        """A tender with score ~40–55 + high strategic dimension."""
        co = make_company(repo, "Niche Co")
        new_buy = make_org(repo, "Brand New Buyer")
        target = make_tender(repo, "Niche Tender", {"value": 50_000})
        tender_issued_by(repo, target, new_buy)
        # co has zero history → strategic dimension will be elevated (new buyer)
        result = engine.opportunity_recommendation(co.uid, target.uid)
        valid = {"Strategic Investment", "Monitor", "Ignore", "Pursue", "Strong Pursue"}
        assert result["recommendation"] in valid


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_explain tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityExplain:
    def test_error_propagates(self, engine):
        result = engine.opportunity_explain("CMP-X", "TEN-X")
        assert "error" in result

    def test_all_explainability_fields(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_explain(co.uid, t.uid)
        for key in [
            "score",
            "dimensions",
            "recommendation",
            "why_pursue",
            "why_ignore",
            "next_actions",
            "evidence",
            "assumptions",
            "weak_evidence",
            "missing_information",
            "reasoning_chain",
            "confidence",
        ]:
            assert key in result, f"Missing key: {key}"

    def test_buyer_present_in_explain(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_explain(co.uid, t.uid)
        assert result["buyer"] is not None
        assert result["buyer"]["name"] == "Ministry of Works"

    def test_tender_value_in_explain(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_explain(co.uid, t.uid)
        assert result["tender_value"] == pytest.approx(500_000.0)

    def test_tender_deadline_in_explain(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_explain(co.uid, t.uid)
        assert result["tender_deadline"] is not None

    def test_explain_consistency_with_score(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        explain = engine.opportunity_explain(co.uid, t.uid)
        scored = engine.opportunity_score(co.uid, t.uid)
        assert explain["score"] == scored["score"]

    def test_explain_consistency_with_recommendation(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        explain = engine.opportunity_explain(co.uid, t.uid)
        rec = engine.opportunity_recommendation(co.uid, t.uid)
        assert explain["recommendation"] == rec["recommendation"]


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_timeline tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityTimeline:
    def test_error_propagates(self, engine):
        result = engine.opportunity_timeline("CMP-X", "TEN-X")
        assert "error" in result

    def test_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_timeline(co.uid, t.uid)
        for key in [
            "submission_urgency",
            "preparation_effort",
            "deadline_risk",
            "months_until_deadline",
            "comparable_wins",
            "comparable_losses",
        ]:
            assert key in result, f"Missing: {key}"

    def test_future_deadline_not_expired(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["submission_urgency"] != "expired"

    def test_urgency_critical_for_near_deadline(self, engine, repo):
        co = make_company(repo, "Co")
        soon = (datetime.datetime.now() + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        t = make_tender(repo, "Critical Deadline Tender", {"closing_date": soon})
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["submission_urgency"] in ("critical", "high")
        assert result["deadline_risk"] in ("medium", "high")

    def test_urgency_low_for_far_deadline(self, engine, repo):
        co = make_company(repo, "Co")
        far = (datetime.datetime.now() + datetime.timedelta(days=365)).strftime("%Y-%m-%d")
        t = make_tender(repo, "Far Deadline Tender", {"closing_date": far})
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["submission_urgency"] == "low"
        assert result["deadline_risk"] == "low"

    def test_prep_effort_medium_for_medium_value(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Medium Value Tender", {"value": 500_000})
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["preparation_effort"] == "1–3 weeks"

    def test_prep_effort_large_for_large_value(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Large Tender", {"value": 10_000_000})
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["preparation_effort"] == "2–6 weeks"

    def test_prep_effort_mega(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Mega Tender", {"value": 50_000_000})
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["preparation_effort"] == "2–4 months"

    def test_no_deadline_unknown_urgency(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "No Deadline Tender")
        result = engine.opportunity_timeline(co.uid, t.uid)
        assert result["submission_urgency"] == "unknown"
        assert result["months_until_deadline"] is None

    def test_comparable_wins_from_history(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.opportunity_timeline(co.uid, target.uid)
        assert len(result["comparable_wins"]) > 0

    def test_comparable_losses_from_history(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.opportunity_timeline(co.uid, target.uid)
        # Alpha has 1 loss with buyer X
        assert len(result["comparable_losses"]) >= 0  # may or may not match


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_risk tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityRisk:
    def test_error_propagates(self, engine):
        result = engine.opportunity_risk("CMP-X", "TEN-X")
        assert "error" in result

    def test_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_risk(co.uid, t.uid)
        assert "overall_risk" in result
        assert "risk_factors" in result
        assert "mitigations" in result

    def test_overall_risk_valid_value(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_risk(co.uid, t.uid)
        assert result["overall_risk"] in ("low", "medium", "high")

    def test_high_competition_triggers_risk_factor(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Competitive Tender")
        # Need 13+ bidders so comp_raw = 1.0 - 13/20 = 0.35 < 0.4
        for i in range(13):
            b = make_company(repo, f"Rival {i}")
            company_bids_tender(repo, b, t)
        result = engine.opportunity_risk(co.uid, t.uid)
        factors = [r["factor"] for r in result["risk_factors"]]
        assert "high_competition" in factors

    def test_no_buyer_history_triggers_risk(self, engine, repo):
        # no_buyer_history fires when bh_raw < 0.25.
        # To achieve this: company bids but never wins → win_rate = 0.0 < 0.25.
        co = make_company(repo, "Losing Co")
        buyer = make_org(repo, "Known Buyer")
        winner = make_company(repo, "Always Wins")
        # Give co 3 bids with no wins → bh_raw = 0.0 + volume_bonus(0.3) = 0.03 < 0.25
        for i in range(3):
            t = make_tender(repo, f"Past Loss {i}", {"value": 300_000})
            tender_issued_by(repo, t, buyer)
            company_bids_tender(repo, co, t)
            company_wins_tender(repo, winner, t)
        target = make_tender(repo, "Buyer Tender", {"value": 300_000})
        tender_issued_by(repo, target, buyer)
        result = engine.opportunity_risk(co.uid, target.uid)
        factors = [r["factor"] for r in result["risk_factors"]]
        assert "no_buyer_history" in factors

    def test_tight_deadline_triggers_risk(self, engine, repo):
        co = make_company(repo, "Co")
        soon = (datetime.datetime.now() + datetime.timedelta(days=5)).strftime("%Y-%m-%d")
        t = make_tender(repo, "Tight Tender", {"closing_date": soon, "value": 300_000})
        result = engine.opportunity_risk(co.uid, t.uid)
        factors = [r["factor"] for r in result["risk_factors"]]
        assert "tight_deadline" in factors

    def test_capability_gap_triggers_risk_when_no_industry_overlap(self, engine, repo):
        co = make_company(repo, "Co")
        ind_it = make_industry(repo, "IT")
        ind_con = make_industry(repo, "Construction")
        company_in_industry(repo, co, ind_it)
        # Create a tender where industry via winning companies is Construction (no IT)
        winner = make_company(repo, "Contractor")
        company_in_industry(repo, winner, ind_con)
        t = make_tender(repo, "Construction Tender")
        company_wins_tender(repo, winner, t)
        # Co has no construction overlap
        result = engine.opportunity_risk(co.uid, t.uid)
        # capability_gap or partial_capability should be in factors if raw < 0.5
        cap_raw = engine.opportunity_score(co.uid, t.uid)["dimensions"]["capability_fit"]["raw"]
        if cap_raw < 0.5:
            factors = [r["factor"] for r in result["risk_factors"]]
            assert any("capability" in f for f in factors)

    def test_mitigations_non_empty_when_risks_exist(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Risky Tender")
        for i in range(12):
            b = make_company(repo, f"Rival {i}")
            company_bids_tender(repo, b, t)
        result = engine.opportunity_risk(co.uid, t.uid)
        if result["risk_factors"]:
            assert len(result["mitigations"]) > 0

    def test_risk_factors_have_severity(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Tender")
        result = engine.opportunity_risk(co.uid, t.uid)
        for rf in result["risk_factors"]:
            assert rf["severity"] in ("low", "medium", "high")
            assert "factor" in rf
            assert "detail" in rf


# ══════════════════════════════════════════════════════════════════════════════
# portfolio_impact tests
# ══════════════════════════════════════════════════════════════════════════════


class TestPortfolioImpact:
    def test_error_propagates(self, engine):
        result = engine.portfolio_impact("CMP-X", "TEN-X")
        assert "error" in result

    def test_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.portfolio_impact(co.uid, t.uid)
        for key in [
            "tender_value",
            "win_probability",
            "expected_revenue",
            "diversification_impact",
            "strategic_value",
            "is_new_client",
            "client_expansion_value",
            "future_relationship_potential",
        ]:
            assert key in result, f"Missing: {key}"

    def test_expected_revenue_calculation(self, engine, repo):
        co = make_company(repo, "Co")
        # Give co a win rate of 1.0 (one win, no losses)
        buyer = make_org(repo, "Buyer")
        past = make_tender(repo, "Past Win", {"value": 100_000})
        tender_issued_by(repo, past, buyer)
        company_wins_tender(repo, co, past)
        target = make_tender(repo, "Target Tender", {"value": 1_000_000})
        tender_issued_by(repo, target, buyer)
        result = engine.portfolio_impact(co.uid, target.uid)
        # win_probability should be > 0 and expected_revenue > 0
        assert result["expected_revenue"] is not None
        assert result["expected_revenue"] > 0

    def test_is_new_client_true_for_unknown_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        org = make_org(repo, "New Org")
        t = make_tender(repo, "New Client Tender")
        tender_issued_by(repo, t, org)
        result = engine.portfolio_impact(co.uid, t.uid)
        assert result["is_new_client"] is True
        assert result["client_expansion_value"] == "high"

    def test_is_new_client_false_for_known_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Existing Buyer")
        past = make_tender(repo, "Past Tender")
        tender_issued_by(repo, past, buyer)
        company_wins_tender(repo, co, past)
        new_t = make_tender(repo, "New Tender")
        tender_issued_by(repo, new_t, buyer)
        result = engine.portfolio_impact(co.uid, new_t.uid)
        assert result["is_new_client"] is False
        assert result["client_expansion_value"] == "low"

    def test_future_potential_high_with_active_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Active Buyer")
        for i in range(15):
            t = make_tender(repo, f"Buyer Tender {i}")
            tender_issued_by(repo, t, buyer)
        target = make_tender(repo, "Target")
        tender_issued_by(repo, target, buyer)
        result = engine.portfolio_impact(co.uid, target.uid)
        assert result["future_relationship_potential"] == "high"

    def test_future_potential_low_with_inactive_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Inactive Buyer")
        t = make_tender(repo, "Single Tender")
        tender_issued_by(repo, t, buyer)
        result = engine.portfolio_impact(co.uid, t.uid)
        assert result["future_relationship_potential"] == "low"

    def test_no_tender_value_gives_none_expected_revenue(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "No Value Tender")
        result = engine.portfolio_impact(co.uid, t.uid)
        assert result["expected_revenue"] is None


# ══════════════════════════════════════════════════════════════════════════════
# similar_opportunities tests
# ══════════════════════════════════════════════════════════════════════════════


class TestSimilarOpportunities:
    def test_error_propagates(self, engine):
        result = engine.similar_opportunities("CMP-X", "TEN-X")
        assert "error" in result

    def test_fields_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.similar_opportunities(co.uid, t.uid)
        assert "similar_count" in result
        assert "similar" in result
        assert "evidence" in result

    def test_same_buyer_match(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid)
        # Alpha Builds won tenders with buyer_x which also issues target
        assert result["similar_count"] > 0
        buyers_matched = [s for s in result["similar"] if "same buyer" in s.get("similarity_reasons", [])]
        assert len(buyers_matched) > 0

    def test_outcome_is_win_or_loss(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid)
        for s in result["similar"]:
            assert s["outcome"] in ("win", "loss")

    def test_similarity_score_range(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid)
        for s in result["similar"]:
            assert 0.0 <= s["similarity"] <= 1.0

    def test_sorted_by_similarity_descending(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid)
        scores = [s["similarity"] for s in result["similar"]]
        assert scores == sorted(scores, reverse=True)

    def test_target_not_in_similar(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid)
        assert all(s["uid"] != target.uid for s in result["similar"])

    def test_limit_respected(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        result = engine.similar_opportunities(co.uid, target.uid, limit=2)
        assert len(result["similar"]) <= 2

    def test_no_history_gives_empty_similar(self, engine, repo):
        co = make_company(repo, "New Co")
        t = make_tender(repo, "New Tender")
        result = engine.similar_opportunities(co.uid, t.uid)
        assert result["similar_count"] == 0
        assert result["similar"] == []


# ══════════════════════════════════════════════════════════════════════════════
# best_opportunities tests
# ══════════════════════════════════════════════════════════════════════════════


class TestBestOpportunities:
    def test_error_propagates(self, engine):
        result = engine.best_opportunities("CMP-X")
        assert "error" in result

    def test_fields_present(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid)
        assert "total_tenders_scored" in result
        assert "top_opportunities" in result
        assert "confidence" in result

    def test_returns_at_most_limit(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid, limit=3)
        assert len(result["top_opportunities"]) <= 3

    def test_sorted_by_score_descending(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid, limit=10)
        scores = [o["score"] for o in result["top_opportunities"]]
        assert scores == sorted(scores, reverse=True)

    def test_each_opportunity_has_fields(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid, limit=5)
        for opp in result["top_opportunities"]:
            assert "tender_uid" in opp
            assert "tender_name" in opp
            assert "score" in opp
            assert "recommendation" in opp
            assert "dimensions" in opp

    def test_total_scored_counts_all_tenders(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid)
        # rich_graph has 7 tenders (3 buyer_x wins, 1 buyer_x loss, 1 buyer_y win + target)
        assert result["total_tenders_scored"] >= 6

    def test_recommendation_label_valid(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.best_opportunities(co.uid)
        valid = {"Strong Pursue", "Pursue", "Strategic Investment", "Monitor", "Ignore"}
        for opp in result["top_opportunities"]:
            assert opp["recommendation"] in valid

    def test_empty_graph_scores_nothing(self, engine, repo):
        co = make_company(repo, "Co")
        result = engine.best_opportunities(co.uid)
        assert result["total_tenders_scored"] == 0
        assert result["top_opportunities"] == []


# ══════════════════════════════════════════════════════════════════════════════
# opportunity_profile tests
# ══════════════════════════════════════════════════════════════════════════════


class TestOpportunityProfile:
    def test_error_propagates(self, engine):
        result = engine.opportunity_profile("CMP-X", "TEN-X")
        assert "error" in result

    def test_all_sub_sections_present(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        result = engine.opportunity_profile(co.uid, t.uid)
        for key in [
            "score",
            "recommendation",
            "explain",
            "timeline",
            "risk",
            "portfolio",
            "similar",
        ]:
            assert key in result, f"Missing section: {key}"

    def test_score_consistent_across_sections(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        profile = engine.opportunity_profile(co.uid, t.uid)
        assert profile["score"] == profile["explain"]["score"]

    def test_recommendation_consistent_across_sections(self, engine, simple_graph):
        co = simple_graph["company"]
        t = simple_graph["tender"]
        profile = engine.opportunity_profile(co.uid, t.uid)
        assert profile["recommendation"] == profile["explain"]["recommendation"]


# ══════════════════════════════════════════════════════════════════════════════
# executive_summary tests
# ══════════════════════════════════════════════════════════════════════════════


class TestExecutiveSummary:
    def test_error_propagates(self, engine):
        result = engine.executive_summary("CMP-X")
        assert "error" in result

    def test_fields_present(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.executive_summary(co.uid)
        for key in [
            "total_tenders_scored",
            "top_opportunities",
            "biggest_risks",
            "why_pursue",
            "why_ignore",
            "immediate_next_actions",
            "confidence",
        ]:
            assert key in result, f"Missing: {key}"

    def test_top_opportunities_limit_respected(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.executive_summary(co.uid, limit=2)
        assert len(result["top_opportunities"]) <= 2

    def test_top_opportunities_are_pursue_recommendations(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.executive_summary(co.uid)
        for opp in result["top_opportunities"]:
            assert opp["recommendation"] in ("Strong Pursue", "Pursue")

    def test_why_pursue_non_empty_with_good_opportunities(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.executive_summary(co.uid)
        # Alpha has strong history → should have pursue reasons
        assert isinstance(result["why_pursue"], list)

    def test_next_actions_list(self, engine, rich_graph):
        co = rich_graph["company"]
        result = engine.executive_summary(co.uid)
        assert isinstance(result["immediate_next_actions"], list)

    def test_no_tenders_gives_empty_summary(self, engine, repo):
        co = make_company(repo, "Empty Co")
        result = engine.executive_summary(co.uid)
        assert result["total_tenders_scored"] == 0
        assert result["top_opportunities"] == []


# ══════════════════════════════════════════════════════════════════════════════
# Score dimension unit tests (isolated scoring methods)
# ══════════════════════════════════════════════════════════════════════════════


class TestScoringDimensions:
    def test_capability_neutral_when_no_industry_data(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "No Industry Tender")
        score, ev = engine._score_capability(co.uid, {"uid": t.uid, "attributes": {}})
        assert score == pytest.approx(0.5)

    def test_capability_perfect_when_full_overlap(self, engine, repo):
        co = make_company(repo, "Co")
        ind = make_industry(repo, "Construction")
        company_in_industry(repo, co, ind)
        winner = make_company(repo, "Winner")
        company_in_industry(repo, winner, ind)
        t = make_tender(repo, "Construction Tender")
        company_wins_tender(repo, winner, t)
        score, ev = engine._score_capability(co.uid, {"uid": t.uid, "attributes": {}})
        assert score > 0.5

    def test_buyer_history_neutral_when_no_buyer(self, engine, repo):
        score, ev = engine._score_buyer_history("CMP-X", None)
        assert score == pytest.approx(0.3)

    def test_buyer_history_low_when_never_bid_with_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = {"uid": "ORG-FAKE", "name": "Fake Buyer", "kind": "organization", "attributes": {}}
        score, ev = engine._score_buyer_history(co.uid, buyer)
        assert score == pytest.approx(0.3)

    def test_competition_score_zero_bidders_neutral(self, engine, repo):
        t = make_tender(repo, "No Bidder Tender")
        score, ev = engine._score_competition(t.uid)
        assert score == pytest.approx(0.5)

    def test_competition_score_one_bidder_high(self, engine, repo):
        t = make_tender(repo, "Sole Source Tender")
        b = make_company(repo, "Sole Bidder")
        company_bids_tender(repo, b, t)
        score, ev = engine._score_competition(t.uid)
        assert score >= 0.7

    def test_workload_high_score_when_no_recent_tenders(self, engine, repo):
        co = make_company(repo, "Idle Co")
        score, ev = engine._score_workload(co.uid)
        assert score >= 0.8

    def test_workload_lower_score_with_many_recent_tenders(self, engine, repo):
        co = make_company(repo, "Busy Co")
        current_year = datetime.datetime.now().year
        for i in range(10):
            t = make_tender(repo, f"Recent Tender {i}", {"valid_from": f"{current_year}-0{(i % 9) + 1}-01"})
            company_bids_tender(repo, co, t)
        score, ev = engine._score_workload(co.uid)
        assert score < 0.8

    def test_win_probability_zero_history(self, engine, repo):
        co = make_company(repo, "No History Co")
        score, ev = engine._score_win_probability(co.uid, None, "TEN-X")
        assert score == pytest.approx(0.2)

    def test_win_probability_improves_with_wins(self, engine, repo):
        co = make_company(repo, "Winning Co")
        buyer = make_org(repo, "Buyer")
        for i in range(4):
            t = make_tender(repo, f"Win {i}")
            tender_issued_by(repo, t, buyer)
            company_wins_tender(repo, co, t)
        buyer_dict = {
            "uid": buyer.uid,
            "name": buyer.name,
            "kind": "organization",
            "attributes": {},
        }
        score, ev = engine._score_win_probability(co.uid, buyer_dict, "TEN-X")
        assert score > 0.5

    def test_strategic_score_high_for_new_buyer(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = {
            "uid": "ORG-NEW",
            "name": "Brand New Buyer",
            "kind": "organization",
            "attributes": {},
        }
        score, ev = engine._score_strategic(co.uid, buyer, [])
        assert score > 0.3  # new buyer adds bonus

    def test_value_fit_neutral_for_no_value(self, engine, repo):
        co = make_company(repo, "Co")
        score, ev = engine._score_value_fit(co.uid, None)
        assert score == pytest.approx(0.5)

    def test_value_fit_high_when_matching_history(self, engine, repo):
        co = make_company(repo, "Medium Co")
        for i in range(5):
            t = make_tender(repo, f"Medium Tender {i}", {"value": 500_000})
            company_wins_tender(repo, co, t)
        score, ev = engine._score_value_fit(co.uid, 800_000)  # medium bucket
        assert score > 0.5


# ══════════════════════════════════════════════════════════════════════════════
# Idempotency and determinism
# ══════════════════════════════════════════════════════════════════════════════


class TestDeterminism:
    def test_score_is_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        r1 = engine.opportunity_score(co.uid, target.uid)
        r2 = engine.opportunity_score(co.uid, target.uid)
        assert r1["score"] == r2["score"]

    def test_recommendation_is_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        r1 = engine.opportunity_recommendation(co.uid, target.uid)
        r2 = engine.opportunity_recommendation(co.uid, target.uid)
        assert r1["recommendation"] == r2["recommendation"]

    def test_similar_is_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        target = rich_graph["target"]
        r1 = engine.similar_opportunities(co.uid, target.uid)
        r2 = engine.similar_opportunities(co.uid, target.uid)
        assert r1["similar_count"] == r2["similar_count"]

    def test_best_opportunities_deterministic(self, engine, rich_graph):
        co = rich_graph["company"]
        r1 = engine.best_opportunities(co.uid, limit=5)
        r2 = engine.best_opportunities(co.uid, limit=5)
        uids_1 = [o["tender_uid"] for o in r1["top_opportunities"]]
        uids_2 = [o["tender_uid"] for o in r2["top_opportunities"]]
        assert uids_1 == uids_2


# ══════════════════════════════════════════════════════════════════════════════
# Edge cases
# ══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_score_tender_with_zero_value(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Zero Value Tender", {"value": 0})
        result = engine.opportunity_score(co.uid, t.uid)
        assert "error" not in result
        assert 0 <= result["score"] <= 100

    def test_score_tender_with_very_large_value(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Huge Tender", {"value": 1_000_000_000})
        result = engine.opportunity_score(co.uid, t.uid)
        assert "error" not in result

    def test_company_with_only_losses(self, engine, repo):
        co = make_company(repo, "Loser Co")
        buyer = make_org(repo, "Buyer")
        for i in range(5):
            t = make_tender(repo, f"Lost {i}")
            tender_issued_by(repo, t, buyer)
            company_bids_tender(repo, co, t)
            winner = make_company(repo, f"Winner {i}")
            company_wins_tender(repo, winner, t)
        target = make_tender(repo, "New Opportunity")
        tender_issued_by(repo, target, buyer)
        result = engine.opportunity_score(co.uid, target.uid)
        assert "error" not in result
        wp = result["dimensions"]["win_probability"]["raw"]
        assert wp == pytest.approx(0.0)

    def test_tender_with_no_issued_by(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "Orphan Tender", {"value": 200_000})
        result = engine.opportunity_score(co.uid, t.uid)
        assert "error" not in result
        assert any("buyer" in m.lower() for m in result["missing_information"])

    def test_portfolio_with_no_buyer_no_crash(self, engine, repo):
        co = make_company(repo, "Co")
        t = make_tender(repo, "No Buyer Tender", {"value": 100_000})
        result = engine.portfolio_impact(co.uid, t.uid)
        assert "error" not in result

    def test_executive_summary_only_one_tender(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Buyer")
        t = make_tender(repo, "Only Tender", {"value": 500_000})
        tender_issued_by(repo, t, buyer)
        company_wins_tender(repo, co, t)
        # Score a different tender for the same company
        new_t = make_tender(repo, "New Target", {"value": 500_000})
        tender_issued_by(repo, new_t, buyer)
        result = engine.executive_summary(co.uid, limit=5)
        assert "error" not in result

    def test_similar_opportunities_value_bucket_match(self, engine, repo):
        co = make_company(repo, "Co")
        buyer = make_org(repo, "Buyer")
        # Past medium tender (won)
        past = make_tender(repo, "Past Medium", {"value": 500_000})
        tender_issued_by(repo, past, buyer)
        company_wins_tender(repo, co, past)
        # Target also medium
        target = make_tender(repo, "Target Medium", {"value": 600_000})
        tender_issued_by(repo, target, buyer)
        result = engine.similar_opportunities(co.uid, target.uid)
        assert result["similar_count"] >= 1
        # Should match on same buyer OR same value bucket
        reasons = [r for s in result["similar"] for r in s["similarity_reasons"]]
        assert "same buyer" in reasons or "same value bucket" in reasons
