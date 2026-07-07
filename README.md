# TenderScope Intelligence Engine

A local, zero-cloud Knowledge Graph platform with **eight complementary layers**:

- **Code Graph** — indexes source code structure (classes, functions, routes, SQL tables, …) and exposes 14 MCP tools for AI coding agents.
- **Business Graph** — indexes business domain entities (companies, tenders, contracts, permits, …) with stable permanent UIDs, typed relations, and audit history.
- **Company Intelligence Engine** — aggregates the business graph into complete, explainable company profiles. Every metric is computed from graph relations. Every answer includes evidence references.
- **Relationship Intelligence Engine** — answers *WHY* two entities are connected. Infers indirect relationships, calculates weighted evidence strength, detects clusters, subcontractor chains, and recurring partnerships.
- **Competitive Intelligence Engine** — answers *HOW WELL* a company competes. Direct and emerging competitors, co-bidders, buyer preferences, market concentration (HHI), market share by any dimension, win rates, growth trends, rankings, and competitive pressure scores.
- **Buyer Intelligence Engine** — profiles *procurement organisations*. Supplier rosters, preferred suppliers, loyalty scores, diversity scores, buying patterns, seasonality, preferred industries, contract sizes, average procurement values, bidder counts, award concentration (HHI), competitiveness scores, year-by-year timelines, and cadence-based tender forecasting.
- **Opportunity Intelligence Engine** — answers *SHOULD WE BID?* for every tender. Scores each opportunity 0–100 across 10 dimensions (capability, buyer history, competition, geo fit, …), produces a recommendation label (Strong Pursue / Pursue / Strategic Investment / Monitor / Ignore), and provides a full explainability report with evidence, assumptions, timeline, risk analysis, portfolio impact, similar historical opportunities, and a CEO-dashboard executive summary.
- **Executive Decision Engine** — the single orchestration layer. Combines all five intelligence engines into one executive-quality decision package: situational awareness, market position, relationship map, opportunity pipeline, buyer landscape, ranked strategic priorities, a consolidated risk register, an executive narrative, and immediate actions. Zero graph reads of its own — delegates everything to CIE, RIE, CeI, BIE, and OIE.

All eight layers share one SQLite WAL file. Total: **91 MCP tools**. See [ARCHITECTURE.md](ARCHITECTURE.md) for full design documentation.

---

## Quick start

