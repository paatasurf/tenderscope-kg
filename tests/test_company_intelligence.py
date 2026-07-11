"""
Tests for CompanyIntelligenceEngine.

Each test builds a minimal in-memory graph, runs one or more CIE methods,
and asserts on both the returned data and the evidence references.
No files are touched; everything runs in SQLite :memory:.
"""

from __future__ import annotations

import sqlite3

import pytest

from tenderscope_kg.company_intelligence import CompanyIntelligenceEngine, _parse_value
from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.repository._sqlite import BizRepositorySQLite

# ── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture
def repo() -> BizRepositorySQLite:
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    return repo


@pytest.fixture
def cie(repo: BizRepositorySQLite) -> CompanyIntelligenceEngine:
    return CompanyIntelligenceEngine(repo)


# ── Helpers ──────────────────────────────────────────────────────────────────


def _company(repo, name, attrs=None):
    e, _ = repo.put_entity(BizEntityKind.COMPANY, name, attributes=attrs or {}, source="test")
    return e


def _tender(repo, name, attrs=None):
    e, _ = repo.put_entity(BizEntityKind.TENDER, name, attributes=attrs or {}, source="test")
    return e


def _org(repo, name):
    e, _ = repo.put_entity(BizEntityKind.ORGANIZATION, name, source="test")
    return e


def _permit(repo, name, attrs=None):
    e, _ = repo.put_entity(BizEntityKind.PERMIT, name, attributes=attrs or {}, source="test")
    return e


def _city(repo, name):
    e, _ = repo.put_entity(BizEntityKind.CITY, name, source="test")
    return e


def _province(repo, name):
    e, _ = repo.put_entity(BizEntityKind.PROVINCE, name, source="test")
    return e


def _industry(repo, name):
    e, _ = repo.put_entity(BizEntityKind.INDUSTRY, name, source="test")
    return e


def _rel(repo, src_uid, kind, tgt_uid, attrs=None, valid_from=None):
    r, _ = repo.put_relation(src_uid, kind, tgt_uid, source="test", attributes=attrs or {}, valid_from=valid_from)
    return r


# ══════════════════════════════════════════════════════════════════════════════
# _require_company (error cases)
# ══════════════════════════════════════════════════════════════════════════════


def test_require_company_not_found(cie):
    result = cie.company_profile("CMP-99999999")
    assert "error" in result
    assert "not found" in result["error"].lower()


def test_require_company_wrong_kind(repo, cie):
    tender = _tender(repo, "Some Tender")
    result = cie.company_profile(tender.uid)
    assert "error" in result
    assert "company" in result["error"].lower()


# ══════════════════════════════════════════════════════════════════════════════
# company_summary
# ══════════════════════════════════════════════════════════════════════════════


def test_summary_basic_fields(repo, cie):
    co = _company(repo, "Acme Construction Ltd")
    result = cie.company_summary(co.uid)
    assert result["uid"] == co.uid
    assert result["name"] == "Acme Construction Ltd"
    assert "confidence_score" in result
    assert "evidence_count" in result
    assert isinstance(result["tenders_won"], int)
    assert isinstance(result["total_awarded_value"], float)


def test_summary_counts_won_tenders(repo, cie):
    co = _company(repo, "Builder Corp")
    t1 = _tender(repo, "Bridge Repair", {"contract_value": "500000"})
    t2 = _tender(repo, "Road Work", {"contract_value": "300000"})
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t1.uid,
        attrs={"contract_value": 500000},
        valid_from="2025/06/01",
    )
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t2.uid,
        attrs={"contract_value": 300000},
        valid_from="2025/07/01",
    )

    result = cie.company_summary(co.uid)
    assert result["tenders_won"] == 2


def test_summary_evidence_count(repo, cie):
    co = _company(repo, "Evidence Corp")
    t1 = _tender(repo, "Tender A")
    t2 = _tender(repo, "Tender B")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t1.uid)
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t2.uid)

    result = cie.company_summary(co.uid)
    assert result["evidence_count"] >= 2


