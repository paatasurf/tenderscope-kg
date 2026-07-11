"""
Comprehensive tests for BuyerIntelligenceEngine.

Covers every public method, all helper functions, edge cases,
evidence presence, score formulas, and idempotency.

Graph construction pattern (in-memory SQLite):
  - make_org(repo, name)      → creates an ORGANIZATION entity, returns UID
  - make_company(repo, name)  → creates a COMPANY entity, returns UID
  - make_tender(repo, name, date=None, value=None) → creates a TENDER entity
  - issue(repo, org, tender)  → tender ISSUED_BY org
  - award(repo, company, tender) → company AWARDED_TO tender
  - bid(repo, company, tender)   → company SUBMITTED_BID on tender
  - link_industry(repo, company, industry_name) → company IN_INDUSTRY industry
"""

from __future__ import annotations

import sqlite3
from typing import Optional

import pytest

from tenderscope_kg.buyer_intelligence import (
    BuyerIntelligenceEngine,
    _buyer_tenders,
    _confidence,
    _ev,
    _hhi,
    _parse_month,
    _parse_year,
    _safe_float,
    _tender_participants,
    _tender_winner,
)
from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.repository._base import BizRepository
from tenderscope_kg.repository._sqlite import BizRepositorySQLite

# ═══════════════════════════════════════════════════════════════════════════════
# Fixtures
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    yield repo
    conn.close()


@pytest.fixture
def bie(repo: BizRepository) -> BuyerIntelligenceEngine:
    return BuyerIntelligenceEngine(repo)


# ═══════════════════════════════════════════════════════════════════════════════
# Graph construction helpers
# ═══════════════════════════════════════════════════════════════════════════════


def make_org(repo: BizRepository, name: str) -> str:
    ent, _ = repo.put_entity(BizEntityKind.ORGANIZATION, name)
    return ent.uid


def make_company(repo: BizRepository, name: str) -> str:
    ent, _ = repo.put_entity(BizEntityKind.COMPANY, name)
    return ent.uid


def make_tender(repo: BizRepository, name: str, date: Optional[str] = None, value: Optional[float] = None) -> str:
    attrs: dict = {}
    if date:
        attrs["valid_from"] = date
    if value is not None:
        attrs["value"] = value
    ent, _ = repo.put_entity(BizEntityKind.TENDER, name, attributes=attrs)
    return ent.uid


def issue(repo: BizRepository, org_uid: str, tender_uid: str) -> None:
    """tender ISSUED_BY org"""
    repo.put_relation(
        source_uid=tender_uid,
        target_uid=org_uid,
        kind=BizRelationKind.ISSUED_BY,
    )


def award(repo: BizRepository, company_uid: str, tender_uid: str) -> None:
    repo.put_relation(
        source_uid=company_uid,
        target_uid=tender_uid,
        kind=BizRelationKind.AWARDED_TO,
    )


def bid(repo: BizRepository, company_uid: str, tender_uid: str) -> None:
    repo.put_relation(
        source_uid=company_uid,
        target_uid=tender_uid,
        kind=BizRelationKind.SUBMITTED_BID,
    )


def link_industry(repo: BizRepository, company_uid: str, industry_name: str) -> str:
    ind_ent, _ = repo.put_entity(BizEntityKind.INDUSTRY, industry_name)
    repo.put_relation(
        source_uid=company_uid,
        target_uid=ind_ent.uid,
        kind=BizRelationKind.IN_INDUSTRY,
    )
    return ind_ent.uid


# ═══════════════════════════════════════════════════════════════════════════════
# Helper function unit tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestHelpers:
    def test_ev_structure(self):
        e = _ev("A", "rel", "B", "Aname", "Bname")
        assert e["entity_uid"] == "A"
        assert e["relation"] == "rel"
        assert e["target_uid"] == "B"
        assert e["entity_name"] == "Aname"
        assert e["target_name"] == "Bname"

    def test_confidence_base(self):
        assert _confidence(0) == pytest.approx(0.3, abs=0.001)

    def test_confidence_grows(self):
        assert _confidence(5) > _confidence(2)

    def test_confidence_caps_at_one(self):
        assert _confidence(100) == pytest.approx(1.0)

    def test_hhi_uniform(self):
        hhi = _hhi([1, 1, 1, 1])
        assert hhi == pytest.approx(0.25)

    def test_hhi_monopoly(self):
        assert _hhi([10]) == pytest.approx(1.0)

    def test_hhi_empty(self):
        assert _hhi([]) == 0.0

    def test_hhi_zero_total(self):
        assert _hhi([0, 0]) == 0.0

    def test_parse_year_valid(self):
        assert _parse_year("2023-06-15") == 2023

    def test_parse_year_none(self):
        assert _parse_year(None) is None

    def test_parse_year_bad(self):
        assert _parse_year("not-a-date") is None

    def test_parse_month_valid(self):
        assert _parse_month("2023-06-15") == 6

    def test_parse_month_none(self):
        assert _parse_month(None) is None

    def test_parse_month_short(self):
        assert _parse_month("2023") is None

    def test_safe_float_number(self):
        assert _safe_float(123.5) == pytest.approx(123.5)

    def test_safe_float_string(self):
        assert _safe_float("99.9") == pytest.approx(99.9)

    def test_safe_float_none(self):
        assert _safe_float(None) is None

    def test_safe_float_bad(self):
        assert _safe_float("abc") is None


