"""
Tests for the BizRepository layer.
Covers: UID allocation, entity create/update/dedup, relations, history, FTS, stats.
"""
from __future__ import annotations

import sqlite3
import tempfile
from pathlib import Path

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.repository._sqlite import BizRepositorySQLite


@pytest.fixture
def repo() -> BizRepositorySQLite:
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    return repo


# ── UID allocation ─────────────────────────────────────────────────────────────

def test_uid_format(repo):
    e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    assert e.uid.startswith("CMP-")
    assert len(e.uid) == 12  # "CMP-" + 8 digits


def test_uid_monotonic(repo):
    e1, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
    e2, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
    n1 = int(e1.uid.split("-")[1])
    n2 = int(e2.uid.split("-")[1])
    assert n2 == n1 + 1


def test_uid_per_kind_independent(repo):
    cmp, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    ten, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
    assert cmp.uid.startswith("CMP-")
    assert ten.uid.startswith("TEN-")
    assert int(cmp.uid.split("-")[1]) == 1
    assert int(ten.uid.split("-")[1]) == 1


# ── Entity create / dedup / update ────────────────────────────────────────────

def test_entity_create(repo):
    e, created = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
    assert created is True
    assert e.uid.startswith("CMP-")
    assert e.name == "Acme Corp"
    assert e.canonical_name == "acme corp"
    assert e.kind == BizEntityKind.COMPANY


def test_entity_dedup_by_canonical(repo):
    e1, c1 = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
    e2, c2 = repo.put_entity(BizEntityKind.COMPANY, "ACME CORP")  # same canonical
    assert c1 is True
    assert c2 is False
    assert e1.uid == e2.uid


def test_entity_different_kinds_not_deduped(repo):
    e1, _ = repo.put_entity(BizEntityKind.COMPANY, "Riverside")
    e2, _ = repo.put_entity(BizEntityKind.CITY, "Riverside")
    assert e1.uid != e2.uid


def test_entity_attributes_merged_on_update(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"city": "Vancouver"})
    e2, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"phone": "604-555-0100"})
    assert e2.attributes.get("city") == "Vancouver"
    assert e2.attributes.get("phone") == "604-555-0100"


def test_entity_retrieve_by_uid(repo):
    e, _ = repo.put_entity(BizEntityKind.TENDER, "Park Renovation 2025")
    fetched = repo.get(e.uid)
    assert fetched is not None
    assert fetched.uid == e.uid
    assert fetched.kind == BizEntityKind.TENDER


def test_entity_not_found(repo):
    assert repo.get("TEN-99999999") is None


# ── Relations ──────────────────────────────────────────────────────────────────

def test_put_relation(repo):
    company, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
    tender, _ = repo.put_entity(BizEntityKind.TENDER, "Park Renovation")
    rel, created = repo.put_relation(
        source_uid=company.uid,
        kind=BizRelationKind.SUBMITTED_BID,
        target_uid=tender.uid,
        source="test",
    )
    assert created is True
    assert rel.source_uid == company.uid
    assert rel.target_uid == tender.uid
    assert rel.kind == BizRelationKind.SUBMITTED_BID


def test_relation_dedup(repo):
    c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    t, _ = repo.put_entity(BizEntityKind.TENDER, "Tender A")
    _, c1 = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
    _, c2 = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
    assert c1 is True
    assert c2 is False


def test_get_neighbors_out(repo):
    company, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
    repo.put_relation(company.uid, BizRelationKind.IN_CITY, city.uid)
    neighbors = repo.get_neighbors(company.uid, direction="out")
    assert len(neighbors) == 1
    rel, nb = neighbors[0]
    assert rel.kind == BizRelationKind.IN_CITY
    assert nb.uid == city.uid


def test_get_neighbors_in(repo):
    company, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
    repo.put_relation(company.uid, BizRelationKind.IN_CITY, city.uid)
    neighbors = repo.get_neighbors(city.uid, direction="in")
    assert len(neighbors) == 1
    _, nb = neighbors[0]
    assert nb.uid == company.uid


def test_get_neighbors_kind_filter(repo):
    c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
    prov, _ = repo.put_entity(BizEntityKind.PROVINCE, "BC")
    repo.put_relation(c.uid, BizRelationKind.IN_CITY, city.uid)
    repo.put_relation(c.uid, BizRelationKind.IN_PROVINCE, prov.uid)
    city_only = repo.get_neighbors(c.uid, direction="out", kinds=[BizRelationKind.IN_CITY])
    assert len(city_only) == 1
    assert city_only[0][1].uid == city.uid


# ── History ───────────────────────────────────────────────────────────────────

def test_entity_history_written(repo):
    e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=True)
    history = repo.entity_history(e.uid)
    assert len(history) == 1
    assert history[0]["snapshot"]["uid"] == e.uid


def test_entity_history_appends_on_update(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=True)
    repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"city": "Victoria"}, write_history=True)
    e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=False)
    history = repo.entity_history(e.uid)
    assert len(history) == 2


# ── FTS ───────────────────────────────────────────────────────────────────────

def test_fts_search(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
    repo.put_entity(BizEntityKind.COMPANY, "Atlantic Dredging")
    repo.put_entity(BizEntityKind.TENDER, "Pacific Highway Expansion")
    repo.rebuild_fts()
    results = repo.search_fts("Pacific")
    names = [r.name for r in results]
    assert any("Pacific" in n for n in names)


def test_fts_empty_query(repo):
    assert repo.search_fts("") == []


# ── Stats ─────────────────────────────────────────────────────────────────────

def test_stats(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Acme")
    repo.put_entity(BizEntityKind.TENDER, "Tender A")
    c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
    t, _ = repo.put_entity(BizEntityKind.TENDER, "Tender A")
    repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
    stats = repo.get_stats()
    assert stats["entities"] == 2
    assert stats["relations"] == 1
    assert stats["by_kind"]["company"] == 1
    assert stats["by_kind"]["tender"] == 1


def test_find_entities_by_kind(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
    repo.put_entity(BizEntityKind.COMPANY, "BuildCo")
    repo.put_entity(BizEntityKind.TENDER, "Tender 1")
    companies = repo.find(kind=BizEntityKind.COMPANY)
    assert len(companies) == 2
    tenders = repo.find(kind=BizEntityKind.TENDER)
    assert len(tenders) == 1


def test_find_entities_name_like(repo):
    repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
    repo.put_entity(BizEntityKind.COMPANY, "Atlantic Dredging")
    hits = repo.find(name_like="Pacific")
    assert len(hits) == 1
    assert hits[0].name == "Pacific Rim Construction"