def test_summary_locations_collected(repo, cie):
    co = _company(repo, "Geo Corp")
    city = _city(repo, "Nanaimo")
    prov = _province(repo, "British Columbia")
    _rel(repo, co.uid, BizRelationKind.IN_CITY, city.uid)
    _rel(repo, co.uid, BizRelationKind.IN_PROVINCE, prov.uid)

    result = cie.company_summary(co.uid)
    assert "Nanaimo" in result["locations"]
    assert "British Columbia" in result["locations"]


def test_summary_industries_collected(repo, cie):
    co = _company(repo, "Construction Co")
    ind = _industry(repo, "Construction")
    _rel(repo, co.uid, BizRelationKind.IN_INDUSTRY, ind.uid)

    result = cie.company_summary(co.uid)
    assert "Construction" in result["industries"]


def test_summary_confidence_increases_with_edges(repo, cie):
    co_few = _company(repo, "Few Edges Co")
    co_many = _company(repo, "Many Edges Co")
    # Add 8 tenders to many-edges company
    for i in range(8):
        t = _tender(repo, f"Tender {i}")
        _rel(repo, co_many.uid, BizRelationKind.AWARDED_TO, t.uid)

    few_result = cie.company_summary(co_few.uid)
    many_result = cie.company_summary(co_many.uid)
    assert many_result["confidence_score"] > few_result["confidence_score"]


# ══════════════════════════════════════════════════════════════════════════════
# company_stats
# ══════════════════════════════════════════════════════════════════════════════


def test_stats_total_contract_value(repo, cie):
    co = _company(repo, "ValueCo")
    t1 = _tender(repo, "Contract A", {"contract_value": "CAD 500,000.00"})
    t2 = _tender(repo, "Contract B", {"contract_value": "CAD 300,000.00"})
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t1.uid,
        attrs={"contract_value": 500000, "award_date": "2025/01/01"},
    )
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t2.uid,
        attrs={"contract_value": 300000, "award_date": "2025/06/01"},
    )

    result = cie.company_stats(co.uid)
    assert result["total_contract_value"] == pytest.approx(800000.0)
    assert result["contract_count"] == 2
    assert result["largest_contract"] == pytest.approx(500000.0)
    assert result["smallest_contract"] == pytest.approx(300000.0)


def test_stats_average_contract_value(repo, cie):
    co = _company(repo, "AvgCo")
    for val in [100000, 200000, 300000]:
        t = _tender(repo, f"T{val}")
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, attrs={"contract_value": val})
    result = cie.company_stats(co.uid)
    assert result["average_contract_value"] == pytest.approx(200000.0)


def test_stats_permit_counts(repo, cie):
    co = _company(repo, "Permit Corp")
    p1 = _permit(repo, "BP-001", {"project_value_numeric": 150000, "issue_date": "2025/03/01"})
    p2 = _permit(repo, "BP-002", {"project_value_numeric": 75000, "issue_date": "2025/05/01"})
    _rel(repo, co.uid, BizRelationKind.APPLIED_FOR, p1.uid)
    _rel(repo, co.uid, BizRelationKind.CONTRACTED_FOR, p2.uid)

    result = cie.company_stats(co.uid)
    assert result["permit_count"] == 2
    assert result["total_permit_value"] == pytest.approx(225000.0)


def test_stats_yearly_breakdown(repo, cie):
    co = _company(repo, "YearlyCo")
    for year, val in [("2024", 100000), ("2024", 200000), ("2025", 500000)]:
        t = _tender(repo, f"Tender {year} {val}")
        _rel(
            repo,
            co.uid,
            BizRelationKind.AWARDED_TO,
            t.uid,
            attrs={"contract_value": val, "award_date": f"{year}/01/01"},
        )

    result = cie.company_stats(co.uid)
    yearly = result["yearly_stats"]
    assert "2024" in yearly
    assert "2025" in yearly
    assert yearly["2024"]["contract_count"] == 2
    assert yearly["2024"]["contract_value"] == pytest.approx(300000.0)
    assert yearly["2025"]["contract_count"] == 1