```bash
# Install (Python 3.11+)
pip install -e ".[dev]"

# ── Code Graph ──────────────────────────────────────────────
# Index a repository
tkg index /path/to/your/repo

# Search code entities
tkg search "authentication" --repo /path/to/your/repo

# Get token-budgeted context pack for an AI task
tkg context "add rate limiting to the user creation endpoint" --repo /path/to/your/repo

# ── Business Graph ───────────────────────────────────────────
# Import companies from a TenderScope scraper CSV
tkg biz-import tenders.csv --repo /path/to/your/repo

# Search business entities
tkg biz-search "Pacific" --repo /path/to/your/repo

# Show a company's full profile
tkg biz-entity CMP-00000001 --repo /path/to/your/repo

# Find path between two entities
tkg biz-path CMP-00000001 TEN-00000042 --repo /path/to/your/repo

# Business graph stats
tkg biz-stats --repo /path/to/your/repo

# ── Company Intelligence Engine ──────────────────────────────
# Full explainable company profile
tkg cie-profile CMP-00000001 --repo /path/to/your/repo

# Competitor analysis (shared buyers + shared tenders)
tkg cie-competitors CMP-00000001 --repo /path/to/your/repo

# Most connected companies (strongest evidence bases)
tkg cie-most-connected --repo /path/to/your/repo

# ── Relationship Intelligence Engine ───────────────────────────
# WHY are two companies connected?
tkg rie-explain CMP-00000001 CMP-00000002 --repo /path/to/your/repo

# Numeric relationship strength + per-signal breakdown
tkg rie-strength CMP-00000001 CMP-00000002 --repo /path/to/your/repo

# Shortest graph path between any two entities
tkg rie-path CMP-00000001 ORG-00000001 --repo /path/to/your/repo

# All inferred indirect relationships for a company
tkg rie-infer CMP-00000001 --repo /path/to/your/repo

# Companies in an industry cluster
tkg rie-clusters "Construction" --repo /path/to/your/repo

# Companies in a city
tkg rie-clusters "Vancouver" --geo --repo /path/to/your/repo

# ── Competitive Intelligence Engine ───────────────────────────
# Full competitive profile for a company
tkg cei-profile CMP-00000001 --repo /path/to/your/repo

# Win rate, loss rate, bid frequency
tkg cei-win-rate CMP-00000001 --repo /path/to/your/repo

# Year-over-year growth trend
tkg cei-growth CMP-00000001 --repo /path/to/your/repo

# Direct and emerging competitors
tkg cei-competitors CMP-00000001 --repo /path/to/your/repo
tkg cei-competitors CMP-00000001 --emerging --repo /path/to/your/repo

# Market share by company in a buyer org's market
tkg cei-market-share ORG-00000001 --by company --repo /path/to/your/repo

# Ranked competitors in an industry
tkg cei-rankings IND-00000001 --by win_rate --repo /path/to/your/repo

# Competitive pressure score
tkg cei-pressure CMP-00000001 --repo /path/to/your/repo

# ── Buyer Intelligence Engine ──────────────────────────────────
# Full buyer profile for a procurement organisation
tkg bie-profile ORG-00000001 --repo /path/to/your/repo

# Supplier roster (all winners) for a buyer
tkg bie-suppliers ORG-00000001 --repo /path/to/your/repo

# Preferred suppliers (awarded ≥2 times)
tkg bie-suppliers ORG-00000001 --preferred --repo /path/to/your/repo

# Buying patterns and seasonality
tkg bie-patterns ORG-00000001 --repo /path/to/your/repo
tkg bie-patterns ORG-00000001 --seasonality --repo /path/to/your/repo

# Year-by-year procurement timeline
tkg bie-timeline ORG-00000001 --repo /path/to/your/repo

# Buyer competitiveness and supplier diversity scores
tkg bie-score ORG-00000001 --repo /path/to/your/repo
tkg bie-score ORG-00000001 --diversity --repo /path/to/your/repo

# Tender forecast (probability + estimated next tender date)
tkg bie-forecast ORG-00000001 --repo /path/to/your/repo

# Award concentration (HHI)
tkg bie-concentration ORG-00000001 --repo /path/to/your/repo

# Supplier loyalty index
tkg bie-loyalty ORG-00000001 --repo /path/to/your/repo

# ── Opportunity Intelligence Engine ─────────────────────────
# Full opportunity profile (score + recommendation + all dimensions)
tkg oie-profile  CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# 0–100 Opportunity Score with dimension breakdown
tkg oie-score    CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Recommendation label + why-pursue/ignore + next actions
tkg oie-recommend CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Full explainability report (evidence, assumptions, reasoning chain)
tkg oie-explain  CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Top-N opportunities for a company (scores all tenders)
tkg oie-best     CMP-00000001 --limit 10 --repo /path/to/your/repo

# CEO-dashboard executive summary
tkg oie-executive CMP-00000001 --limit 5 --repo /path/to/your/repo

# Submission urgency, prep effort, deadline risk
tkg oie-timeline CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Risk factors with severity + mitigations
tkg oie-risk     CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Portfolio impact: expected revenue + strategic value
tkg oie-portfolio CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# Similar historical opportunities with outcome (win/loss)
tkg oie-similar  CMP-00000001 TEN-00000001 --repo /path/to/your/repo

# ── Executive Decision Engine ───────────────────────────────
# Full executive decision package (all engines combined)
tkg ede-decision   CMP-00000001 --limit 5 --repo /path/to/your/repo

# Situational awareness: CIE summary + win rate + trend
tkg ede-situation  CMP-00000001 --repo /path/to/your/repo

# Market position: competitive pressure + classification
tkg ede-market     CMP-00000001 --repo /path/to/your/repo

# Ranked strategic priorities with recommended actions
tkg ede-priorities CMP-00000001 --repo /path/to/your/repo

# Consolidated risk register from all engines
tkg ede-risks      CMP-00000001 --repo /path/to/your/repo
```