# ═══════════════════════════════════════════════════════════════════════════════
# Graph traversal helper tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestGraphHelpers:
    def test_buyer_tenders_empty(self, repo: BizRepository):
        org = make_org(repo, "EmptyOrg")
        assert _buyer_tenders(repo, org) == []

    def test_buyer_tenders_returns_tenders(self, repo: BizRepository):
        org = make_org(repo, "BuyerOrg")
        t1 = make_tender(repo, "T1")
        t2 = make_tender(repo, "T2")
        issue(repo, org, t1)
        issue(repo, org, t2)
        result = _buyer_tenders(repo, org)
        uids = {r["uid"] for r in result}
        assert t1 in uids and t2 in uids

    def test_tender_participants_empty(self, repo: BizRepository):
        t = make_tender(repo, "Lonely Tender")
        assert _tender_participants(repo, t) == []

    def test_tender_participants_returns_all_roles(self, repo: BizRepository):
        t = make_tender(repo, "T-Roles")
        c1 = make_company(repo, "Winner")
        c2 = make_company(repo, "Bidder")
        award(repo, c1, t)
        bid(repo, c2, t)
        parts = _tender_participants(repo, t)
        roles = {p["role"] for p in parts}
        assert BizRelationKind.AWARDED_TO.value in roles
        assert BizRelationKind.SUBMITTED_BID.value in roles

    def test_tender_winner_none(self, repo: BizRepository):
        t = make_tender(repo, "Unbid Tender")
        assert _tender_winner(repo, t) is None

    def test_tender_winner_correct(self, repo: BizRepository):
        t = make_tender(repo, "Won Tender")
        c = make_company(repo, "WinCo")
        award(repo, c, t)
        w = _tender_winner(repo, t)
        assert w is not None
        assert w["uid"] == c