def test_stats_evidence_list(repo, cie):
    co = _company(repo, "EvidenceCo")
    t = _tender(repo, "Tender EV")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.company_stats(co.uid)
    assert result["evidence_count"] >= 1
    assert len(result["evidence"]) >= 1
    first_ev = result["evidence"][0]
    assert "uid" in first_ev
    assert "relation" in first_ev
    assert "name" in first_ev


def test_stats_activity_dates(repo, cie):
    co = _company(repo, "DateCo")
    t1 = _tender(repo, "Early Tender")
    t2 = _tender(repo, "Late Tender")
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t1.uid,
        attrs={"award_date": "2023/01/01", "contract_value": 100000},
    )
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t2.uid,
        attrs={"award_date": "2026/06/01", "contract_value": 200000},
    )

    result = cie.company_stats(co.uid)
    assert result["first_activity"] is not None
    assert result["latest_activity"] is not None
    assert result["first_activity"] <= result["latest_activity"]


# ══════════════════════════════════════════════════════════════════════════════
# company_buyers
# ══════════════════════════════════════════════════════════════════════════════


def test_buyers_via_tender_issued_by(repo, cie):
    co = _company(repo, "BuyerCo")
    org = _org(repo, "Department of Public Works")
    tender = _tender(repo, "Bridge Repair")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, tender.uid)
    _rel(repo, tender.uid, BizRelationKind.ISSUED_BY, org.uid)

    result = cie.company_buyers(co.uid)
    assert result["buyer_count"] >= 1
    buyer_names = [b["name"] for b in result["buyers"]]
    assert "Department of Public Works" in buyer_names


def test_buyers_evidence_path_present(repo, cie):
    co = _company(repo, "PathCo")
    org = _org(repo, "Transport Canada")
    tender = _tender(repo, "Airport Work")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, tender.uid)
    _rel(repo, tender.uid, BizRelationKind.ISSUED_BY, org.uid)

    result = cie.company_buyers(co.uid)
    buyer = next(b for b in result["buyers"] if b["name"] == "Transport Canada")
    assert len(buyer["tenders"]) >= 1
    assert "evidence_path" in buyer["tenders"][0]


def test_buyers_empty_when_no_tenders(repo, cie):
    co = _company(repo, "NoBuyersCo")
    result = cie.company_buyers(co.uid)
    assert result["buyer_count"] == 0
    assert result["buyers"] == []


# ══════════════════════════════════════════════════════════════════════════════
# company_competitors
# ══════════════════════════════════════════════════════════════════════════════


def test_competitors_via_shared_tender(repo, cie):
    co_a = _company(repo, "Alpha Builders")
    co_b = _company(repo, "Beta Construction")
    tender = _tender(repo, "Shared Contract")
    _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, tender.uid)
    _rel(repo, co_b.uid, BizRelationKind.AWARDED_TO, tender.uid)

    result = cie.company_competitors(co_a.uid)
    comp_uids = [c["uid"] for c in result["competitors"]]
    assert co_b.uid in comp_uids


def test_competitors_shared_tender_evidence(repo, cie):
    co_a = _company(repo, "Gamma Roofing")
    co_b = _company(repo, "Delta Roofing")
    tender = _tender(repo, "Roof Replacement")
    _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, tender.uid)
    _rel(repo, co_b.uid, BizRelationKind.AWARDED_TO, tender.uid)

    result = cie.company_competitors(co_a.uid)
    comp = next(c for c in result["competitors"] if c["uid"] == co_b.uid)
    assert len(comp["shared_tenders"]) >= 1
    assert "evidence_path" in comp["shared_tenders"][0]