---

## MCP server

Add to your Claude Code / Windsurf / Cursor MCP config:

```json
{
  "mcpServers": {
    "tkg": {
      "command": "tkg-mcp",
      "args": ["--repo", "/absolute/path/to/your/repo"]
    }
  }
}
```

### Code Graph tools (14)

| Tool | Purpose |
|---|---|
| `kg_search` | FTS + glob search across all code entities |
| `kg_entity_detail` | Full detail + neighbours for one entity |
| `kg_file_outline` | All entities in a file, by line |
| `kg_callers` | Who calls this function? (multi-hop) |
| `kg_callees` | What does this function call? (multi-hop) |
| `kg_inheritance` | Full class hierarchy |
| `kg_imports` | All imports for a file |
| `kg_api_routes` | All HTTP routes in the codebase |
| `kg_sql_tables` | All SQL tables + columns |
| `kg_table_usage` | Which code reads/writes a table |
| `kg_subgraph` | N-hop subgraph around an entity |
| `kg_context_pack` | **Primary** — token-budgeted context pack for a task |
| `kg_stats` | Index statistics |
| `kg_reindex` | Incremental or full re-index |

### Business Graph tools (10)

| Tool | Purpose |
|---|---|
| `biz_search` | FTS + name search over companies, tenders, permits, … |
| `biz_entity` | Full detail for an entity by UID (`CMP-…`, `TEN-…`, …) |
| `biz_neighbors` | One-hop typed neighbours with direction + kind filter |
| `biz_find_path` | BFS path between any two entities |
| `biz_related_companies` | 2-hop company network for competitive intelligence |
| `biz_contracts` | All awards, bids, and contracts for a company |
| `biz_import` | Import from CSV / JSON / TenderScope file |
| `biz_stats` | Entity counts by kind, relation count, UID sequences |
| `biz_entity_history` | Full audit trail for an entity |
| `biz_list` | Paginated list of all entities of a given kind |

### Company Intelligence Engine tools (14)

| Tool | Purpose |
|---|---|
| `cie_profile` | Complete explainable profile — all sub-queries assembled |
| `cie_summary` | Lightweight overview with confidence score + evidence count |
| `cie_stats` | Financial stats: total/avg/largest contract, permits, yearly breakdown |
| `cie_buyers` | Buyer organisations with tender evidence paths |
| `cie_competitors` | Competitor companies ranked by shared buyers + tenders |
| `cie_contracts` | Awarded contracts sorted by value descending |
| `cie_tenders` | Tenders won and submitted, each with evidence path |
| `cie_timeline` | Chronological activity timeline with yearly summary |
| `cie_locations` | Cities, provinces, addresses with evidence paths |
| `cie_industries` | Direct industry relations + categories inferred from tenders |
| `cie_top_competitors` | Companies ranked by distinct buyer count (market breadth) |
| `cie_companies_by_city` | All companies in a given city |
| `cie_companies_by_province` | All companies in a given province |
| `cie_most_connected` | Companies ranked by total graph edge count |

### Relationship Intelligence Engine tools (8)

| Tool | Purpose |
|---|---|
| `rie_explain` | WHY explanation: direct edges, path, shared buyers/competitors/industries/locations, natural-language text |
| `rie_strength` | Weighted relationship strength (0–1) with per-signal breakdown |
| `rie_path` | BFS shortest path with hop relation kinds and weights |
| `rie_infer` | All inferred indirect relationships: buyer-links, subcontractor hints, partnerships, clusters |
| `rie_partnerships` | Recurring partnerships — companies co-appearing in ≥ N tenders/events |
| `rie_industry_cluster` | All companies in an industry cluster, ranked by tender count |
| `rie_geo_cluster` | All companies in a city or province |
| `rie_org_influence` | Buyer org's network influence: tender count, company count, total value, score |

