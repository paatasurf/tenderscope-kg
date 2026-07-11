"""Tests for the REST API transport layer."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from tenderscope_kg.domain import BizEntityKind
from tenderscope_kg.repository import open_repository
from tenderscope_kg.rest_server import create_rest_app
from tenderscope_kg.server_engines import build_engines


@pytest.fixture
def client() -> TestClient:
    """Test client with the same mount prefixes used in production."""
    repo = open_repository()
    # Seed a canonical company so identity/profile contract tests have a real record.
    repo.put_entity(
        kind=BizEntityKind.COMPANY,
        name="TenderScope Inc.",
        attributes={"city": "Vancouver"},
    )
    engines = build_engines(repo)
    rest_app = create_rest_app(engines)
    app = Starlette(
        routes=[
            Mount("/api/v1/graph", app=rest_app),
            Mount("/api/graph", app=rest_app),
        ]
    )
    return TestClient(app)


def test_v1_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/v1/graph/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"


def test_v1_ready_returns_200(client: TestClient) -> None:
    response = client.get("/api/v1/graph/ready")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "engines" in body
    assert "repository" in body


def test_legacy_health_returns_200(client: TestClient) -> None:
    response = client.get("/api/graph/health")
    assert response.status_code == 200


def test_legacy_health_includes_deprecation_header(client: TestClient) -> None:
    response = client.get("/api/graph/health")
    assert response.headers["Deprecation"] == "true"
    assert "Sunset" in response.headers


def test_v1_health_excludes_deprecation_header(client: TestClient) -> None:
    response = client.get("/api/v1/graph/health")
    assert "Deprecation" not in response.headers
    assert "Sunset" not in response.headers


def test_v1_companies_pagination(client: TestClient) -> None:
    response = client.get("/api/v1/graph/companies?limit=5&offset=0")
    assert response.status_code == 200
    body = response.json()
    assert "items" in body or "results" in body or "count" in body


def test_legacy_companies_pagination(client: TestClient) -> None:
    response = client.get("/api/graph/companies?limit=5&offset=0")
    assert response.status_code == 200
    assert response.headers["Deprecation"] == "true"


def test_v1_and_legacy_companies_match(client: TestClient) -> None:
    v1 = client.get("/api/v1/graph/companies?limit=5&offset=0").json()
    legacy = client.get("/api/graph/companies?limit=5&offset=0").json()
    assert v1 == legacy


def test_v1_unknown_company_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/graph/companies/CMP-00000000")
    assert response.status_code == 404


def _first_company_uid(client: TestClient) -> str:
    body = client.get("/api/v1/graph/companies?limit=1&offset=0").json()
    results = body.get("results") or body.get("items", [])
    assert len(results) > 0
    return results[0]["uid"]


def test_v1_company_identity_contract(client: TestClient) -> None:
    uid = _first_company_uid(client)
    response = client.get(f"/api/v1/graph/companies/{uid}/identity")
    assert response.status_code == 200
    body = response.json()
    expected = {
        "company_uid",
        "display_name",
        "canonical_name",
        "aliases",
        "external_ids",
        "attributes",
        "merge_candidates",
    }
    assert expected.issubset(body.keys())
    assert body["company_uid"] == uid
    assert isinstance(body["aliases"], list)
    assert isinstance(body["external_ids"], dict)


def test_legacy_company_identity_matches_v1(client: TestClient) -> None:
    uid = _first_company_uid(client)
    v1 = client.get(f"/api/v1/graph/companies/{uid}/identity").json()
    legacy = client.get(f"/api/graph/companies/{uid}/identity").json()
    assert v1 == legacy
    assert legacy["company_uid"] == uid