def test_competitors_excludes_self(repo, cie):
    co = _company(repo, "SelfCo")
    tender = _tender(repo, "SelfTender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, tender.uid)

    result = cie.company_competitors(co.uid)
    comp_uids = [c["uid"] for c in result["competitors"]]
    assert co.uid not in comp_uids


def test_competitors_ranked_by_shared_evidence(repo, cie):
    co_a = _company(repo, "Center Co")
    co_b = _company(repo, "Big Rival")  # shares 2 tenders
    co_c = _company(repo, "Small Rival")  # shares 1 tender

    t1 = _tender(repo, "Tender 1")
    t2 = _tender(repo, "Tender 2")
    t3 = _tender(repo, "Tender 3")

    _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, t1.uid)
    _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, t2.uid)
    _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, t3.uid)
    _rel(repo, co_b.uid, BizRelationKind.AWARDED_TO, t1.uid)
    _rel(repo, co_b.uid, BizRelationKind.AWARDED_TO, t2.uid)
    _rel(repo, co_c.uid, BizRelationKind.AWARDED_TO, t3.uid)

    result = cie.company_competitors(co_a.uid)
    comps = result["competitors"]
    assert len(comps) >= 2
    # Big Rival (2 shared) should rank above Small Rival (1 shared)
    uids_ordered = [c["uid"] for c in comps]
    assert uids_ordered.index(co_b.uid) < uids_ordered.index(co_c.uid)


def test_competitors_no_activity(repo, cie):
    co = _company(repo, "Isolated Co")
    result = cie.company_competitors(co.uid)
    assert result["competitor_count"] == 0
    assert result["competitors"] == []


# ══════════════════════════════════════════════════════════════════════════════
# company_contracts
# ══════════════════════════════════════════════════════════════════════════════


def test_contracts_sorted_by_value(repo, cie):
    co = _company(repo, "SortCo")
    for val in [100000, 500000, 250000]:
        t = _tender(repo, f"T{val}")
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, attrs={"contract_value": val})

    result = cie.company_contracts(co.uid)
    values = [c["contract_value"] for c in result["contracts"] if c.get("contract_value")]
    assert values == sorted(values, reverse=True)


def test_contracts_total_value(repo, cie):
    co = _company(repo, "TotalCo")
    for val in [200000, 300000]:
        t = _tender(repo, f"T{val}")
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, attrs={"contract_value": val})

    result = cie.company_contracts(co.uid)
    assert result["total_value"] == pytest.approx(500000.0)
    assert result["average_value"] == pytest.approx(250000.0)
    assert result["largest_contract"] == pytest.approx(300000.0)