### Competitive Intelligence Engine tools (11)

| Tool | Purpose |
|---|---|
| `cei_competitor_profile` | Full competitive profile — all sub-queries assembled |
| `cei_win_rate` | Win/loss rates, bid frequency, participation counts |
| `cei_growth_trend` | Year-over-year activity timeline + trend label (growing/stable/declining) |
| `cei_direct_competitors` | Ranked direct competitors by tender co-occurrence + shared buyers |
| `cei_emerging_competitors` | Rising challengers in the same markets (new entrants and growers) |
| `cei_co_bidders` | Companies that frequently bid alongside, ranked by co-bid frequency |
| `cei_buyer_preferences` | Buyer org's preferred suppliers with award counts and win rates |
| `cei_market_concentration` | HHI + concentration level + dominant suppliers for any market scope |
| `cei_market_share` | % share breakdown by company, year, buyer, city, province, or industry |
| `cei_competitor_rankings` | Ranked company list for a scope by wins, bids, win_rate, or market_share |
| `cei_competitive_pressure` | Composite pressure score (0–1) with component breakdown |

### Executive Decision Engine tools (8)

| Tool | Purpose |
|---|---|
| `ede_executive_decision` | Master call: full decision package combining all five engines — situation, market, relationships, pipeline, buyers, priorities, risks, narrative, actions |
| `ede_company_situation` | Situational awareness: CIE company summary, CeI win rate + growth trend, top buyers, industries, health score |
| `ede_market_position` | Competitive standing: pressure score, classification (incumbent/challenger/emerging), direct competitors, emerging threats |
| `ede_relationship_map` | Key relationships: recurring partnerships, subcontractor chains, inferred indirect relationships from RIE |
| `ede_opportunity_pipeline` | Ranked opportunity pipeline from OIE: top opportunities, pipeline health score, next actions, biggest risks |
| `ede_buyer_landscape` | Key buyer snapshots from BIE: procurement volume, forecast probability, supplier diversity, preferred-supplier flag |
| `ede_strategic_priorities` | Ranked strategic priorities with evidence and recommended actions (up to 10, sorted by score) |
| `ede_risk_register` | Consolidated risk register from all engines with severity, deduplication, and overall risk level |

### Opportunity Intelligence Engine tools (10)

| Tool | Purpose |
|---|---|
| `oie_opportunity_profile` | Full profile: score + recommendation + timeline + risk + portfolio + similar |
| `oie_opportunity_score` | 0–100 score with 10-dimension breakdown and evidence |
| `oie_opportunity_recommendation` | Recommendation label + why-pursue/ignore + next actions |
| `oie_opportunity_explain` | Full explainability: evidence, assumptions, reasoning chain |
| `oie_opportunity_timeline` | Urgency, prep effort, deadline risk, comparable historical wins/losses |
| `oie_opportunity_risk` | Risk factors (competition, capability, deadline, …) with severity + mitigations |
| `oie_portfolio_impact` | Expected revenue, win probability, diversification, strategic value |
| `oie_similar_opportunities` | Historical tenders similar by buyer / industry / value bucket |
| `oie_best_opportunities` | Score ALL tenders for a company; return top-N ranked |
| `oie_executive_summary` | CEO-dashboard: top opportunities, biggest risks, immediate next actions |

### Buyer Intelligence Engine tools (16)

