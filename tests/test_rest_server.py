"""Tests for the REST API transport layer."""
from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.testclient import TestClient

from tenderscope_kg.repository import open_repository
from tenderscope_kg.rest_server import create_rest_app
from tenderscope_kg.server_engines import build_engines


@pytest.fixture
def client() -> TestClient:
    """Test client with the same mount prefixes used in production."""
    repo = open_repository()
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
    assert "business_graph" in body or "error" in body


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