def test_contracts_evidence_path(repo, cie):
    co = _company(repo, "PathCo2")
    t = _tender(repo, "Path Tender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, attrs={"contract_value": 100000})

    result = cie.company_contracts(co.uid)
    assert len(result["contracts"]) == 1
    contract = result["contracts"][0]
    assert "evidence_path" in contract
    assert co.uid in contract["evidence_path"]
    assert t.uid in contract["evidence_path"]


def test_contracts_empty(repo, cie):
    co = _company(repo, "EmptyCo")
    result = cie.company_contracts(co.uid)
    assert result["contract_count"] == 0
    assert result["total_value"] == 0.0
    assert result["contracts"] == []


# ══════════════════════════════════════════════════════════════════════════════
# company_tenders
# ══════════════════════════════════════════════════════════════════════════════


def test_tenders_won_vs_submitted(repo, cie):
    co = _company(repo, "TenderCo")
    won = _tender(repo, "Won Tender")
    sub = _tender(repo, "Submitted Tender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, won.uid)
    _rel(repo, co.uid, BizRelationKind.SUBMITTED_BID, sub.uid)

    result = cie.company_tenders(co.uid)
    assert result["tenders_won_count"] == 1
    assert result["tenders_submitted_count"] == 1
    won_uids = [t["uid"] for t in result["tenders_won"]]
    sub_uids = [t["uid"] for t in result["tenders_submitted"]]
    assert won.uid in won_uids
    assert sub.uid in sub_uids


def test_tenders_includes_evidence_path(repo, cie):
    co = _company(repo, "EvidTenderCo")
    t = _tender(repo, "Evid Tender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.company_tenders(co.uid)
    entry = result["tenders_won"][0]
    assert "evidence_path" in entry
    assert co.uid in entry["evidence_path"]


def test_tenders_category_field(repo, cie):
    co = _company(repo, "CatCo")
    t = _tender(repo, "Construction Tender", {"category": "Construction"})
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.company_tenders(co.uid)
    entry = result["tenders_won"][0]
    assert entry["category"] == "Construction"


# ══════════════════════════════════════════════════════════════════════════════
# company_timeline
# ══════════════════════════════════════════════════════════════════════════════


def test_timeline_sorted_chronologically(repo, cie):
    co = _company(repo, "TimeCo")
    t1 = _tender(repo, "Late Tender")
    t2 = _tender(repo, "Early Tender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t1.uid, valid_from="2026/06/01")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t2.uid, valid_from="2024/01/01")

    result = cie.company_timeline(co.uid)
    events = result["events"]
    assert len(events) == 2
    assert events[0]["date"] <= events[1]["date"]


def test_timeline_event_structure(repo, cie):
    co = _company(repo, "EventCo")
    t = _tender(repo, "Event Tender", {"award_date": "2025/05/15"})
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, valid_from="2025/05/15")

    result = cie.company_timeline(co.uid)
    assert result["event_count"] >= 1
    ev = result["events"][0]
    assert "date" in ev
    assert "event_type" in ev
    assert "counterpart_uid" in ev
    assert "evidence_path" in ev


def test_timeline_yearly_summary(repo, cie):
    co = _company(repo, "YearTimeCo")
    for i, year in enumerate(["2024", "2024", "2025"]):
        t = _tender(repo, f"T-{year}-idx{i}")
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid, valid_from=f"{year}/01/01")

    result = cie.company_timeline(co.uid)
    yearly = result["yearly_activity"]
    assert yearly.get("2024", 0) == 2
    assert yearly.get("2025", 0) == 1


def test_timeline_empty_no_dates(repo, cie):
    co = _company(repo, "NoDatCo")
    t = _tender(repo, "Undated Tender")
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)
    # No valid_from, no dates in tender attributes → events may be 0 or skipped

    result = cie.company_timeline(co.uid)
    # Should not crash; either 0 events or events with None dates excluded
    assert isinstance(result["events"], list)


# ══════════════════════════════════════════════════════════════════════════════
# company_locations
# ══════════════════════════════════════════════════════════════════════════════


def test_locations_cities_and_provinces(repo, cie):
    co = _company(repo, "LocCo")
    city = _city(repo, "Victoria")
    prov = _province(repo, "British Columbia")
    _rel(repo, co.uid, BizRelationKind.IN_CITY, city.uid)
    _rel(repo, co.uid, BizRelationKind.IN_PROVINCE, prov.uid)

    result = cie.company_locations(co.uid)
    city_names = [c["name"] for c in result["cities"]]
    prov_names = [p["name"] for p in result["provinces"]]
    assert "Victoria" in city_names
    assert "British Columbia" in prov_names


def test_locations_evidence_paths(repo, cie):
    co = _company(repo, "LocEvCo")
    city = _city(repo, "Surrey")
    _rel(repo, co.uid, BizRelationKind.IN_CITY, city.uid)

    result = cie.company_locations(co.uid)
    city_entry = result["cities"][0]
    assert "evidence_path" in city_entry
    assert co.uid in city_entry["evidence_path"]
    assert city.uid in city_entry["evidence_path"]


def test_locations_attribute_fields(repo, cie):
    co = _company(repo, "AttrLocCo", attrs={"city": "Burnaby", "province": "BC"})
    result = cie.company_locations(co.uid)
    assert result["attribute_city"] == "Burnaby"
    assert result["attribute_province"] == "BC"


def test_locations_empty(repo, cie):
    co = _company(repo, "NoLocCo")
    result = cie.company_locations(co.uid)
    assert result["location_count"] == 0
    assert result["cities"] == []
    assert result["provinces"] == []


# ══════════════════════════════════════════════════════════════════════════════
# company_industries
# ══════════════════════════════════════════════════════════════════════════════


def test_industries_direct_relation(repo, cie):
    co = _company(repo, "IndCo")
    ind = _industry(repo, "Construction")
    _rel(repo, co.uid, BizRelationKind.IN_INDUSTRY, ind.uid)

    result = cie.company_industries(co.uid)
    names = [i["name"] for i in result["industries"]]
    assert "Construction" in names
    assert result["industries"][0]["evidence_path"]


def test_industries_inferred_from_tenders(repo, cie):
    co = _company(repo, "InferCo")
    t = _tender(repo, "Roof Replacement", {"category": "Construction"})
    _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.company_industries(co.uid)
    inferred_names = [i["name"] for i in result["inferred_categories"]]
    assert "Construction" in inferred_names


def test_industries_inferred_evidence_count(repo, cie):
    co = _company(repo, "MultiCatCo")
    for i in range(3):
        t = _tender(repo, f"Construction Tender {i}", {"category": "Construction"})
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.company_industries(co.uid)
    cat = next(c for c in result["inferred_categories"] if c["name"] == "Construction")
    assert cat["evidence_count"] == 3


# ══════════════════════════════════════════════════════════════════════════════
# company_profile (integration)
# ══════════════════════════════════════════════════════════════════════════════


def test_profile_assembles_all_subqueries(repo, cie):
    co = _company(repo, "ProfileCo")
    t = _tender(repo, "Big Contract", {"contract_value": "CAD 500,000"})
    org = _org(repo, "Buyer Org")
    city = _city(repo, "Vancouver")
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t.uid,
        attrs={"contract_value": 500000, "award_date": "2025/05/01"},
    )
    _rel(repo, t.uid, BizRelationKind.ISSUED_BY, org.uid)
    _rel(repo, co.uid, BizRelationKind.IN_CITY, city.uid)

    result = cie.company_profile(co.uid)
    assert result["uid"] == co.uid
    assert result["name"] == "ProfileCo"
    assert "summary" in result
    assert "stats" in result
    assert "buyers" in result
    assert "competitors" in result
    assert "contracts" in result
    assert "tenders" in result
    assert "timeline" in result
    assert "locations" in result
    assert "industries" in result


