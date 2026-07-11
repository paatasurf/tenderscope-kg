"""
Tests for the importer framework: CSVImporter, JSONImporter, TenderScopeImporter.
"""

from __future__ import annotations

import csv
import json
import sqlite3

import pytest

from tenderscope_kg.domain import BizEntityKind
from tenderscope_kg.importers import CSVImporter, JSONImporter, TenderScopeImporter
from tenderscope_kg.repository._sqlite import BizRepositorySQLite


@pytest.fixture
def repo():
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    yield repo
    conn.close()


# ── CSVImporter ───────────────────────────────────────────────────────────────


def test_csv_import_basic(repo, tmp_path):
    p = tmp_path / "companies.csv"
    p.write_text("company_name,city,phone\nAcme Corp,Vancouver,604-555-0100\nBuildCo,Victoria,\n")
    schema = {
        "entity_kind": "company",
        "name_column": "company_name",
        "attribute_columns": ["city", "phone"],
    }
    imp = CSVImporter(repo, str(p), schema=schema, source_tag="test")
    result = imp.run()
    assert result.entities_created == 2
    assert result.errors == []


def test_csv_import_idempotent(repo, tmp_path):
    p = tmp_path / "companies.csv"
    p.write_text("company_name\nAcme Corp\nBuildCo\n")
    schema = {"entity_kind": "company", "name_column": "company_name"}
    CSVImporter(repo, str(p), schema=schema).run()
    result = CSVImporter(repo, str(p), schema=schema).run()
    assert result.entities_created == 0
    assert result.entities_updated == 2
    stats = repo.get_stats()
    assert stats["entities"] == 2  # no duplicates


def test_csv_import_with_relations(repo, tmp_path):
    p = tmp_path / "companies.csv"
    p.write_text("company_name,parent\nSubCo,Acme Corp\n")
    schema = {
        "entity_kind": "company",
        "name_column": "company_name",
        "relation_columns": [{"column": "parent", "relation_kind": "subsidiary_of", "target_kind": "company"}],
    }
    result = CSVImporter(repo, str(p), schema=schema).run()
    assert result.entities_created == 2  # SubCo + Acme Corp
    assert result.relations_created == 1


def test_csv_import_missing_file(repo):
    schema = {"entity_kind": "company", "name_column": "name"}
    result = CSVImporter(repo, "/nonexistent/file.csv", schema=schema).run()
    assert len(result.errors) > 0


def test_csv_import_missing_schema_keys(repo, tmp_path):
    p = tmp_path / "f.csv"
    p.write_text("name\nAcme\n")
    result = CSVImporter(repo, str(p), schema={}).run()
    assert len(result.errors) > 0


# ── JSONImporter ──────────────────────────────────────────────────────────────


def test_json_import_array(repo, tmp_path):
    data = [
        {"kind": "company", "name": "Acme Corp", "city": "Vancouver"},
        {"kind": "tender", "name": "Park Renovation 2025"},
    ]
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    result = JSONImporter(repo, str(p)).run()
    assert result.entities_created == 2
    assert result.errors == []


def test_json_import_envelope_with_relations(repo, tmp_path):
    data = {
        "entities": [
            {"kind": "company", "name": "Acme Corp"},
            {"kind": "tender", "name": "Park Renovation"},
        ],
        "relations": [
            {
                "source_kind": "company",
                "source_name": "Acme Corp",
                "target_kind": "tender",
                "target_name": "Park Renovation",
                "kind": "submitted_bid",
            }
        ],
    }
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    result = JSONImporter(repo, str(p)).run()
    assert result.entities_created == 2
    assert result.relations_created == 1


def test_json_import_idempotent(repo, tmp_path):
    data = [{"kind": "company", "name": "Acme"}]
    p = tmp_path / "data.json"
    p.write_text(json.dumps(data))
    JSONImporter(repo, str(p)).run()
    result = JSONImporter(repo, str(p)).run()
    assert result.entities_created == 0
    assert result.entities_updated == 1
    assert repo.get_stats()["entities"] == 1


def test_json_import_missing_file(repo):
    result = JSONImporter(repo, "/nonexistent/file.json").run()
    assert len(result.errors) > 0


# ── TenderScopeImporter ───────────────────────────────────────────────────────


def test_tenderscope_import_tenders(repo, tmp_path):
    p = tmp_path / "tenders.csv"
    rows = [
        {"tender_title": "Bridge Repair", "region": "Metro Vancouver", "closing_at": "2025-12-01"},
        {"tender_title": "Road Paving", "region": "Fraser Valley", "closing_at": "2025-11-15"},
    ]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    result = TenderScopeImporter(repo, str(p), source_tag="test").run()
    assert result.entities_created == 2
    assert result.errors == []
    tenders = repo.find(kind=BizEntityKind.TENDER)
    assert len(tenders) == 2


def test_tenderscope_import_companies(repo, tmp_path):
    p = tmp_path / "companies.csv"
    rows = [
        {"company_name": "Acme Corp", "city": "Vancouver", "province": "BC"},
        {"company_name": "BuildCo", "city": "Victoria", "province": "BC"},
    ]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    result = TenderScopeImporter(repo, str(p), source_tag="test").run()
    # 2 companies + 2 cities (Vancouver, Victoria) + 1 province (BC)
    assert result.entities_created >= 2
    assert result.relations_created >= 2  # in_city relations


def test_tenderscope_import_awards(repo, tmp_path):
    p = tmp_path / "awards.csv"
    rows = [
        {"vendor_name": "Acme Corp", "tender_title": "Bridge Repair", "contract_value": "250000"},
        {"vendor_name": "BuildCo", "tender_title": "Road Paving", "contract_value": "180000"},
    ]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    result = TenderScopeImporter(repo, str(p), source_tag="test").run()
    assert result.entities_created >= 4  # 2 companies + 2 tenders
    assert result.relations_created >= 4  # awarded_to + awarded_by for each


def test_tenderscope_import_permits(repo, tmp_path):
    p = tmp_path / "permits.csv"
    rows = [
        {"permit_number": "BP-2025-001", "address": "123 Main St", "applicant": "Acme Corp"},
    ]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    result = TenderScopeImporter(repo, str(p), source_tag="test").run()
    assert result.entities_created >= 1
    permits = repo.find(kind=BizEntityKind.PERMIT)
    assert len(permits) == 1


def test_tenderscope_import_limit(repo, tmp_path):
    p = tmp_path / "tenders.csv"
    rows = [{"tender_title": f"Tender {i}"} for i in range(10)]
    with open(p, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["tender_title"])
        w.writeheader()
        w.writerows(rows)
    result = TenderScopeImporter(repo, str(p), limit=3).run()
    assert result.entities_created == 3
