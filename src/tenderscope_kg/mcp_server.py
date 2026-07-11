"""
MCP server exposing Knowledge Graph tools to AI coding agents.
Run with:  tkg-mcp --repo /path/to/repo
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import click
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import (
    CallToolRequest,
    CallToolResult,
    ListToolsRequest,
    ListToolsResult,
    TextContent,
    Tool,
)

from .db import GraphDB
from .importers import CSVImporter, JSONImporter, TenderScopeImporter
from .indexer import Indexer
from .query_engine import QueryEngine
from .repository import open_repository
from .server_engines import EngineSet, build_engines

_TOOLS: list[Tool] = [
    Tool(
        name="kg_search",
        description=(
            "Full-text + name search over all indexed entities (functions, classes, "
            "SQL tables, API routes, config keys, etc). Returns compact summaries. "
            "Use this as the first step to locate relevant code before deeper queries."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name, keyword, or phrase to search"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional filter: file, module, class, function, method, "
                    "sql_table, api_route, config_key, interface, type_alias",
                },
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="kg_entity_detail",
        description=(
            "Get full detail for a specific entity by qualified name: signature, docstring, "
            "immediate callers, callees, and all graph neighbours. "
            "Use after kg_search to drill into a specific entity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {
                    "type": "string",
                    "description": "Dotted qualified name, e.g. myapp.api.users.get_user",
                },
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="kg_file_outline",
        description=(
            "List all entities defined in a file (classes, functions, routes, etc.) "
            "ordered by line number. Accepts a partial path like 'users.py'."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Full or partial repo-relative file path",
                },
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="kg_callers",
        description="Find all functions/methods that call a given function. Supports multi-hop.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "depth": {"type": "integer", "default": 1, "description": "Traversal depth (1-3)"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="kg_callees",
        description="Find all functions/methods called by a given function. Supports multi-hop.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "depth": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="kg_inheritance",
        description="Get the full class hierarchy (ancestors and descendants) for a class.",
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string", "description": "Class qualified name"},
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="kg_imports",
        description="Show all imports for a given file. Useful for understanding dependencies.",
        inputSchema={
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
            },
            "required": ["file_path"],
        },
    ),
    Tool(
        name="kg_api_routes",
        description="List all HTTP API routes discovered in the codebase (Express, Hono, Fastify, etc.).",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="kg_sql_tables",
        description="List all SQL tables and their columns found in the codebase.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="kg_table_usage",
        description="Find all files and functions that read from or write to a SQL table.",
        inputSchema={
            "type": "object",
            "properties": {
                "table_name": {"type": "string"},
            },
            "required": ["table_name"],
        },
    ),
    Tool(
        name="kg_subgraph",
        description=(
            "Return a subgraph (entities + relations) within N hops of an entity. "
            "Useful for understanding the local context around a component."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "qualified_name": {"type": "string"},
                "depth": {"type": "integer", "default": 2, "description": "Hop depth (1-3)"},
            },
            "required": ["qualified_name"],
        },
    ),
    Tool(
        name="kg_context_pack",
        description=(
            "Build a token-budgeted context pack for a task description. "
            "Searches the graph, ranks relevant entities by relevance, and returns "
            "a compact text representation that fits within the token budget. "
            "This is the primary tool for task-oriented context retrieval."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "Natural language description of the task you are about to perform",
                },
                "token_budget": {
                    "type": "integer",
                    "default": 4000,
                    "description": "Maximum tokens for the returned context",
                },
                "seed_names": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional list of qualified names to anchor the search",
                },
            },
            "required": ["task"],
        },
    ),
    Tool(
        name="kg_stats",
        description="Return index statistics: entity counts, relation counts, languages, last updated.",
        inputSchema={
            "type": "object",
            "properties": {},
        },
    ),
    Tool(
        name="kg_reindex",
        description=(
            "Trigger a re-index of the repository. Use after adding/modifying files. "
            "Incremental by default (only re-parses changed files)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "full": {
                    "type": "boolean",
                    "default": False,
                    "description": "Force full re-index (ignore incremental cache)",
                },
            },
        },
    ),
    # ── Business Graph tools ──────────────────────────────────────────────
    Tool(
        name="biz_search",
        description=(
            "Search business entities (companies, tenders, permits, contracts, persons, …) "
            "by name or keyword.  Returns stable UIDs and summaries.  "
            "Use this as the entry point to explore the business knowledge graph."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Name or keyword to search"},
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional kind filter: company, tender, person, address, permit, "
                        "contract, license, project, organization, document, city, province, "
                        "industry, naics, equipment, phone, email, website"
                    ),
                },
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="biz_entity",
        description=(
            "Get full detail for a business entity by UID (e.g. CMP-00000001).  "
            "Returns all attributes and all connected entities.  "
            "For a company, prefer cie_profile which includes evidence, stats, buyers, "
            "competitors, contracts, timeline, locations, and industries.  "
            "Use biz_entity for non-company entities (tenders, permits, addresses, etc)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Permanent entity UID, e.g. CMP-00000001"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="biz_neighbors",
        description=(
            "Return all entities directly connected to a given entity via typed relations.  "
            "Supports direction filtering (out/in/both) and relation-kind filtering."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "direction": {
                    "type": "string",
                    "enum": ["out", "in", "both"],
                    "default": "both",
                },
                "kinds": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional relation kind filter (e.g. awarded_to, employs, …)",
                },
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="biz_find_path",
        description=(
            "Find a path between two business entities in the knowledge graph using BFS.  "
            "Returns the chain of entities and relation types connecting them.  "
            "For an explained, evidence-backed path with strength scoring between two "
            "companies, prefer rie_path or rie_explain instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid1": {"type": "string", "description": "Source entity UID"},
                "uid2": {"type": "string", "description": "Target entity UID"},
                "max_depth": {"type": "integer", "default": 6, "description": "Maximum hop depth"},
            },
            "required": ["uid1", "uid2"],
        },
    ),
    Tool(
        name="biz_related_companies",
        description=(
            "Find companies related to a given company via up to 2-hop relation chains.  "
            "Returns basic entity summaries only.  "
            "For competitive intelligence, prefer cei_direct_competitors (ranked, evidence-backed) "
            "or cie_competitors (shared-buyer evidence).  "
            "For partner/subcontractor discovery, prefer rie_partnerships or rie_infer."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="biz_contracts",
        description=(
            "Return all contracts, tenders, and bids associated with a company.  "
            "Returns raw relation tuples only.  "
            "For richer contract data with values, dates, and totals, use cie_contracts.  "
            "For the full scored opportunity pipeline, use ede_opportunity_pipeline."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="biz_import",
        description=(
            "Import business entities from a file into the knowledge graph.  "
            "Supports CSV (with schema), JSON (array or envelope), and TenderScope native files.  "
            "The importer is auto-detected from file extension and content."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or repo-relative path to the file",
                },
                "format": {
                    "type": "string",
                    "enum": ["auto", "csv", "json", "tenderscope"],
                    "default": "auto",
                    "description": "'auto' detects format from file extension",
                },
                "csv_schema": {
                    "type": "object",
                    "description": "Required for CSV format: {entity_kind, name_column, attribute_columns:[]}",
                },
                "source_tag": {
                    "type": "string",
                    "default": "mcp",
                    "description": "Provenance tag attached to all imported entities",
                },
            },
            "required": ["path"],
        },
    ),
    Tool(
        name="biz_stats",
        description="Return business graph statistics: entity counts by kind, relation count, sequence positions.",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="biz_entity_history",
        description=(
            "Return the full audit trail (history of changes) for a business entity by UID.  "
            "Each entry contains a full snapshot of the entity at the time of the change."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="biz_list",
        description="Paginated list of all business entities of a given kind.",
        inputSchema={
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "description": "Entity kind: company, tender, permit, contract, person, …",
                },
                "limit": {"type": "integer", "default": 50},
                "offset": {"type": "integer", "default": 0},
            },
            "required": ["kind"],
        },
    ),
    # ── Company Intelligence Engine tools ─────────────────────────────────
    Tool(
        name="cie_profile",
        description=(
            "Complete explainable company profile: stats, buyers, competitors, contracts, "
            "tenders, timeline, locations, and industries — all computed from the live graph. "
            "Every metric includes evidence references and graph paths."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_summary",
        description=(
            "Lightweight company overview: tenders won/submitted, total awarded value, "
            "unique buyers, locations, industries, confidence score, and evidence count."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_stats",
        description=(
            "Financial + activity statistics for a company: total contract value, "
            "average/largest/smallest contract, permit counts, yearly breakdown, "
            "and full evidence references."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_buyers",
        description=(
            "Return all government/private buyers associated with a company, "
            "each with the list of tenders that link them and evidence paths."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_competitors",
        description=(
            "Discover competitor companies via shared buyers and shared tenders. "
            "Returns ranked list with shared evidence count and graph paths."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_contracts",
        description=(
            "All awarded contracts for a company with values, dates, URLs, "
            "sorted by contract value descending.  Includes total and average."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_tenders",
        description=(
            "All tenders a company participated in, split into won and submitted. "
            "Each entry includes category, dates, contract value, and evidence path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_timeline",
        description=(
            "Chronological activity timeline for a company derived from graph edges. "
            "Includes contract awards, permit applications, and yearly summary."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_locations",
        description=(
            "All cities, provinces, and addresses associated with a company, each with evidence path to the graph edge."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_industries",
        description=(
            "Industry and category associations for a company: direct graph relations "
            "plus categories inferred from won tenders."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID (CMP-…)"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cie_top_competitors",
        description=(
            "Rank all companies by number of distinct buyers — a proxy for market breadth. "
            "Returns top N companies sorted by buyer count."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    ),
    Tool(
        name="cie_companies_by_city",
        description="Return all companies located in a given city.",
        inputSchema={
            "type": "object",
            "properties": {
                "city": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["city"],
        },
    ),
    Tool(
        name="cie_companies_by_province",
        description="Return all companies located in a given province.",
        inputSchema={
            "type": "object",
            "properties": {
                "province": {"type": "string"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["province"],
        },
    ),
    Tool(
        name="cie_most_connected",
        description=(
            "Rank companies by total edge count (in + out). "
            "Highly connected companies have the strongest evidence bases."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "default": 20},
            },
        },
    ),
    # ── Relationship Intelligence Engine tools ─────────────────────────────
    Tool(
        name="rie_explain",
        description=(
            "Explain WHY two business entities are connected. "
            "Returns direct relations, shortest path, shared buyers, shared competitors, "
            "shared industries, shared locations, co-appearances, relationship strength, "
            "confidence, and a natural-language explanation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid_a": {"type": "string", "description": "First entity UID"},
                "uid_b": {"type": "string", "description": "Second entity UID"},
            },
            "required": ["uid_a", "uid_b"],
        },
    ),
    Tool(
        name="rie_strength",
        description=(
            "Weighted relationship strength (0–1) between two entities with "
            "per-signal breakdown: direct edges, shared buyers, shared competitors, "
            "shared industries, shared locations, co-appearances."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid_a": {"type": "string"},
                "uid_b": {"type": "string"},
            },
            "required": ["uid_a", "uid_b"],
        },
    ),
    Tool(
        name="rie_path",
        description=(
            "BFS shortest path between any two business entities. "
            "Each hop includes the relation kind and relation weight. "
            "Use max_depth to control search depth (default 8)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid_a": {"type": "string"},
                "uid_b": {"type": "string"},
                "max_depth": {"type": "integer", "default": 8},
            },
            "required": ["uid_a", "uid_b"],
        },
    ),
    Tool(
        name="rie_infer",
        description=(
            "Infer all indirect relationships for a single entity: "
            "shared-buyer links, subcontractor hints, recurring partnerships, "
            "industry-cluster peers, geographic-cluster peers. "
            "Each inference includes strength, confidence, and evidence_path."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Entity UID"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="rie_partnerships",
        description=(
            "Detect recurring business partnerships for a company: "
            "other companies that co-appear in ≥ N tenders/events. "
            "Indicates stable subcontractor or joint-venture relationships."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "min_count": {"type": "integer", "default": 2},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="rie_industry_cluster",
        description=(
            "All companies in an industry cluster (same industry node), "
            "ranked by tender count. Pass an industry UID or name."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "industry": {"type": "string", "description": "Industry UID or name"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["industry"],
        },
    ),
    Tool(
        name="rie_geo_cluster",
        description=("All companies in a geographic cluster (city or province). Pass a city/province UID or name."),
        inputSchema={
            "type": "object",
            "properties": {
                "location": {"type": "string", "description": "City/province UID or name"},
                "limit": {"type": "integer", "default": 200},
            },
            "required": ["location"],
        },
    ),
    Tool(
        name="rie_org_influence",
        description=(
            "Measure a buyer organisation's influence on the company network. "
            "Returns tender count, company count, total contract value, "
            "influence score (0–1), and all commissioned companies."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Organisation UID (ORG-…)"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["uid"],
        },
    ),
    # ── Competitive Intelligence Engine tools ───────────────────────────────
    Tool(
        name="cei_competitor_profile",
        description=(
            "Full competitive profile for a company: win rate, growth trend, "
            "direct competitors, emerging competitors, co-bidders, common losers, "
            "and composite competitive pressure score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Company UID"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_win_rate",
        description=(
            "Win rate, loss rate, bid frequency, and participation counts "
            "for a company.  Evidence includes every awarded tender."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_growth_trend",
        description=(
            "Year-over-year tender activity trend for a company: "
            "wins and bids per year, trend label (growing/stable/declining)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_direct_competitors",
        description=(
            "Companies competing directly on the same tenders or sharing "
            "the same buyer organisations, ranked by competition score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_emerging_competitors",
        description=(
            "Competitors with rising activity in the same markets over "
            "the last N years.  Distinguishes new entrants from growers."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "lookback_years": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_co_bidders",
        description=(
            "Companies that frequently bid alongside this company on the same tenders, ranked by co-bid count."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "min_count": {"type": "integer", "default": 1},
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="cei_buyer_preferences",
        description=(
            "Which companies a buyer organisation consistently chooses: "
            "award count, win rate, and evidence per preferred supplier."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "buyer_uid": {"type": "string", "description": "Buyer ORG or CMP UID"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["buyer_uid"],
        },
    ),
    Tool(
        name="cei_market_concentration",
        description=(
            "Herfindahl\u2013Hirschman Index (HHI) and dominant-supplier analysis "
            "for a market defined by a buyer org, industry, city, or province UID."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scope_uid": {"type": "string", "description": "Buyer ORG, IND, CTY, or PRV UID"},
            },
            "required": ["scope_uid"],
        },
    ),
    Tool(
        name="cei_market_share",
        description=(
            "Percentage market share breakdown for a market scope. "
            "Slice by: company, year, buyer, city, province, or industry."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scope_uid": {"type": "string"},
                "by": {
                    "type": "string",
                    "enum": ["company", "year", "buyer", "city", "province", "industry"],
                    "default": "company",
                },
                "limit": {"type": "integer", "default": 50},
            },
            "required": ["scope_uid"],
        },
    ),
    Tool(
        name="cei_competitor_rankings",
        description=(
            "Ranked company list for a market scope (buyer org, industry, "
            "city, province). Sort by: wins, bids, win_rate, or market_share."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "scope_uid": {"type": "string"},
                "by": {
                    "type": "string",
                    "enum": ["wins", "bids", "win_rate", "market_share"],
                    "default": "wins",
                },
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["scope_uid"],
        },
    ),
    Tool(
        name="cei_competitive_pressure",
        description=(
            "Composite competitive pressure score (0\u20131) for a company: "
            "competitor density, win pressure, co-bidder intensity, "
            "and buyer market concentration (HHI)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    # ── Opportunity Intelligence Engine tools ─────────────────────────
    Tool(
        name="oie_opportunity_profile",
        description=(
            "Full opportunity profile for a (company, tender) pair: score, "
            "recommendation, timeline, risk, portfolio impact, and similar "
            "historical opportunities — all assembled in one response."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_opportunity_score",
        description=(
            "Calculate a 0–100 Opportunity Score with full dimension breakdown: "
            "capability fit, buyer history, industry history, value fit, "
            "geographic fit, competition level, buyer attractiveness, "
            "strategic importance, workload impact, win probability."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_opportunity_recommendation",
        description=(
            "Return a recommendation label (Strong Pursue / Pursue / "
            "Strategic Investment / Monitor / Ignore) with why-pursue, "
            "why-ignore, and immediate next-action steps."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_opportunity_explain",
        description=(
            "Full explainability report: score breakdown, graph evidence, "
            "assumptions, weak evidence, missing information, "
            "step-by-step reasoning chain, and recommendation."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_opportunity_timeline",
        description=(
            "Estimate preparation effort, submission urgency, deadline risk, "
            "comparable historical wins, and comparable losses."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_opportunity_risk",
        description=(
            "Risk analysis: competition, capability gaps, workload, buyer "
            "reliability, value exposure, geographic risk — each with "
            "severity and mitigation hints."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_portfolio_impact",
        description=(
            "Estimate expected revenue contribution, diversification impact, "
            "strategic value, client expansion value, and future relationship "
            "potential for a tender opportunity."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_similar_opportunities",
        description=(
            "Find historical tenders similar to the target by buyer, industry, "
            "and value bucket — showing outcomes (win/loss) and similarity score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "tender_uid": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["company_uid", "tender_uid"],
        },
    ),
    Tool(
        name="oie_best_opportunities",
        description=(
            "Score ALL tenders in the graph for a given company and return the top-N ranked by Opportunity Score."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="oie_executive_summary",
        description=(
            "CEO-dashboard summary of the OPPORTUNITY PIPELINE for a company: "
            "top-N scored tenders to pursue, biggest opportunity-level risks, "
            "why pursue, why ignore, immediate bid-related next actions, "
            "opportunity cost, and overall confidence level.  "
            "Scope is opportunity scoring only (OIE layer).  "
            "For a full strategic decision package combining all five intelligence "
            "engines (situation, market, relationships, buyers, priorities, risks), "
            "use ede_executive_decision instead."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "limit": {"type": "integer", "default": 5},
            },
            "required": ["company_uid"],
        },
    ),
    # ── Buyer Intelligence Engine tools ────────────────────────────────
    Tool(
        name="bie_buyer_profile",
        description=(
            "Full buyer profile for a procurement organisation: all sub-queries "
            "assembled — suppliers, patterns, seasonality, concentration, forecast."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string", "description": "Buyer ORG or CMP UID"},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_buyer_summary",
        description="Lightweight buyer summary: total tenders, active suppliers, award HHI, top supplier.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_supplier_roster",
        description="All suppliers that ever won a tender from this buyer, with award counts and win rates.",
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_preferred_suppliers",
        description="Suppliers consistently chosen by this buyer (awarded ≥ min_awards times).",
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "min_awards": {"type": "integer", "default": 2},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_supplier_loyalty",
        description=(
            "Loyalty index per supplier (award_count / total_tenders). Overall loyalty score = sqrt(HHI of awards)."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "limit": {"type": "integer", "default": 30},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_supplier_diversity",
        description="Supplier diversity score (1 − HHI). Higher = more diverse supplier base.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_buying_patterns",
        description=(
            "Temporal and structural buying patterns: cadence, avg value, avg bidder count, peak month, busiest year."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_procurement_seasonality",
        description="Monthly and quarterly tender distribution with seasonality index per month.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_preferred_industries",
        description="Industries this buyer procures from most, derived from winning companies’ IN_INDUSTRY edges.",
        inputSchema={
            "type": "object",
            "properties": {
                "uid": {"type": "string"},
                "limit": {"type": "integer", "default": 20},
            },
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_preferred_contract_sizes",
        description="Distribution of tenders by contract-value bucket (micro/small/medium/large/mega).",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_avg_procurement_value",
        description="Average, median, min, max, and total aggregate procurement value for a buyer.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_avg_bidder_count",
        description="Average, min, max bidders per tender + single-bidder rate for a buyer.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_award_concentration",
        description="HHI of awards to suppliers. High = concentrated; low = diverse.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_buyer_competitiveness",
        description=(
            "Competitiveness score for a buyer’s procurement process: "
            "blends avg-bidder score, diversity, and open-tender rate."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_buyer_timeline",
        description="Year-by-year procurement timeline: tenders, suppliers, winners, values, top winner.",
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    Tool(
        name="bie_tender_forecast",
        description=(
            "Probability and estimated timing of future tenders based on "
            "historical cadence (months between past tenders)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"uid": {"type": "string"}},
            "required": ["uid"],
        },
    ),
    # ── Executive Decision Engine tools ─────────────────────────────────
    Tool(
        name="ede_executive_decision",
        description=(
            "Master orchestration call. Combines all five intelligence engines "
            "(CIE, RIE, CeI, BIE, OIE) into one executive decision package: "
            "situation, market position, relationships, opportunity pipeline, "
            "buyer landscape, strategic priorities, risk register, "
            "executive narrative, and immediate actions."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "opportunity_limit": {"type": "integer", "default": 5},
            },
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_company_situation",
        description=(
            "High-level situational awareness: CIE company summary, CeI win "
            "rate, growth trend, top buyers, and industries. Returns health score."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_market_position",
        description=(
            "Competitive standing: pressure score, direct competitors, "
            "emerging threats, market classification (incumbent/challenger/emerging)."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_relationship_map",
        description=(
            "Key relationships: recurring partnerships, subcontractor chains, "
            "and inferred indirect relationships from RIE."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_opportunity_pipeline",
        description=(
            "Ranked opportunity pipeline from OIE: top opportunities, pipeline "
            "health score (pursue_rate), next actions, biggest risks."
        ),
        inputSchema={
            "type": "object",
            "properties": {
                "company_uid": {"type": "string"},
                "limit": {"type": "integer", "default": 10},
            },
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_buyer_landscape",
        description=(
            "Key buyer snapshots from BIE: procurement volume, forecast "
            "probability, supplier diversity, and whether the company is a "
            "preferred supplier."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_strategic_priorities",
        description=(
            "Ranked strategic priorities with evidence and recommended actions, "
            "derived from all five engines: pursue opportunities, manage "
            "competition, capture buyer timing, improve win rate, leverage "
            "partnerships."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
    Tool(
        name="ede_risk_register",
        description=(
            "Consolidated risk register from all engines: OIE opportunity risks, "
            "competitive pressure, buyer concentration, declining trend, partner "
            "dependency, and win-rate risk — each with severity and mitigation."
        ),
        inputSchema={
            "type": "object",
            "properties": {"company_uid": {"type": "string"}},
            "required": ["company_uid"],
        },
    ),
]


class KGServer:
    def __init__(
        self,
        repo_root: str,
        db_path: str,
        engines: EngineSet | None = None,
    ):
        import os
        import time

        _t0 = time.perf_counter()

        def _log(msg: str) -> None:
            elapsed = int((time.perf_counter() - _t0) * 1000)
            click.echo(f"[startup +{elapsed:>5}ms] {msg}", err=True)

        _log(f"KGServer.__init__ started  repo={repo_root!r}  db={db_path!r}")
        _log(f"DATABASE_URL={'SET' if os.environ.get('DATABASE_URL') else 'NOT SET (SQLite fallback)'}")

        self.repo_root = Path(repo_root).resolve()

        _log("open_repository() starting — may connect to PostgreSQL")
        try:
            biz_repo = open_repository(Path(db_path))
        except Exception as exc:
            _log(f"open_repository() FAILED after {int((time.perf_counter() - _t0) * 1000)}ms: {exc}")
            raise
        _log("open_repository() + setup_schema() done")

        _log("GraphDB.connect() starting (SQLite)")
        self.db = GraphDB(Path(db_path))
        self.db.biz_repo = biz_repo
        self.db.connect()
        _log("GraphDB.connect() done")

        self.engine = QueryEngine(self.db)
        _log("build_engines() starting")
        _engines = engines if engines is not None else build_engines(biz_repo)
        _log("build_engines() done")

        self.biz_engine = _engines.biz
        self.cie = _engines.cie
        self.rie = _engines.rie
        self.cei = _engines.cei
        self.bie = _engines.bie
        self.oie = _engines.oie
        self.ede = _engines.ede
        self._server = Server("tenderscope-kg")
        self._register_handlers()
        _log(f"KGServer.__init__ complete — total {int((time.perf_counter() - _t0) * 1000)}ms")

    def _register_handlers(self) -> None:
        @self._server.list_tools()
        async def list_tools(req: ListToolsRequest) -> ListToolsResult:
            return ListToolsResult(tools=_TOOLS)

        @self._server.call_tool()
        async def call_tool(req: CallToolRequest) -> CallToolResult:
            args = req.params.arguments or {}
            try:
                result = self._dispatch(req.params.name, args)
            except Exception as exc:
                result = {"error": str(exc)}
            return CallToolResult(content=[TextContent(type="text", text=json.dumps(result, indent=2))])

    def _dispatch(self, name: str, args: dict) -> Any:
        e = self.engine
        if name == "kg_search":
            return e.search(args["query"], args.get("kinds"), args.get("limit", 20))
        if name == "kg_entity_detail":
            return e.get_entity_detail(args["qualified_name"])
        if name == "kg_file_outline":
            return e.get_file_outline(args["file_path"])
        if name == "kg_callers":
            return e.get_callers(args["qualified_name"], args.get("depth", 1), args.get("limit", 20))
        if name == "kg_callees":
            return e.get_callees(args["qualified_name"], args.get("depth", 1), args.get("limit", 20))
        if name == "kg_inheritance":
            return e.get_inheritance_chain(args["qualified_name"])
        if name == "kg_imports":
            return e.get_imports(args["file_path"])
        if name == "kg_api_routes":
            return e.list_api_routes()
        if name == "kg_sql_tables":
            return e.list_sql_tables()
        if name == "kg_table_usage":
            return e.get_table_usage(args["table_name"])
        if name == "kg_subgraph":
            return e.get_subgraph(args["qualified_name"], args.get("depth", 2))
        if name == "kg_context_pack":
            return e.context_pack(
                args["task"],
                args.get("token_budget", 4000),
                args.get("seed_names"),
            )
        if name == "kg_stats":
            return e.get_stats()
        if name == "kg_reindex":
            full = args.get("full", False)
            indexer = Indexer(
                self.db,
                str(self.repo_root),
                incremental=not full,
            )
            return indexer.run()

        # ── Business graph tools ──────────────────────────────────────────
        bz = self.biz_engine
        if name == "biz_search":
            return bz.search(args["query"], args.get("kinds"), args.get("limit", 20))
        if name == "biz_entity":
            return bz.entity(args["uid"])
        if name == "biz_neighbors":
            return bz.neighbors(
                args["uid"],
                direction=args.get("direction", "both"),
                kinds=args.get("kinds"),
                limit=args.get("limit", 50),
            )
        if name == "biz_find_path":
            return bz.find_path(
                args["uid1"],
                args["uid2"],
                max_depth=args.get("max_depth", 6),
            )
        if name == "biz_related_companies":
            return bz.related_companies(args["uid"], limit=args.get("limit", 20))
        if name == "biz_contracts":
            return bz.contracts(args["uid"], limit=args.get("limit", 50))
        if name == "biz_stats":
            return bz.graph_statistics()
        if name == "biz_entity_history":
            return bz.entity_history(args["uid"])
        if name == "biz_list":
            return bz.list_by_kind(args["kind"], limit=args.get("limit", 50), offset=args.get("offset", 0))
        if name == "biz_import":
            return self._dispatch_biz_import(args)

        # ── Company Intelligence Engine tools ─────────────────────────────
        cie = self.cie
        if name == "cie_profile":
            return cie.company_profile(args["uid"])
        if name == "cie_summary":
            return cie.company_summary(args["uid"])
        if name == "cie_stats":
            return cie.company_stats(args["uid"])
        if name == "cie_buyers":
            return cie.company_buyers(args["uid"])
        if name == "cie_competitors":
            return cie.company_competitors(args["uid"], limit=args.get("limit", 20))
        if name == "cie_contracts":
            return cie.company_contracts(args["uid"], limit=args.get("limit", 100))
        if name == "cie_tenders":
            return cie.company_tenders(args["uid"], limit=args.get("limit", 200))
        if name == "cie_timeline":
            return cie.company_timeline(args["uid"])
        if name == "cie_locations":
            return cie.company_locations(args["uid"])
        if name == "cie_industries":
            return cie.company_industries(args["uid"])
        if name == "cie_top_competitors":
            return cie.top_competitors(limit=args.get("limit", 20))
        if name == "cie_companies_by_city":
            return cie.companies_by_city(args["city"], limit=args.get("limit", 100))
        if name == "cie_companies_by_province":
            return cie.companies_by_province(args["province"], limit=args.get("limit", 200))
        if name == "cie_most_connected":
            return cie.most_connected_companies(limit=args.get("limit", 20))

        # ── Relationship Intelligence Engine tools ────────────────────────────
        rie = self.rie
        if name == "rie_explain":
            return rie.explain(args["uid_a"], args["uid_b"])
        if name == "rie_strength":
            return rie.relationship_strength(args["uid_a"], args["uid_b"])
        if name == "rie_path":
            return rie.shortest_path(args["uid_a"], args["uid_b"], max_depth=args.get("max_depth", 8))
        if name == "rie_infer":
            return rie.infer_relationships(args["uid"], limit=args.get("limit", 50))
        if name == "rie_partnerships":
            return rie.recurring_partnerships(args["uid"], min_count=args.get("min_count", 2))
        if name == "rie_industry_cluster":
            return rie.industry_clusters(args["industry"], limit=args.get("limit", 100))
        if name == "rie_geo_cluster":
            return rie.geographic_clusters(args["location"], limit=args.get("limit", 200))
        if name == "rie_org_influence":
            return rie.organization_influence(args["uid"], limit=args.get("limit", 100))

        # ── Competitive Intelligence Engine tools ──────────────────────────
        cei = self.cei
        if name == "cei_competitor_profile":
            return cei.competitor_profile(args["uid"])
        if name == "cei_win_rate":
            return cei.win_rate(args["uid"])
        if name == "cei_growth_trend":
            return cei.growth_trend(args["uid"])
        if name == "cei_direct_competitors":
            return cei.direct_competitors(args["uid"], limit=args.get("limit", 50))
        if name == "cei_emerging_competitors":
            return cei.emerging_competitors(
                args["uid"],
                lookback_years=args.get("lookback_years", 2),
                limit=args.get("limit", 30),
            )
        if name == "cei_co_bidders":
            return cei.co_bidders(
                args["uid"],
                min_count=args.get("min_count", 1),
                limit=args.get("limit", 50),
            )
        if name == "cei_buyer_preferences":
            return cei.buyer_preferences(args["buyer_uid"], limit=args.get("limit", 30))
        if name == "cei_market_concentration":
            return cei.market_concentration(args["scope_uid"])
        if name == "cei_market_share":
            return cei.market_share(
                args["scope_uid"],
                by=args.get("by", "company"),
                limit=args.get("limit", 50),
            )
        if name == "cei_competitor_rankings":
            return cei.competitor_rankings(
                args["scope_uid"],
                by=args.get("by", "wins"),
                limit=args.get("limit", 30),
            )
        if name == "cei_competitive_pressure":
            return cei.competitive_pressure(args["uid"])

        # ── Buyer Intelligence Engine tools ─────────────────────────────
        bie = self.bie
        if name == "bie_buyer_profile":
            return bie.buyer_profile(args["uid"])
        if name == "bie_buyer_summary":
            return bie.buyer_summary(args["uid"])
        if name == "bie_supplier_roster":
            return bie.supplier_roster(args["uid"], limit=args.get("limit", 100))
        if name == "bie_preferred_suppliers":
            return bie.preferred_suppliers(
                args["uid"],
                min_awards=args.get("min_awards", 2),
                limit=args.get("limit", 30),
            )
        if name == "bie_supplier_loyalty":
            return bie.supplier_loyalty(args["uid"], limit=args.get("limit", 30))
        if name == "bie_supplier_diversity":
            return bie.supplier_diversity(args["uid"])
        if name == "bie_buying_patterns":
            return bie.buying_patterns(args["uid"])
        if name == "bie_procurement_seasonality":
            return bie.procurement_seasonality(args["uid"])
        if name == "bie_preferred_industries":
            return bie.preferred_industries(args["uid"], limit=args.get("limit", 20))
        if name == "bie_preferred_contract_sizes":
            return bie.preferred_contract_sizes(args["uid"])
        if name == "bie_avg_procurement_value":
            return bie.avg_procurement_value(args["uid"])
        if name == "bie_avg_bidder_count":
            return bie.avg_bidder_count(args["uid"])
        if name == "bie_award_concentration":
            return bie.award_concentration(args["uid"])
        if name == "bie_buyer_competitiveness":
            return bie.buyer_competitiveness(args["uid"])
        if name == "bie_buyer_timeline":
            return bie.buyer_timeline(args["uid"])
        if name == "bie_tender_forecast":
            return bie.tender_forecast(args["uid"])

        # ── Opportunity Intelligence Engine tools ─────────────────────────
        oie = self.oie
        if name == "oie_opportunity_profile":
            return oie.opportunity_profile(args["company_uid"], args["tender_uid"])
        if name == "oie_opportunity_score":
            return oie.opportunity_score(args["company_uid"], args["tender_uid"])
        if name == "oie_opportunity_recommendation":
            return oie.opportunity_recommendation(args["company_uid"], args["tender_uid"])
        if name == "oie_opportunity_explain":
            return oie.opportunity_explain(args["company_uid"], args["tender_uid"])
        if name == "oie_opportunity_timeline":
            return oie.opportunity_timeline(args["company_uid"], args["tender_uid"])
        if name == "oie_opportunity_risk":
            return oie.opportunity_risk(args["company_uid"], args["tender_uid"])
        if name == "oie_portfolio_impact":
            return oie.portfolio_impact(args["company_uid"], args["tender_uid"])
        if name == "oie_similar_opportunities":
            return oie.similar_opportunities(args["company_uid"], args["tender_uid"], limit=args.get("limit", 10))
        if name == "oie_best_opportunities":
            return oie.best_opportunities(args["company_uid"], limit=args.get("limit", 10))
        if name == "oie_executive_summary":
            return oie.executive_summary(args["company_uid"], limit=args.get("limit", 5))

        ede = self.ede
        if name == "ede_executive_decision":
            return ede.executive_decision(args["company_uid"], opportunity_limit=args.get("opportunity_limit", 5))
        if name == "ede_company_situation":
            return ede.company_situation(args["company_uid"])
        if name == "ede_market_position":
            return ede.market_position(args["company_uid"])
        if name == "ede_relationship_map":
            return ede.relationship_map(args["company_uid"])
        if name == "ede_opportunity_pipeline":
            return ede.opportunity_pipeline(args["company_uid"], limit=args.get("limit", 10))
        if name == "ede_buyer_landscape":
            return ede.buyer_landscape(args["company_uid"])
        if name == "ede_strategic_priorities":
            return ede.strategic_priorities(args["company_uid"])
        if name == "ede_risk_register":
            return ede.risk_register(args["company_uid"])

        return {"error": f"Unknown tool: {name}"}

    def _dispatch_biz_import(self, args: dict) -> Any:
        path = args.get("path", "")
        fmt = args.get("format", "auto")
        source_tag = args.get("source_tag", "mcp")
        repo = self.db.biz_repo

        # Resolve path: absolute or relative to repo root
        from pathlib import Path as _Path

        p = _Path(path)
        if not p.is_absolute():
            p = self.repo_root / p

        suffix = p.suffix.lower()

        if fmt == "auto":
            if suffix == ".csv":
                fmt = "csv"
            elif suffix == ".json":
                fmt = "json"
            else:
                fmt = "tenderscope"

        if fmt == "csv":
            schema = args.get("csv_schema")
            if not schema:
                return {"error": "biz_import with format=csv requires csv_schema"}
            importer = CSVImporter(repo, str(p), schema=schema, source_tag=source_tag)
        elif fmt == "json":
            importer = JSONImporter(repo, str(p), source_tag=source_tag)
        else:
            importer = TenderScopeImporter(repo, str(p), source_tag=source_tag)

        result = importer.run()
        return result.to_dict()

    async def serve(self) -> None:
        async with stdio_server() as (read_stream, write_stream):
            await self._server.run(read_stream, write_stream, self._server.create_initialization_options())

    async def serve_sse(self, host: str = "0.0.0.0", port: int = 8080) -> None:
        import os

        import uvicorn
        from mcp.server.sse import SseServerTransport
        from starlette.applications import Starlette
        from starlette.requests import Request
        from starlette.responses import JSONResponse
        from starlette.routing import Mount, Route

        sse = SseServerTransport("/messages/")

        async def handle_sse(request):
            async with sse.connect_sse(request.scope, request.receive, request._send) as (
                read_stream,
                write_stream,
            ):
                await self._server.run(
                    read_stream,
                    write_stream,
                    self._server.create_initialization_options(),
                )

        async def handle_health(request: Request) -> JSONResponse:
            stats = self.db.biz_repo.get_stats() if self.db.biz_repo else {}
            return JSONResponse({"status": "ok", "graph": stats})

        async def handle_verify(request: Request) -> JSONResponse:
            """Phase 1: verify read access to public.* tables."""
            import psycopg2

            database_url = os.environ.get("DATABASE_URL", "").strip()
            if not database_url:
                return JSONResponse({"error": "DATABASE_URL not set"}, status_code=503)
            try:
                conn = psycopg2.connect(database_url)
                from tenderscope_kg.importers.bc_scraper_pg_importer import (
                    BCScraperPGImporter,
                )

                importer = BCScraperPGImporter(repo=self.db.biz_repo, conn=conn)
                counts = importer.verify_access()
                conn.close()
                return JSONResponse({"status": "ok", "public_table_counts": counts})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        async def handle_import(request: Request) -> JSONResponse:
            """Phase 2: import public.* → graph.* via BCScraperPGImporter.
            Runs in a thread executor so the event loop stays unblocked.
            """
            import asyncio

            import psycopg2

            database_url = os.environ.get("DATABASE_URL", "").strip()
            if not database_url:
                return JSONResponse({"error": "DATABASE_URL not set"}, status_code=503)
            if self.db.biz_repo is None:
                return JSONResponse({"error": "graph repository not initialised"}, status_code=503)

            biz_repo = self.db.biz_repo

            def _run_import():
                from tenderscope_kg.importers.bc_scraper_pg_importer import (
                    BCScraperPGImporter,
                )

                conn = psycopg2.connect(database_url)
                try:
                    importer = BCScraperPGImporter(repo=biz_repo, conn=conn)
                    return importer.run()
                finally:
                    conn.close()

            try:
                loop = asyncio.get_event_loop()
                result = await loop.run_in_executor(None, _run_import)
                graph_stats = await loop.run_in_executor(None, biz_repo.get_stats)
                return JSONResponse({"import_result": result.to_dict(), "graph_stats": graph_stats})
            except Exception as exc:
                return JSONResponse({"error": str(exc)}, status_code=500)

        from .rest_server import create_rest_app
        from .server_engines import EngineSet

        rest_app = create_rest_app(
            EngineSet(
                biz=self.biz_engine,
                cie=self.cie,
                rie=self.rie,
                cei=self.cei,
                bie=self.bie,
                oie=self.oie,
                ede=self.ede,
            )
        )

        app = Starlette(
            routes=[
                Route("/sse", endpoint=handle_sse),
                Route("/api/health", endpoint=handle_health),
                Route("/api/verify", endpoint=handle_verify),
                Route("/api/import", endpoint=handle_import, methods=["POST"]),
                Mount("/messages/", app=sse.handle_post_message),
                # Stable v1 prefix and legacy prefix serve the same REST app.
                Mount("/api/v1/graph", app=rest_app),
                Mount("/api/graph", app=rest_app),
            ]
        )
        config = uvicorn.Config(app, host=host, port=port, log_level="info")
        server = uvicorn.Server(config)
        click.echo(f"MCP SSE server binding to {host}:{port}", err=True)
        await server.serve()
        click.echo("MCP SSE server stopped", err=True)


@click.command()
@click.option("--repo", required=True, help="Path to the repository root to index")
@click.option(
    "--db",
    default=None,
    help="Path to the graph database file (default: <repo>/.tkg/graph.db)",
)
@click.option("--index/--no-index", default=True, help="Run indexer on startup")
@click.option(
    "--transport",
    default="stdio",
    type=click.Choice(["stdio", "sse"]),
    help="Transport: stdio (default, for local AI agents) or sse (HTTP, for Railway/cloud)",
)
@click.option("--port", default=None, type=int, help="HTTP port for SSE transport (default: $PORT or 8080)")
def run(repo: str, db: str | None, index: bool, transport: str, port: int | None) -> None:
    """Start the TenderScope Knowledge Graph MCP server."""
    import asyncio
    import os
    import time
    import traceback

    _run_t0 = time.perf_counter()

    def _log(msg: str) -> None:
        elapsed = int((time.perf_counter() - _run_t0) * 1000)
        click.echo(f"[run +{elapsed:>5}ms] {msg}", err=True)

    _log(f"tkg-mcp starting  transport={transport!r}  index={index}")

    repo_path = Path(repo).resolve()
    if not repo_path.exists():
        click.echo(f"Error: repo path does not exist: {repo_path}", err=True)
        sys.exit(1)

    db_path = db or str(repo_path / ".tkg" / "graph.db")

    try:
        server = KGServer(str(repo_path), db_path)
    except Exception:
        _log("FATAL: KGServer.__init__ raised an exception — server cannot start")
        traceback.print_exc(file=sys.stderr)
        sys.exit(1)

    if index:
        _log(f"indexing {repo_path} ...")
        indexer = Indexer(server.db, str(repo_path))
        stats = indexer.run()
        _log(
            f"index complete: {stats['entities']} entities, "
            f"{stats['relations']} relations, "
            f"{stats.get('elapsed_s', '?')}s"
        )

    if transport == "sse":
        http_port = port or int(os.environ.get("PORT", "8080"))
        _log(f"starting uvicorn on 0.0.0.0:{http_port}")
        asyncio.run(server.serve_sse(port=http_port))
    else:
        asyncio.run(server.serve())