| Tool | Purpose |
|---|---|
| `bie_buyer_profile` | Full buyer profile — all sub-queries assembled |
| `bie_buyer_summary` | Lightweight summary: tenders, suppliers, award HHI, top supplier |
| `bie_supplier_roster` | All suppliers with award counts and win rates |
| `bie_preferred_suppliers` | Suppliers awarded ≥ N times (consistent choices) |
| `bie_supplier_loyalty` | Loyalty index per supplier + overall loyalty score |
| `bie_supplier_diversity` | Diversity score (1 − HHI); higher = more diverse |
| `bie_buying_patterns` | Cadence, avg value, avg bidder count, peak month, busiest year |
| `bie_procurement_seasonality` | Monthly + quarterly tender distribution with seasonality index |
| `bie_preferred_industries` | Industries most procured from (via winning suppliers' IN_INDUSTRY) |
| `bie_preferred_contract_sizes` | Distribution by contract-value bucket (micro/small/medium/large/mega) |
| `bie_avg_procurement_value` | Average, median, min, max, and total aggregate value |
| `bie_avg_bidder_count` | Average, min, max bidders per tender + single-bidder rate |
| `bie_award_concentration` | HHI of awards to suppliers + concentration level |
| `bie_buyer_competitiveness` | Composite competitiveness score (avg-bidder score × diversity × open-tender rate) |
| `bie_buyer_timeline` | Year-by-year procurement timeline with trend label |
| `bie_tender_forecast` | Cadence-based forecast: probability + estimated next tender date |

---

## Business Graph — entity model

Every business entity has a **permanent, immutable UID** that never changes:

```
CMP-00000001  Company        TEN-00000001  Tender
PER-00000001  Person         ADR-00000001  Address
PRV-00000001  Province       CTY-00000001  City
LIC-00000001  License        PRJ-00000001  Project
CON-00000001  Contract       PRM-00000001  Permit
DOC-00000001  Document       ORG-00000001  Organization
IND-00000001  Industry       NAI-00000001  NAICS code
EQP-00000001  Equipment      PHN-00000001  Phone
EML-00000001  Email          WEB-00000001  Website
```

Entities are **deduplicated** on `(kind, canonical_name)` — importing the same company twice merges attributes rather than creating a duplicate.

---

## Business Graph — relation model

Relations are first-class edges with `confidence`, `source` (provenance), and `valid_from`/`valid_to` for temporal tracking.

```
owns / owned_by           subsidiary_of / parent_of
employs / employed_by     manages / managed_by
awarded_to / awarded_by   submitted_bid
in_city / in_province     has_address / located_at
has_permit / has_contract has_license / has_document
in_industry / has_naics   works_with / related_to
issued_by / issues        applied_for / contracted_for
participated_in
code_references           (bridge: code graph → business graph)
```

---

## Importers

| Importer | Trigger | Use case |
|---|---|---|
| `BCTenderImporter` | `bc-tender-scraper` data directory | Tenders, awards, permits, buyer orgs |
| `TenderScopeImporter` | `.csv` with tender/company/permit/award columns | Generic TenderScope scraper output |
| `CSVImporter` | Any CSV with a schema dict | Custom column mapping |
| `JSONImporter` | Array or `{entities, relations}` envelope | General JSON data |

```python
from tenderscope_kg.importers import TenderScopeImporter
from tenderscope_kg import GraphDB, BizQueryEngine

db = GraphDB(".tkg/graph.db")
db.connect()
importer = TenderScopeImporter(db.biz_repo, "tenders.csv")
result = importer.run()
print(result.to_dict())
```

Adding a new importer: subclass `BaseImporter`, implement `run() -> ImportResult`, call `self.repo.put_entity()` / `self.repo.put_relation()`. No other changes needed.

---

## Code Graph — what it indexes

| Entity kind | Examples |
|---|---|
| `file` | Every source file |
| `module` | Python modules, JS/TS modules |
| `class` | Python classes, TS classes |
| `function` / `method` | All callables with signatures + docstrings |
| `interface` / `type_alias` / `enum` | TypeScript types |
| `sql_table` / `sql_column` | DDL from `.sql` files |
| `api_route` | Express / Hono / Fastify / FastAPI route handlers |
| `config_file` / `config_key` | JSON, YAML, TOML, `.env` |
| `pipeline` / `pipeline_stage` | GitHub Actions workflows |

| Language | Parser | Strategy |
|---|---|---|
| Python | `ast` | Full parse tree — classes, functions, imports, calls, inheritance |
| JavaScript / TypeScript | regex | Classes, functions, interfaces, types, routes, imports |
| SQL | regex | `CREATE TABLE`, DML references |
| JSON / YAML / TOML / `.env` | stdlib + regex | Config key extraction |
| GitHub Actions | regex | Pipeline and stage definitions |

---

## Typical agent workflow (code)

```
1. kg_context_pack("add OAuth login")          → identify relevant files/functions
2. kg_file_outline("auth.py")                  → understand file structure
3. kg_entity_detail("myapp.auth.login_user")   → deep dive one function
4. kg_callers("myapp.auth.login_user")         → who calls it
5. kg_sql_tables()                             → which tables are involved
```

## Typical agent workflow (business)

```
1. biz_search("Pacific construction")          → find companies by name
2. biz_entity("CMP-00000001")                  → full profile + all connections
3. biz_contracts("CMP-00000001")               → what tenders/contracts they won
4. biz_related_companies("CMP-00000001")       → competitor/partner network
5. biz_find_path("CMP-00000001", "TEN-00042")  → how are they connected?
```

## Typical agent workflow (company intelligence)

```
1. biz_search("Acme Construction")             → get UID
2. cie_profile("CMP-00000001")                 → complete explainable profile
3. cie_competitors("CMP-00000001")             → ranked competitor list with evidence
4. cie_timeline("CMP-00000001")                → when and how did they win contracts?
5. cie_stats("CMP-00000001")                   → financial summary with yearly breakdown
6. cie_companies_by_city("Vancouver")          → who else operates in the same market?
```

## Typical agent workflow (relationship intelligence)

```
1. biz_search("Acme") + biz_search("Rival")    → get both UIDs
2. rie_explain("CMP-00000001", "CMP-00000002") → WHY are they connected?
3. rie_strength("CMP-00000001", "CMP-00000002")→ how strong is the relationship?
4. rie_path("CMP-00000001", "ORG-00000005")    → shortest path to buyer org
5. rie_infer("CMP-00000001")                   → all inferred indirect partners/peers
6. rie_industry_cluster("Construction")         → full competitive landscape
7. rie_org_influence("ORG-00000005")            → how powerful is this buyer org?
```

## Typical agent workflow (executive decision)

```
1. biz_search("Acme Construction")                → get UID
2. ede_executive_decision("CMP-00000001")          → complete decision package (all engines)
3. ede_strategic_priorities("CMP-00000001")        → what should we do first?
4. ede_risk_register("CMP-00000001")               → what could go wrong?
5. ede_opportunity_pipeline("CMP-00000001")        → which tenders to pursue?
6. ede_buyer_landscape("CMP-00000001")             → which buyers to pre-position with?
```

## Typical agent workflow (competitive intelligence)

```
1. biz_search("Acme Construction")               → get UID
2. cei_competitor_profile("CMP-00000001")         → full competitive profile
3. cei_win_rate("CMP-00000001")                   → win rate, bid frequency, loss rate
4. cei_direct_competitors("CMP-00000001")         → who is competing head-to-head?
5. cei_emerging_competitors("CMP-00000001")       → who is growing into this market?
6. cei_market_concentration("ORG-00000005")       → is this buyer's market concentrated?
7. cei_market_share("ORG-00000005", by="company") → share breakdown by supplier
8. cei_competitive_pressure("CMP-00000001")       → composite pressure score
9. cei_buyer_preferences("ORG-00000005")          → does this buyer favour anyone?
```

---

## Running tests

```bash
pytest tests/ -v
```

823 tests covering all eight layers: repository CRUD, query engine, importers, FTS, path finding, history, company intelligence, graph traversal, relationship inference, strength scoring, competitive analysis, win rates, market concentration, buyer profiles, opportunity scoring, recommendation labels, explainability, timeline urgency, risk factors, portfolio impact, similar opportunities, executive summary, executive decision orchestration, market position, relationship map, opportunity pipeline, buyer landscape, strategic priorities, consolidated risk register, evidence references, and integration audit (legacy biz_ paths proven to delegate through intelligence engines).

---

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for:
- Full storage schema and index design
- UID allocation and deduplication strategy
- Scalability notes (50M+ edges)
- Migration path to PostgreSQL / Neo4j
- Complete relation kind reference