def test_profile_error_propagates(cie):
    result = cie.company_profile("CMP-00000000")
    assert "error" in result


def test_profile_no_nested_errors(repo, cie):
    """A well-formed company with relations should not produce any nested errors."""
    co = _company(repo, "CleanProfileCo")
    t = _tender(repo, "Clean Tender", {"contract_value": "100000"})
    _rel(
        repo,
        co.uid,
        BizRelationKind.AWARDED_TO,
        t.uid,
        attrs={"contract_value": 100000, "award_date": "2025/01/01"},
    )

    result = cie.company_profile(co.uid)
    assert "error" not in result
    for key in ["summary", "stats", "contracts", "tenders", "timeline", "locations", "industries"]:
        sub = result.get(key, {})
        assert "error" not in sub, f"Unexpected error in sub-query '{key}': {sub.get('error')}"


# ══════════════════════════════════════════════════════════════════════════════
# Graph traversal queries
# ══════════════════════════════════════════════════════════════════════════════


def test_companies_by_city(repo, cie):
    co1 = _company(repo, "Vancouver Builder A")
    co2 = _company(repo, "Vancouver Builder B")
    co3 = _company(repo, "Surrey Builder")
    city_van = _city(repo, "Vancouver")
    city_sur = _city(repo, "Surrey")
    _rel(repo, co1.uid, BizRelationKind.IN_CITY, city_van.uid)
    _rel(repo, co2.uid, BizRelationKind.IN_CITY, city_van.uid)
    _rel(repo, co3.uid, BizRelationKind.IN_CITY, city_sur.uid)

    result = cie.companies_by_city("Vancouver")
    uids = [c["uid"] for c in result["companies"]]
    assert co1.uid in uids
    assert co2.uid in uids
    assert co3.uid not in uids