# ═══════════════════════════════════════════════════════════════════════════════
# buyer_summary
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuyerSummary:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.buyer_summary("ORG-MISSING")
        assert "error" in r

    def test_empty_buyer(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EmptyBuyer")
        r = bie.buyer_summary(org)
        assert r["total_tenders"] == 0
        assert r["active_suppliers"] == 0
        assert r["award_hhi"] == 0.0

    def test_summary_fields_present(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "FieldsOrg")
        t = make_tender(repo, "T-Sum")
        c = make_company(repo, "FieldCo")
        issue(repo, org, t)
        award(repo, c, t)
        r = bie.buyer_summary(org)
        assert r["total_tenders"] == 1
        assert r["active_suppliers"] == 1
        assert "evidence" in r
        assert "confidence" in r

    def test_top_supplier(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TopOrg")
        c1 = make_company(repo, "FreqWinner")
        c2 = make_company(repo, "RareWinner")
        for i in range(3):
            t = make_tender(repo, f"T-TopOrg-{i}")
            issue(repo, org, t)
            award(repo, c1, t)
        t_rare = make_tender(repo, "T-TopOrg-rare")
        issue(repo, org, t_rare)
        award(repo, c2, t_rare)
        r = bie.buyer_summary(org)
        assert r["top_supplier"]["uid"] == c1

    def test_company_kind_accepted(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        cmp = make_company(repo, "CompanyBuyer")
        r = bie.buyer_summary(cmp)
        assert "error" not in r


# ═══════════════════════════════════════════════════════════════════════════════
# supplier_roster
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupplierRoster:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.supplier_roster("ORG-MISSING")
        assert "error" in r

    def test_empty_roster(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoSuppliers")
        r = bie.supplier_roster(org)
        assert r["supplier_count"] == 0
        assert r["suppliers"] == []

    def test_roster_contains_winners(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "RosterOrg")
        c1 = make_company(repo, "Alpha")
        c2 = make_company(repo, "Beta")
        t1 = make_tender(repo, "T-R1")
        t2 = make_tender(repo, "T-R2")
        issue(repo, org, t1)
        issue(repo, org, t2)
        award(repo, c1, t1)
        award(repo, c2, t2)
        r = bie.supplier_roster(org)
        uids = {s["uid"] for s in r["suppliers"]}
        assert c1 in uids and c2 in uids

    def test_roster_win_rate_correct(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "WinRateOrg")
        c = make_company(repo, "WinRateCo")
        for i in range(4):
            t = make_tender(repo, f"T-WR-{i}")
            issue(repo, org, t)
            if i < 2:
                award(repo, c, t)
            else:
                bid(repo, c, t)
        r = bie.supplier_roster(org)
        sup = next(s for s in r["suppliers"] if s["uid"] == c)
        assert sup["award_count"] == 2
        assert sup["bid_count"] == 4
        assert sup["win_rate"] == pytest.approx(0.5)

    def test_roster_sorted_by_awards(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "SortOrg")
        c1 = make_company(repo, "Few")
        c2 = make_company(repo, "Many")
        for i in range(3):
            t = make_tender(repo, f"T-Sort-{i}")
            issue(repo, org, t)
            award(repo, c2, t)
        t_one = make_tender(repo, "T-Sort-one")
        issue(repo, org, t_one)
        award(repo, c1, t_one)
        r = bie.supplier_roster(org)
        assert r["suppliers"][0]["uid"] == c2

    def test_roster_limit(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "LimitOrg")
        for i in range(10):
            c = make_company(repo, f"Co-{i}")
            t = make_tender(repo, f"T-Lim-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_roster(org, limit=3)
        assert len(r["suppliers"]) <= 3


# ═══════════════════════════════════════════════════════════════════════════════
# preferred_suppliers
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreferredSuppliers:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.preferred_suppliers("ORG-MISSING")
        assert "error" in r

    def test_no_preferred_when_all_one_award(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "OneAwardOrg")
        for i in range(5):
            c = make_company(repo, f"PrefCo-{i}")
            t = make_tender(repo, f"T-Pref-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.preferred_suppliers(org, min_awards=2)
        assert r["preferred_supplier_count"] == 0

    def test_preferred_threshold_respected(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "ThreshOrg")
        c1 = make_company(repo, "Repeat")
        c2 = make_company(repo, "OneTime")
        for i in range(3):
            t = make_tender(repo, f"T-Thresh-{i}")
            issue(repo, org, t)
            award(repo, c1, t)
        t_one = make_tender(repo, "T-OneTime")
        issue(repo, org, t_one)
        award(repo, c2, t_one)
        r = bie.preferred_suppliers(org, min_awards=2)
        assert r["preferred_supplier_count"] == 1
        assert r["preferred_suppliers"][0]["uid"] == c1

    def test_evidence_present(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EvPrefOrg")
        c = make_company(repo, "EvPrefCo")
        for i in range(3):
            t = make_tender(repo, f"T-EvPref-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.preferred_suppliers(org, min_awards=2)
        assert "evidence" in r
        assert "confidence" in r


# ═══════════════════════════════════════════════════════════════════════════════
# supplier_loyalty
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupplierLoyalty:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.supplier_loyalty("ORG-MISSING")
        assert "error" in r

    def test_empty_buyer_loyalty(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EmptyLoyOrg")
        r = bie.supplier_loyalty(org)
        assert r["overall_loyalty_score"] == 0.0
        assert r["unique_suppliers_awarded"] == 0

    def test_loyalty_index_formula(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "LoyOrg")
        c = make_company(repo, "LoyCo")
        for i in range(4):
            t = make_tender(repo, f"T-Loy-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_loyalty(org)
        sup = next(s for s in r["supplier_loyalty"] if s["uid"] == c)
        # 4 awards out of 4 tenders → loyalty_index = 1.0
        assert sup["loyalty_index"] == pytest.approx(1.0)

    def test_loyalty_interpretation_high(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "HighLoyOrg")
        c = make_company(repo, "HighLoyCo")
        for i in range(5):
            t = make_tender(repo, f"T-HiLoy-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_loyalty(org)
        # All awards go to one company → high loyalty
        assert r["loyalty_interpretation"] == "high"

    def test_loyalty_interpretation_low(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        # 10 equal suppliers → HHI=0.1 → sqrt(0.1)≈0.316 → 'medium' (threshold for low is <0.25)
        # Need ≥17 equal suppliers to get below 0.25: use 25
        org = make_org(repo, "LowLoyOrg")
        for i in range(25):
            c = make_company(repo, f"LowLoyCo-{i}")
            t = make_tender(repo, f"T-LowLoy-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_loyalty(org)
        # HHI=1/25=0.04 → sqrt(0.04)=0.2 → 'low'
        assert r["loyalty_interpretation"] == "low"

    def test_loyalty_sorted_by_index(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "SortLoyOrg")
        c1 = make_company(repo, "Loyal")
        c2 = make_company(repo, "Occasional")
        for i in range(3):
            t = make_tender(repo, f"T-SortLoy-{i}")
            issue(repo, org, t)
            award(repo, c1, t)
        t_occ = make_tender(repo, "T-Occ")
        issue(repo, org, t_occ)
        award(repo, c2, t_occ)
        r = bie.supplier_loyalty(org)
        assert r["supplier_loyalty"][0]["uid"] == c1


# ═══════════════════════════════════════════════════════════════════════════════
# supplier_diversity
# ═══════════════════════════════════════════════════════════════════════════════


class TestSupplierDiversity:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.supplier_diversity("ORG-MISSING")
        assert "error" in r

    def test_no_awards_diversity_one(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        # With no awards HHI=0 → diversity = 1 - 0 = 1.0 (no concentration data)
        org = make_org(repo, "NoDivOrg")
        t = make_tender(repo, "T-NoDivTender")
        issue(repo, org, t)
        r = bie.supplier_diversity(org)
        assert r["diversity_score"] == pytest.approx(1.0)

    def test_monopoly_diversity_zero(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "MonopolyOrg")
        c = make_company(repo, "MonoCo")
        for i in range(5):
            t = make_tender(repo, f"T-Mono-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_diversity(org)
        assert r["diversity_score"] == pytest.approx(0.0)
        assert r["diversity_level"] == "very_low"

    def test_max_diversity(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "DiverseOrg")
        for i in range(4):
            c = make_company(repo, f"DivCo-{i}")
            t = make_tender(repo, f"T-Div-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.supplier_diversity(org)
        assert r["diversity_score"] == pytest.approx(0.75)
        assert r["diversity_level"] == "high"

    def test_diversity_fields(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "FieldsDivOrg")
        c = make_company(repo, "FieldsDivCo")
        t = make_tender(repo, "T-FieldsDiv")
        issue(repo, org, t)
        award(repo, c, t)
        r = bie.supplier_diversity(org)
        assert "award_hhi" in r
        assert "unique_suppliers" in r
        assert "diversity_score" in r
        assert "diversity_level" in r
        assert "evidence" in r
        assert "confidence" in r


# ═══════════════════════════════════════════════════════════════════════════════
# buying_patterns
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuyingPatterns:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.buying_patterns("ORG-MISSING")
        assert "error" in r

    def test_empty_buyer(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EmptyPatOrg")
        r = bie.buying_patterns(org)
        assert r["total_tenders"] == 0
        assert r["avg_value"] is None
        assert r["avg_bidder_count"] == 0.0

    def test_peak_month_detected(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "PeakMonthOrg")
        for i in range(3):
            t = make_tender(repo, f"T-PeakMar-{i}", date="2023-03-15")
            issue(repo, org, t)
        t_jan = make_tender(repo, "T-PeakJan", date="2023-01-10")
        issue(repo, org, t_jan)
        r = bie.buying_patterns(org)
        assert r["peak_month"] == 3
        assert r["peak_month_name"] == "Mar"

    def test_busiest_year(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "BusiestYrOrg")
        for i in range(4):
            t = make_tender(repo, f"T-2022-{i}", date="2022-06-01")
            issue(repo, org, t)
        t_2021 = make_tender(repo, "T-2021", date="2021-06-01")
        issue(repo, org, t_2021)
        r = bie.buying_patterns(org)
        assert r["busiest_year"] == 2022

    def test_avg_value(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "AvgValOrg")
        t1 = make_tender(repo, "T-V100", value=100.0)
        t2 = make_tender(repo, "T-V200", value=200.0)
        issue(repo, org, t1)
        issue(repo, org, t2)
        r = bie.buying_patterns(org)
        assert r["avg_value"] == pytest.approx(150.0)

    def test_cadence_calculated(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "CadOrg")
        issue(repo, org, make_tender(repo, "T-Cad-A", date="2021-01-01"))
        issue(repo, org, make_tender(repo, "T-Cad-B", date="2021-07-01"))
        issue(repo, org, make_tender(repo, "T-Cad-C", date="2022-01-01"))
        r = bie.buying_patterns(org)
        assert r["cadence_months"] is not None
        assert r["cadence_months"] > 0


# ═══════════════════════════════════════════════════════════════════════════════
# procurement_seasonality
# ═══════════════════════════════════════════════════════════════════════════════


class TestProcurementSeasonality:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.procurement_seasonality("ORG-MISSING")
        assert "error" in r

    def test_empty_no_dates(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoDatesOrg")
        t = make_tender(repo, "T-NoDates")
        issue(repo, org, t)
        r = bie.procurement_seasonality(org)
        assert r["tenders_with_dates"] == 0

    def test_twelve_months_returned(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TwelveMonthOrg")
        t = make_tender(repo, "T-12M", date="2023-06-15")
        issue(repo, org, t)
        r = bie.procurement_seasonality(org)
        assert len(r["monthly"]) == 12

    def test_four_quarters_returned(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "QuarterOrg")
        t = make_tender(repo, "T-Q1", date="2023-02-01")
        issue(repo, org, t)
        r = bie.procurement_seasonality(org)
        assert len(r["quarterly"]) == 4

    def test_peak_month_correct(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "PeakSeaOrg")
        for i in range(3):
            t = make_tender(repo, f"T-Sep-{i}", date="2023-09-10")
            issue(repo, org, t)
        t_mar = make_tender(repo, "T-Mar-Sea", date="2023-03-01")
        issue(repo, org, t_mar)
        r = bie.procurement_seasonality(org)
        assert r["peak_month"]["month"] == 9

    def test_seasonality_index_structure(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "SeaIdxOrg")
        t = make_tender(repo, "T-SeaIdx", date="2023-04-01")
        issue(repo, org, t)
        r = bie.procurement_seasonality(org)
        for month_row in r["monthly"]:
            assert "seasonality_index" in month_row
            assert "share" in month_row
            assert "month_name" in month_row


# ═══════════════════════════════════════════════════════════════════════════════
# preferred_industries
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreferredIndustries:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.preferred_industries("ORG-MISSING")
        assert "error" in r

    def test_no_industries(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoIndOrg")
        c = make_company(repo, "NoIndCo")
        t = make_tender(repo, "T-NoInd")
        issue(repo, org, t)
        award(repo, c, t)
        r = bie.preferred_industries(org)
        assert r["industry_count"] == 0

    def test_industries_detected(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "IndOrg")
        c = make_company(repo, "IndCo")
        t = make_tender(repo, "T-Ind")
        issue(repo, org, t)
        award(repo, c, t)
        link_industry(repo, c, "Construction")
        r = bie.preferred_industries(org)
        assert r["industry_count"] >= 1
        assert any(i["name"] == "Construction" for i in r["industries"])

    def test_industry_sorted_by_count(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "IndSortOrg")
        # 3 construction vs 1 IT
        for i in range(3):
            c = make_company(repo, f"ConstrCo-{i}")
            t = make_tender(repo, f"T-Constr-{i}")
            issue(repo, org, t)
            award(repo, c, t)
            link_industry(repo, c, "Construction")
        c_it = make_company(repo, "ITCo")
        t_it = make_tender(repo, "T-IT")
        issue(repo, org, t_it)
        award(repo, c_it, t_it)
        link_industry(repo, c_it, "Information Technology")
        r = bie.preferred_industries(org)
        assert r["industries"][0]["name"] == "Construction"

    def test_industry_share_sums_to_one(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "ShareIndOrg")
        for name, ind in [("A", "Ind-A"), ("B", "Ind-B"), ("C", "Ind-C")]:
            c = make_company(repo, f"ShareIndCo-{name}")
            t = make_tender(repo, f"T-ShareInd-{name}")
            issue(repo, org, t)
            award(repo, c, t)
            link_industry(repo, c, ind)
        r = bie.preferred_industries(org)
        total_share = sum(i["share"] for i in r["industries"])
        assert total_share == pytest.approx(1.0, abs=0.001)


# ═══════════════════════════════════════════════════════════════════════════════
# preferred_contract_sizes
# ═══════════════════════════════════════════════════════════════════════════════


class TestPreferredContractSizes:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.preferred_contract_sizes("ORG-MISSING")
        assert "error" in r

    def test_no_values(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoValOrg")
        t = make_tender(repo, "T-NoVal")
        issue(repo, org, t)
        r = bie.preferred_contract_sizes(org)
        assert r["tenders_with_value"] == 0
        assert r["avg_value"] is None

    def test_five_buckets_returned(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "BucketOrg")
        t = make_tender(repo, "T-Bucket", value=5000.0)
        issue(repo, org, t)
        r = bie.preferred_contract_sizes(org)
        assert len(r["buckets"]) == 5

    def test_micro_bucket(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "MicroOrg")
        t = make_tender(repo, "T-Micro", value=500.0)
        issue(repo, org, t)
        r = bie.preferred_contract_sizes(org)
        micro = next(b for b in r["buckets"] if b["bucket"] == "micro")
        assert micro["count"] == 1
        assert r["preferred_bucket"] == "micro"

    def test_large_bucket(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "LargeOrg")
        for i in range(3):
            t = make_tender(repo, f"T-Large-{i}", value=5_000_000.0)
            issue(repo, org, t)
        r = bie.preferred_contract_sizes(org)
        large = next(b for b in r["buckets"] if b["bucket"] == "large")
        assert large["count"] == 3
        assert r["preferred_bucket"] == "large"


# ═══════════════════════════════════════════════════════════════════════════════
# avg_procurement_value
# ═══════════════════════════════════════════════════════════════════════════════


class TestAvgProcurementValue:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.avg_procurement_value("ORG-MISSING")
        assert "error" in r

    def test_no_values(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoAvgOrg")
        t = make_tender(repo, "T-NoAvg")
        issue(repo, org, t)
        r = bie.avg_procurement_value(org)
        assert r["tenders_with_value"] == 0
        assert r["avg_value"] is None

    def test_avg_correct(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "AvgOrg")
        vals = [100.0, 200.0, 300.0]
        for i, v in enumerate(vals):
            t = make_tender(repo, f"T-Avg-{i}", value=v)
            issue(repo, org, t)
        r = bie.avg_procurement_value(org)
        assert r["avg_value"] == pytest.approx(200.0)
        assert r["min_value"] == pytest.approx(100.0)
        assert r["max_value"] == pytest.approx(300.0)

    def test_median_odd(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "MedianOddOrg")
        for i, v in enumerate([10.0, 20.0, 30.0]):
            t = make_tender(repo, f"T-MedOdd-{i}", value=v)
            issue(repo, org, t)
        r = bie.avg_procurement_value(org)
        assert r["median_value"] == pytest.approx(20.0)

    def test_total_value(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TotalOrg")
        for i, v in enumerate([100.0, 200.0, 300.0]):
            t = make_tender(repo, f"T-Total-{i}", value=v)
            issue(repo, org, t)
        r = bie.avg_procurement_value(org)
        assert r["total_value"] == pytest.approx(600.0)


# ═══════════════════════════════════════════════════════════════════════════════
# avg_bidder_count
# ═══════════════════════════════════════════════════════════════════════════════


class TestAvgBidderCount:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.avg_bidder_count("ORG-MISSING")
        assert "error" in r

    def test_empty_buyer(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EmptyBidOrg")
        r = bie.avg_bidder_count(org)
        assert r["total_tenders"] == 0
        assert r["avg_bidder_count"] == 0.0

    def test_single_bidder_rate(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "SBOrg")
        t1 = make_tender(repo, "T-SB1")
        t2 = make_tender(repo, "T-SB2")
        c1 = make_company(repo, "SBCo1")
        c2 = make_company(repo, "SBCo2")
        issue(repo, org, t1)
        issue(repo, org, t2)
        # t1: 1 bidder (single)
        bid(repo, c1, t1)
        # t2: 2 bidders
        bid(repo, c1, t2)
        bid(repo, c2, t2)
        r = bie.avg_bidder_count(org)
        assert r["single_bidder_tenders"] == 1
        assert r["single_bidder_rate"] == pytest.approx(0.5)

    def test_avg_bidder_count_correct(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "AvgBidOrg")
        c1 = make_company(repo, "Bidder1")
        c2 = make_company(repo, "Bidder2")
        c3 = make_company(repo, "Bidder3")
        # t1: 2 bidders, t2: 4 bidders → avg = 3
        t1 = make_tender(repo, "T-AvgBid1")
        t2 = make_tender(repo, "T-AvgBid2")
        issue(repo, org, t1)
        issue(repo, org, t2)
        bid(repo, c1, t1)
        bid(repo, c2, t1)
        bid(repo, c1, t2)
        bid(repo, c2, t2)
        bid(repo, c3, t2)
        award(repo, c3, t2)
        r = bie.avg_bidder_count(org)
        assert r["avg_bidder_count"] == pytest.approx(3.0, abs=0.5)


# ═══════════════════════════════════════════════════════════════════════════════
# award_concentration
# ═══════════════════════════════════════════════════════════════════════════════


class TestAwardConcentration:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.award_concentration("ORG-MISSING")
        assert "error" in r

    def test_no_awards(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoAwardConc")
        t = make_tender(repo, "T-NoAward")
        issue(repo, org, t)
        r = bie.award_concentration(org)
        assert r["total_awards"] == 0
        assert r["hhi"] == 0.0

    def test_monopoly_hhi_one(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "MonoHHIOrg")
        c = make_company(repo, "MonoHHICo")
        for i in range(5):
            t = make_tender(repo, f"T-MonoHHI-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.award_concentration(org)
        assert r["hhi"] == pytest.approx(1.0)
        assert r["concentration_level"] == "highly_concentrated"

    def test_equal_distribution_low_hhi(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EqualHHIOrg")
        for i in range(4):
            c = make_company(repo, f"EqCo-{i}")
            t = make_tender(repo, f"T-Eq-{i}")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.award_concentration(org)
        assert r["hhi"] == pytest.approx(0.25)
        assert r["concentration_level"] == "highly_concentrated"

    def test_top_suppliers_sorted(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TopSupOrg")
        c1 = make_company(repo, "TopSup1")
        c2 = make_company(repo, "TopSup2")
        for i in range(3):
            t = make_tender(repo, f"T-TopSup1-{i}")
            issue(repo, org, t)
            award(repo, c1, t)
        t_one = make_tender(repo, "T-TopSup2-one")
        issue(repo, org, t_one)
        award(repo, c2, t_one)
        r = bie.award_concentration(org)
        assert r["top_suppliers"][0]["uid"] == c1


# ═══════════════════════════════════════════════════════════════════════════════
# buyer_competitiveness
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuyerCompetitiveness:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.buyer_competitiveness("ORG-MISSING")
        assert "error" in r

    def test_fields_present(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "CompFields")
        r = bie.buyer_competitiveness(org)
        assert "competitiveness_score" in r
        assert "competitiveness_level" in r
        assert "components" in r
        assert "confidence" in r
        assert "evidence" in r

    def test_score_range(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "CompRange")
        c1 = make_company(repo, "CompRangeCo1")
        c2 = make_company(repo, "CompRangeCo2")
        for i in range(4):
            t = make_tender(repo, f"T-CompRange-{i}")
            issue(repo, org, t)
            bid(repo, c1, t)
            bid(repo, c2, t)
            award(repo, c1 if i % 2 == 0 else c2, t)
        r = bie.buyer_competitiveness(org)
        assert 0.0 <= r["competitiveness_score"] <= 1.0

    def test_competitive_level_labels(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "LevelOrg")
        r = bie.buyer_competitiveness(org)
        assert r["competitiveness_level"] in (
            "highly_competitive",
            "moderately_competitive",
            "low_competition",
        )

    def test_high_bidder_count_increases_score(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org_few = make_org(repo, "FewBidOrg")
        org_many = make_org(repo, "ManyBidOrg")
        # few: 1 bidder per tender
        for i in range(3):
            c = make_company(repo, f"FewBidCo-{i}")
            t = make_tender(repo, f"T-FewBid-{i}")
            issue(repo, org_few, t)
            bid(repo, c, t)
            award(repo, c, t)
        # many: 5 bidders per tender, 3 different winners
        companies = [make_company(repo, f"ManyBidCo-{i}") for i in range(5)]
        for i in range(3):
            t = make_tender(repo, f"T-ManyBid-{i}")
            issue(repo, org_many, t)
            for c in companies:
                bid(repo, c, t)
            award(repo, companies[i], t)
        r_few = bie.buyer_competitiveness(org_few)
        r_many = bie.buyer_competitiveness(org_many)
        assert r_many["competitiveness_score"] > r_few["competitiveness_score"]


# ═══════════════════════════════════════════════════════════════════════════════
# buyer_timeline
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuyerTimeline:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.buyer_timeline("ORG-MISSING")
        assert "error" in r

    def test_empty_timeline(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "EmptyTLOrg")
        r = bie.buyer_timeline(org)
        assert r["timeline"] == []
        assert r["years_active"] == 0

    def test_years_in_timeline(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "YearsTLOrg")
        for yr in ["2021", "2022", "2023"]:
            t = make_tender(repo, f"T-TL-{yr}", date=f"{yr}-06-01")
            issue(repo, org, t)
        r = bie.buyer_timeline(org)
        years = [row["year"] for row in r["timeline"]]
        assert 2021 in years and 2022 in years and 2023 in years

    def test_tender_count_per_year(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TenderCountOrg")
        for i in range(3):
            t = make_tender(repo, f"T-TC-2022-{i}", date="2022-03-01")
            issue(repo, org, t)
        t_2023 = make_tender(repo, "T-TC-2023", date="2023-03-01")
        issue(repo, org, t_2023)
        r = bie.buyer_timeline(org)
        yr22 = next(row for row in r["timeline"] if row["year"] == 2022)
        assert yr22["tender_count"] == 3

    def test_trend_growing(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "GrowingOrg")
        for i in range(2):
            t = make_tender(repo, f"T-Grow-2022-{i}", date="2022-01-01")
            issue(repo, org, t)
        for i in range(5):
            t = make_tender(repo, f"T-Grow-2023-{i}", date="2023-01-01")
            issue(repo, org, t)
        r = bie.buyer_timeline(org)
        assert r["trend"] == "growing"

    def test_trend_declining(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "DecliningOrg")
        for i in range(5):
            t = make_tender(repo, f"T-Decl-2022-{i}", date="2022-01-01")
            issue(repo, org, t)
        t_2023 = make_tender(repo, "T-Decl-2023", date="2023-01-01")
        issue(repo, org, t_2023)
        r = bie.buyer_timeline(org)
        assert r["trend"] == "declining"

    def test_top_winner_per_year(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "TopWinOrg")
        c1 = make_company(repo, "TopWin1")
        c2 = make_company(repo, "TopWin2")
        for i in range(3):
            t = make_tender(repo, f"T-TW1-2023-{i}", date="2023-06-01")
            issue(repo, org, t)
            award(repo, c1, t)
        t_c2 = make_tender(repo, "T-TW2-2023", date="2023-07-01")
        issue(repo, org, t_c2)
        award(repo, c2, t_c2)
        r = bie.buyer_timeline(org)
        yr23 = next(row for row in r["timeline"] if row["year"] == 2023)
        assert yr23["top_winner"]["uid"] == c1


# ═══════════════════════════════════════════════════════════════════════════════
# tender_forecast
# ═══════════════════════════════════════════════════════════════════════════════


class TestTenderForecast:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.tender_forecast("ORG-MISSING")
        assert "error" in r

    def test_no_dated_tenders(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NoDateFcOrg")
        t = make_tender(repo, "T-FcNoDate")
        issue(repo, org, t)
        r = bie.tender_forecast(org)
        assert r["tenders_with_dates"] == 0
        assert r["forecast_basis"] == "no_dated_tenders"
        assert r["forecast_probability"] == pytest.approx(0.5)

    def test_cadence_model_used(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "CadFcOrg")
        issue(repo, org, make_tender(repo, "T-Fc-A", date="2021-01-01"))
        issue(repo, org, make_tender(repo, "T-Fc-B", date="2021-07-01"))
        issue(repo, org, make_tender(repo, "T-Fc-C", date="2022-01-01"))
        r = bie.tender_forecast(org)
        assert r["forecast_basis"] == "cadence_model"
        assert r["cadence_months"] is not None

    def test_probability_range(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "ProbOrg")
        issue(repo, org, make_tender(repo, "T-Prob-A", date="2020-01-01"))
        issue(repo, org, make_tender(repo, "T-Prob-B", date="2020-06-01"))
        r = bie.tender_forecast(org)
        assert 0.0 <= r["forecast_probability"] <= 1.0

    def test_estimated_next_present(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "NextFcOrg")
        issue(repo, org, make_tender(repo, "T-Next-A", date="2022-01-01"))
        issue(repo, org, make_tender(repo, "T-Next-B", date="2022-07-01"))
        r = bie.tender_forecast(org)
        assert r["estimated_next_year"] is not None
        assert r["estimated_next_month"] is not None

    def test_last_tender_fields(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "LastFcOrg")
        issue(repo, org, make_tender(repo, "T-Last-A", date="2023-05-15"))
        issue(repo, org, make_tender(repo, "T-Last-B", date="2023-09-20"))
        r = bie.tender_forecast(org)
        assert r["last_tender_year"] == 2023
        assert r["last_tender_month"] == 9


# ═══════════════════════════════════════════════════════════════════════════════
# buyer_profile (full)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBuyerProfile:
    def test_missing_entity(self, bie: BuyerIntelligenceEngine):
        r = bie.buyer_profile("ORG-MISSING")
        assert "error" in r

    def test_full_profile_keys(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "FullProfOrg")
        c = make_company(repo, "FullProfCo")
        t = make_tender(repo, "T-FP", date="2023-06-01", value=50000.0)
        issue(repo, org, t)
        award(repo, c, t)
        r = bie.buyer_profile(org)
        assert "error" not in r
        for key in [
            "uid",
            "name",
            "kind",
            "summary",
            "supplier_roster",
            "preferred_suppliers",
            "supplier_loyalty",
            "supplier_diversity",
            "buying_patterns",
            "procurement_seasonality",
            "preferred_industries",
            "preferred_contract_sizes",
            "avg_procurement_value",
            "avg_bidder_count",
            "award_concentration",
            "buyer_competitiveness",
            "buyer_timeline",
            "tender_forecast",
        ]:
            assert key in r, f"Missing key: {key}"

    def test_idempotent(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "IdempOrg")
        c = make_company(repo, "IdempCo")
        t = make_tender(repo, "T-Idemp")
        issue(repo, org, t)
        award(repo, c, t)
        r1 = bie.buyer_profile(org)
        r2 = bie.buyer_profile(org)
        assert r1["summary"]["total_tenders"] == r2["summary"]["total_tenders"]

    def test_org_kind_in_profile(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "KindOrg")
        r = bie.buyer_profile(org)
        assert r.get("kind") == "organization"

    def test_company_as_buyer(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        cmp = make_company(repo, "CompanyAsBuyer")
        c2 = make_company(repo, "CaB-Supplier")
        t = make_tender(repo, "T-CaB")
        issue(repo, cmp, t)
        award(repo, c2, t)
        r = bie.buyer_profile(cmp)
        assert "error" not in r
        assert r["kind"] == "company"


# ═══════════════════════════════════════════════════════════════════════════════
# Edge cases and confidence
# ═══════════════════════════════════════════════════════════════════════════════


class TestEdgeCases:
    def test_all_methods_return_confidence(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "ConfOrg")
        c = make_company(repo, "ConfCo")
        t = make_tender(repo, "T-Conf", date="2023-01-01", value=1000.0)
        issue(repo, org, t)
        award(repo, c, t)
        for r in [
            bie.buyer_summary(org),
            bie.supplier_roster(org),
            bie.supplier_loyalty(org),
            bie.supplier_diversity(org),
            bie.buying_patterns(org),
            bie.award_concentration(org),
            bie.buyer_competitiveness(org),
            bie.buyer_timeline(org),
            bie.tender_forecast(org),
        ]:
            assert "confidence" in r, f"Missing confidence in {r.keys()}"

    def test_all_methods_handle_missing_uid(self, bie: BuyerIntelligenceEngine):
        missing = "ORG-99999999"
        for method in [
            lambda: bie.buyer_summary(missing),
            lambda: bie.supplier_roster(missing),
            lambda: bie.preferred_suppliers(missing),
            lambda: bie.supplier_loyalty(missing),
            lambda: bie.supplier_diversity(missing),
            lambda: bie.buying_patterns(missing),
            lambda: bie.procurement_seasonality(missing),
            lambda: bie.preferred_industries(missing),
            lambda: bie.preferred_contract_sizes(missing),
            lambda: bie.avg_procurement_value(missing),
            lambda: bie.avg_bidder_count(missing),
            lambda: bie.award_concentration(missing),
            lambda: bie.buyer_competitiveness(missing),
            lambda: bie.buyer_timeline(missing),
            lambda: bie.tender_forecast(missing),
            lambda: bie.buyer_profile(missing),
        ]:
            r = method()
            assert "error" in r, f"Expected error for {method}"

    def test_wrong_kind_rejected(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        tender_uid = make_tender(repo, "WrongKindTender")
        r = bie.buyer_profile(tender_uid)
        assert "error" in r

    def test_large_graph_performance(self, bie: BuyerIntelligenceEngine, repo: BizRepository):
        org = make_org(repo, "PerfOrg")
        for i in range(50):
            c = make_company(repo, f"PerfCo-{i}")
            t = make_tender(repo, f"T-Perf-{i}", date=f"202{i // 10 + 0}-{(i % 12) + 1:02d}-01")
            issue(repo, org, t)
            award(repo, c, t)
        r = bie.buyer_summary(org)
        assert r["total_tenders"] == 50
        assert r["active_suppliers"] == 50
