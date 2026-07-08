"""
TenderScope Graph REST API.

Transport layer only.  Every handler:
  1. Extracts parameters from the HTTP request.
  2. Calls one method on the shared EngineSet.
  3. Returns the result as a JSON response.

No business logic lives here.  All query, scoring, and graph-traversal
logic lives in the engine layer (BizQueryEngine, CompanyIntelligenceEngine,
etc.) accessed through EngineSet.

Mounted at /api/graph/ to avoid namespace collision with bc-tender-scraper's
own /api/ routes when that service proxies through.

ID compatibility
----------------
During the migration period both identifier formats are accepted for
company lookups:

  - Graph UID   e.g. CMP-00000001   (permanent, graph-native)
  - Scraper ID  e.g. 1247           (integer, bc-tender-scraper legacy)

The resolution logic for scraper IDs lives in BizQueryEngine.company_by_scraper_id()
so this file contains no lookup branching itself.
"""
from __future__ import annotations

from typing import TYPE_CHECKING

from fastapi import FastAPI, HTTPException, Query

if TYPE_CHECKING:
    from .server_engines import EngineSet


def create_rest_app(engines: "EngineSet") -> FastAPI:
    """
    Build and return the FastAPI application.

    Accepts the shared EngineSet constructed at process startup.
    Must not call build_engines() itself.
    """
    app = FastAPI(
        title="TenderScope Graph API",
        description="REST transport over the TenderScope business knowledge graph.",
        version="1.0.0",
        docs_url="/api/graph/docs",
        redoc_url=None,
    )

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/api/graph/health", tags=["meta"])
    def graph_health() -> dict:
        return engines.biz.graph_statistics()

    # ── Company list ──────────────────────────────────────────────────────────

    @app.get("/api/graph/companies", tags=["companies"])
    def list_companies(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return engines.biz.list_by_kind("company", limit=limit, offset=offset)

    # ── Company search (alias-resolving) ─────────────────────────────────────

    @app.get("/api/graph/companies/search", tags=["companies"])
    def search_companies(
        q: str = Query(..., min_length=1, max_length=300),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict:
        return engines.biz.find_companies(q, limit=limit)

    # ── Single company by UID or scraper ID ───────────────────────────────────

    @app.get("/api/graph/companies/{company_id}", tags=["companies"])
    def get_company(company_id: str) -> dict:
        result = engines.biz.company_by_id(company_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    # ── Company identity (aliases + external IDs) ─────────────────────────────

    @app.get("/api/graph/companies/{uid}/identity", tags=["companies"])
    def get_company_identity(uid: str) -> dict:
        result = engines.biz.company_identity(uid)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    # ── Company intelligence profile ──────────────────────────────────────────

    @app.get("/api/graph/companies/{uid}/profile", tags=["companies"])
    def get_company_profile(uid: str) -> dict:
        result = engines.cie.company_profile(uid)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    return app