def test_companies_by_city_not_found(repo, cie):
    result = cie.companies_by_city("Atlantis")
    assert "error" in result


def test_companies_by_province(repo, cie):
    co = _company(repo, "BC Builder")
    prov = _province(repo, "British Columbia")
    _rel(repo, co.uid, BizRelationKind.IN_PROVINCE, prov.uid)

    result = cie.companies_by_province("British Columbia")
    uids = [c["uid"] for c in result["companies"]]
    assert co.uid in uids


def test_companies_by_province_not_found(repo, cie):
    result = cie.companies_by_province("Nonexistent Province")
    assert "error" in result


def test_most_connected_companies(repo, cie):
    co_many = _company(repo, "Connected Corp")
    _company(repo, "Isolated Corp")
    for i in range(5):
        t = _tender(repo, f"Connected Tender {i}")
        _rel(repo, co_many.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.most_connected_companies(limit=10)
    assert len(result["companies"]) >= 2
    # Connected Corp should appear first
    first = result["companies"][0]
    assert first["uid"] == co_many.uid
    assert first["total_edges"] >= 5
    assert first["total_edges"] > result["companies"][-1]["total_edges"] or len(result["companies"]) == 1


def test_most_connected_edge_counts(repo, cie):
    co = _company(repo, "EdgeCountCo")
    for i in range(3):
        t = _tender(repo, f"EC Tender {i}")
        _rel(repo, co.uid, BizRelationKind.AWARDED_TO, t.uid)

    result = cie.most_connected_companies(limit=5)
    entry = next(c for c in result["companies"] if c["uid"] == co.uid)
    assert entry["out_edges"] >= 3
    assert entry["total_edges"] >= 3


def test_top_competitors_ranking(repo, cie):
    # Company A: wins 3 tenders from 2 different orgs
    co_a = _company(repo, "Top Co A")
    org1 = _org(repo, "Buyer 1")
    org2 = _org(repo, "Buyer 2")
    for i in range(3):
        t = _tender(repo, f"Tender A{i}")
        _rel(repo, co_a.uid, BizRelationKind.AWARDED_TO, t.uid)
        org = org1 if i < 2 else org2
        _rel(repo, t.uid, BizRelationKind.ISSUED_BY, org.uid)

    # Company B: wins 1 tender from 1 org
    co_b = _company(repo, "Top Co B")
    t_b = _tender(repo, "Tender B0")
    _rel(repo, co_b.uid, BizRelationKind.AWARDED_TO, t_b.uid)
    _rel(repo, t_b.uid, BizRelationKind.ISSUED_BY, org1.uid)

    result = cie.top_competitors(limit=10)
    uids = [c["uid"] for c in result["top_competitors"]]
    assert co_a.uid in uids
    assert co_b.uid in uids
    # co_a has more buyers → should rank first
    assert uids.index(co_a.uid) < uids.index(co_b.uid)


# ══════════════════════════════════════════════════════════════════════════════
# Helper function unit tests
# ══════════════════════════════════════════════════════════════════════════════


def test_parse_value_int():
    assert _parse_value(500000) == pytest.approx(500000.0)


def test_parse_value_float():
    assert _parse_value(273936.60) == pytest.approx(273936.60)


def test_parse_value_cad_string():
    assert _parse_value("CAD 273,936.60") == pytest.approx(273936.60)


def test_parse_value_plain_string():
    assert _parse_value("500000") == pytest.approx(500000.0)


def test_parse_value_empty():
    assert _parse_value("") is None


def test_parse_value_none():
    assert _parse_value(None) is None


def test_parse_value_large():
    assert _parse_value("CAD 2,826,600.00") == pytest.approx(2826600.0)
