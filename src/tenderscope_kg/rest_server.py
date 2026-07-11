"""
TenderScope Graph REST API.

Transport layer only.  Every handler:
  1. Extracts parameters from the HTTP request.
  2. Calls one method on the shared EngineSet.
  3. Returns the result as a JSON response.

No business logic lives here.  All query, scoring, and graph-traversal
logic lives in the engine layer (BizQueryEngine, CompanyIntelligenceEngine,
etc.) accessed through EngineSet.

Versioning
----------
The app is mounted at two prefixes by the MCP/SSE server:

  - ``/api/graph``    legacy prefix (deprecated, kept for backward compatibility)
  - ``/api/v1/graph`` current stable prefix

Both mounts serve identical endpoints.  Requests through the legacy prefix
receive ``Deprecation`` and ``Sunset`` headers to alert callers to migrate.

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
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

if TYPE_CHECKING:
    from .server_engines import EngineSet


class LegacyDeprecationMiddleware(BaseHTTPMiddleware):
    """
    Mark requests routed through the legacy ``/api/graph`` mount as deprecated.

    The current stable mount is ``/api/v1/graph``.  Both mounts serve the same
    endpoints, so this middleware only adds headers and does not alter
    response bodies or status codes.
    """

    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        # root_path is set by Starlette's Mount() to the mount prefix.
        root_path = request.scope.get("root_path", "")
        if root_path == "/api/graph":
            response.headers["Deprecation"] = "true"
            # Tentative sunset date; update when the legacy prefix is removed.
            response.headers["Sunset"] = "Sun, 31 Dec 2026 23:59:59 GMT"
        return response


def create_rest_app(engines: "EngineSet") -> FastAPI:
    """
    Build and return the versioned FastAPI application.

    Accepts the shared EngineSet constructed at process startup.
    Must not call build_engines() itself.
    """
    app = FastAPI(
        title="TenderScope Graph API",
        description=(
            "REST transport over the TenderScope business knowledge graph. "
            "Stable prefix: /api/v1/graph. Legacy prefix: /api/graph (deprecated)."
        ),
        version="1.0.0",
        docs_url="/docs",
        redoc_url=None,
    )
    app.add_middleware(LegacyDeprecationMiddleware)

    # ── Health ────────────────────────────────────────────────────────────────

    @app.get("/health", tags=["meta"])
    def graph_health() -> dict:
        return engines.biz.graph_statistics()

    # ── Company list ──────────────────────────────────────────────────────────

    @app.get("/companies", tags=["companies"])
    def list_companies(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
    ) -> dict:
        return engines.biz.list_by_kind("company", limit=limit, offset=offset)

    # ── Company search (alias-resolving) ─────────────────────────────────────

    @app.get("/companies/search", tags=["companies"])
    def search_companies(
        q: str = Query(..., min_length=1, max_length=300),
        limit: int = Query(20, ge=1, le=200),
    ) -> dict:
        return engines.biz.find_companies(q, limit=limit)

    # ── Single company by UID or scraper ID ───────────────────────────────────

    @app.get("/companies/{company_id}", tags=["companies"])
    def get_company(company_id: str) -> dict:
        result = engines.biz.company_by_id(company_id)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    # ── Company identity (aliases + external IDs) ─────────────────────────────

    @app.get("/companies/{uid}/identity", tags=["companies"])
    def get_company_identity(uid: str) -> dict:
        result = engines.biz.company_identity(uid)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    # ── Company intelligence profile ──────────────────────────────────────────

    @app.get("/companies/{uid}/profile", tags=["companies"])
    def get_company_profile(uid: str) -> dict:
        result = engines.cie.company_profile(uid)
        if "error" in result:
            raise HTTPException(status_code=404, detail=result["error"])
        return result

    return app
