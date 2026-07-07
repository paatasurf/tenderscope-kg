# TenderScope Intelligence Engine — Architecture

## Overview

The TenderScope Intelligence Engine is a local knowledge graph platform with two complementary layers:

```
┌─────────────────────────────────────────────────────────────┐
│                  TenderScope Intelligence Engine             │
│                                                             │
│   ┌──────────────────────┐   ┌──────────────────────────┐  │
│   │     Code Graph       │   │    Business Graph        │  │
│   │  (v1 — source code)  │   │  (v2 — domain entities)  │  │
│   │                      │   │                          │  │
│   │  files, classes,     │   │  companies, tenders,     │  │
│   │  functions, routes,  │   │  permits, contracts,     │  │
│   │  SQL tables, config  │   │  persons, addresses, …   │  │
│   └──────────┬───────────┘   └────────────┬─────────────┘  │
│              │                            │                 │
│              └──────────┬─────────────────┘                 │
│                         │                                   │
│              ┌──────────▼──────────┐                        │
│              │   SQLite WAL file   │                        │
│              │   (.tkg/graph.db)   │                        │
│              └─────────────────────┘                        │
└─────────────────────────────────────────────────────────────┘
```

Both graphs share **one SQLite file** and **one connection**, but use entirely separate table namespaces. They can be migrated to different backends independently.

---

## Code Graph

### Purpose

Understands the **structure of source code** — what files exist, what classes and functions they define, how they call each other, what SQL tables they read/write, what HTTP routes they expose.

Primarily used by **AI coding agents** via the `kg_*` MCP tools.

### Entities

| Kind | Description |
|---|---|
| `file` | A source file in the repository |
| `module` | A Python/JS module |
| `class` | A class definition |
| `function` | A top-level function |
| `method` | A method on a class |
| `sql_table` | A SQL table defined in DDL |
| `sql_column` | A column within a SQL table |
| `api_route` | An HTTP route (GET/POST/…) |
| `config_key` | A configuration value |
| `config_file` | A config file (.env, .json, .yaml, .toml) |
| `pipeline` | A named processing pipeline |
| `pipeline_stage` | A stage within a pipeline |
| `interface` | A TypeScript/JS interface |
| `type_alias` | A TypeScript type alias |
| `enum` | An enum definition |

### Relations

| Kind | Description |
|---|---|
| `imports` | Module A imports module B |
| `calls` | Function A calls function B |
| `inherits` | Class A extends class B |
| `implements` | Class A implements interface B |
| `contains` | A table contains a column |
| `uses_table` | A function reads/writes a SQL table |
| `handles_route` | A function handles an HTTP route |
| `depends_on` | Generic dependency |
| `pipeline_step` | Ordered stage within a pipeline |

### Entity IDs

Code-graph entity IDs are **content-addressed**: `sha256(kind + ":" + qualified_name)[:16]`. Stable as long as the qualified name is stable.

### Storage

```
entities        — all code entities
relations       — all code relations
entities_fts    — FTS5 content= table (porter + unicode61)
meta            — key/value store for index metadata
```

### Parsers

| Language | Files | Strategy |
|---|---|---|
| Python | `.py`, `.pyi` | `ast` module — full parse tree |
| JavaScript/TypeScript | `.js`, `.jsx`, `.ts`, `.tsx` | Regex heuristics |
| SQL | `.sql` | Regex (CREATE TABLE, DML) |
| Config | `.json`, `.yaml`, `.toml`, `.env`, GitHub Actions | Key extraction |

### Indexer

- Walks the repository, respects `.gitignore`
- Incremental: SHA256 file hashing, skips unchanged files
- Post-index pass resolves cross-file relation targets by qualified name
- Calls `db.rebuild_fts()` once after all entities are written

---

## Business Graph

### Purpose

Understands the **TenderScope business domain** — companies, tenders, contracts, permits, persons, locations, and the typed relationships between them.

Primarily used by **business intelligence workflows** via the `biz_*` MCP tools and CLI commands.

### Entity Model

Every business entity has a **permanent, immutable UID** that never changes, even if the entity's attributes are updated.

#### UID Format

```
CMP-00000001    Company
TEN-00000001    Tender
PER-00000001    Person
ADR-00000001    Address
PHN-00000001    Phone
EML-00000001    Email
WEB-00000001    Website
LIC-00000001    License
PRJ-00000001    Project
ORG-00000001    Organization
DOC-00000001    Document
PRV-00000001    Province
CTY-00000001    City
IND-00000001    Industry
NAI-00000001    NAICS code
EQP-00000001    Equipment
CON-00000001    Contract
PRM-00000001    Permit
```

UIDs are allocated from a per-prefix sequence table (`sequences`) using an atomic `INSERT … ON CONFLICT DO UPDATE`. Sequences are monotonically increasing and never recycled.

#### Deduplication

Entities are deduplicated on `(kind, canonical_name)` where `canonical_name` is the lowercased, whitespace-normalised form of `name`. If an entity already exists, attributes are **merged** (new values win for overlapping keys), and the UID is preserved.

### Entity Attributes

Attributes are stored as a **JSON blob** in the `attributes` column. This means:
- The schema can evolve without migrations
- Any field is full-text searchable via the FTS index
- Strongly-typed promoted columns (`name`, `canonical_name`) are available for fast index lookups

### Relation Model

All relations are **first-class edges** stored in `biz_relations`.

| Field | Type | Description |
|---|---|---|
| `id` | TEXT PK | sha256[:16] of (source_uid + kind + target_uid) |
| `source_uid` | TEXT FK | Source entity UID |
| `target_uid` | TEXT FK | Target entity UID |
| `kind` | TEXT | `BizRelationKind` value |
| `confidence` | REAL | 0–1 data quality score |
| `source` | TEXT | Which importer created this relation |
| `attributes` | TEXT (JSON) | Extra metadata |
| `valid_from` | TEXT | ISO 8601 — when this relation became true |
| `valid_to` | TEXT | ISO 8601 — NULL means still valid |
| `created_at` | TEXT | When this row was first written |

Relations are deduplicated on `(source_uid, kind, target_uid)` via the `id` hash.

#### All Relation Kinds

```
owns             owned_by         parent_of        subsidiary_of    member_of
employs          employed_by      managed_by       manages          contact_for
works_with       awarded_to       submitted_bid    awarded_by       licensed_by
licenses         located_at       has_address      in_city          in_province
references       related_to       depends_on       uses
has_phone        has_email        has_website
has_document     has_permit       has_contract     has_license
in_industry      has_naics        owns_equipment
issued_by        issues           applied_for      contracted_for
participated_in
code_references  (code graph → business graph bridge)
```

### Entity History

Every write to `biz_entities` appends a row to `biz_entity_history` (append-only, never updated or deleted). This provides a full audit trail of every attribute change, who made it, and when.

```sql
biz_entity_history(
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    uid        TEXT NOT NULL,
    snapshot   TEXT NOT NULL,   -- full JSON of entity at write time
    changed_by TEXT,            -- importer name
    changed_at TEXT             -- ISO 8601
)
```

### Storage

```
sequences           — per-prefix UID autoincrement counters
biz_entities        — business entities
biz_relations       — typed, temporal, provenance-tracked edges
biz_entity_history  — append-only audit log
biz_fts             — FTS5 non-content table (porter + unicode61)
```

#### Indexes

```sql
idx_biz_ent_kind        ON biz_entities(kind)
idx_biz_ent_canonical   ON biz_entities(canonical_name)
idx_biz_ent_name        ON biz_entities(name)
idx_biz_rel_source      ON biz_relations(source_uid)
idx_biz_rel_target      ON biz_relations(target_uid)
idx_biz_rel_kind        ON biz_relations(kind)
idx_biz_rel_src_kind    ON biz_relations(source_uid, kind)   ← composite for typed neighbour queries
idx_biz_rel_tgt_kind    ON biz_relations(target_uid, kind)   ← composite for typed reverse queries
idx_biz_hist_uid        ON biz_entity_history(uid)
```

The composite `(source_uid, kind)` indexes mean typed one-hop neighbour queries are `O(log N + fan-out)`, not `O(N)`, even at 50M edges.

---

## Importer Framework

Importers are **plug-ins**. Adding a new data source never requires architecture changes.

### Interface

```python
class BaseImporter(ABC):
    name: str = "base"           # unique tag for provenance
    def run(self) -> ImportResult: ...
```

### Built-in Importers

| Importer | Format | Use case |
|---|---|---|
| `CSVImporter` | Any CSV | Schema-driven, configurable column mapping |
| `JSONImporter` | Array or envelope JSON | General JSON, supports inline relations |
| `TenderScopeImporter` | TenderScope scraper output | Tenders, companies, permits, contract awards |

### Adding a New Importer

```python
from tenderscope_kg.importers.base import BaseImporter
from tenderscope_kg.biz_models import ImportResult, BizEntityKind

class MyImporter(BaseImporter):
    name = "my_source"

    def run(self) -> ImportResult:
        result = self._make_result()
        # ... read data, call self.repo.put_entity() / put_relation()
        self.repo.rebuild_fts()
        return result
```

---

## Query Engine

### Code Query Engine (`QueryEngine`)

| Method | MCP Tool | Description |
|---|---|---|
| `search()` | `kg_search` | FTS + name glob search |
| `get_entity_detail()` | `kg_entity_detail` | Full entity + neighbours |
| `get_file_outline()` | `kg_file_outline` | All entities in a file |
| `get_callers()` | `kg_callers` | Who calls this function? |
| `get_callees()` | `kg_callees` | What does this call? |
| `get_inheritance_chain()` | `kg_inheritance` | Class hierarchy |
| `get_imports()` | `kg_imports` | File import graph |
| `list_api_routes()` | `kg_api_routes` | All HTTP routes |
| `list_sql_tables()` | `kg_sql_tables` | All SQL tables + columns |
| `get_table_usage()` | `kg_table_usage` | Who reads/writes a table? |
| `get_subgraph()` | `kg_subgraph` | N-hop subgraph |
| `context_pack()` | `kg_context_pack` | Token-budgeted context for AI tasks |
| `get_stats()` | `kg_stats` | Index statistics |

### Business Query Engine (`BizQueryEngine`)

| Method | MCP Tool | Description |
|---|---|---|
| `search()` | `biz_search` | FTS + name-like search |
| `entity()` | `biz_entity` | Full entity + connections |
| `company()` | — | Rich company profile |
| `tender()` | — | Rich tender profile |
| `neighbors()` | `biz_neighbors` | One-hop typed neighbours |
| `related_companies()` | `biz_related_companies` | 2-hop company network |
| `contracts()` | `biz_contracts` | Company's awards and bids |
| `find_path()` | `biz_find_path` | BFS path between entities |
| `shortest_path()` | — | Alias for find_path |
| `graph_statistics()` | `biz_stats` | Business graph stats |
| `entity_history()` | `biz_entity_history` | Full audit trail |
| `list_by_kind()` | `biz_list` | Paginated list by kind |

---

## Company Intelligence Engine (v3)

### Purpose

Turns the business graph into a **business intelligence engine** capable of producing a complete, explainable profile for any company. Every metric is computed from graph relations at query time — nothing is stored redundantly.

### Design Principles

- **Graph-first**: all metrics derived from `biz_entities` + `biz_relations`. No denormalised summary tables.
- **Explainable**: every numeric result includes an `evidence` list of `(uid, relation_kind, name)` triples so callers can trace any figure back to raw graph edges.
- **Composable**: each method returns an independent dict. `company_profile()` assembles them all; individual methods can be called cheaply for partial views.
- **Scalable**: all queries use the `(source_uid, kind)` / `(target_uid, kind)` composite indexes. No full-table scans.
- **Backward compatible**: pure read-only layer above `BizRepository`. No schema changes.

### Company Intelligence API

| Method | MCP Tool | CLI Command | Description |
|---|---|---|---|
| `company_profile(uid)` | `cie_profile` | `cie-profile <uid>` | Complete explainable profile (all sub-queries) |
| `company_summary(uid)` | `cie_summary` | `cie-summary <uid>` | Lightweight overview with confidence score |
| `company_stats(uid)` | `cie_stats` | — | Financial stats + yearly breakdown |
| `company_buyers(uid)` | `cie_buyers` | — | Buyer orgs with tender evidence |
| `company_competitors(uid)` | `cie_competitors` | `cie-competitors <uid>` | Competitors ranked by shared evidence |
| `company_contracts(uid)` | `cie_contracts` | `cie-contracts <uid>` | Awarded contracts sorted by value |
| `company_tenders(uid)` | `cie_tenders` | `cie-tenders <uid>` | Tenders won and submitted |
| `company_timeline(uid)` | `cie_timeline` | `cie-timeline <uid>` | Chronological activity timeline |
| `company_locations(uid)` | `cie_locations` | — | Cities, provinces, addresses |
| `company_industries(uid)` | `cie_industries` | — | Industries + inferred categories |

### Graph Traversal API

| Method | MCP Tool | CLI Command | Description |
|---|---|---|---|
| `top_competitors(n)` | `cie_top_competitors` | — | Companies ranked by buyer count |
| `companies_by_city(city)` | `cie_companies_by_city` | — | All companies in a city |
| `companies_by_province(prov)` | `cie_companies_by_province` | — | All companies in a province |
| `most_connected_companies(n)` | `cie_most_connected` | `cie-most-connected` | Companies by total edge count |

### Confidence Score

The confidence score in `company_summary` is a transparent, evidence-based heuristic:

```
confidence = min(1.0, 0.30 + 0.07 × min(10, evidence_count))
```

- Starts at 0.30 for a company with zero connections.
- Increases by 0.07 per directly connected graph edge, capped at 1.0 (reached at 10 edges).
- Evidence count = number of direct edges (`biz_relations` rows) involving the company.
- **Explainable**: callers always see both `confidence_score` and `evidence_count` side by side.

### Competitor Discovery Algorithm

1. Collect all tenders the company has an `AWARDED_TO` or `SUBMITTED_BID` relation to.
2. For each tender, find all other companies that also have `AWARDED_TO` pointing to it → **shared tender competitors**.
3. For each buyer org reachable via `tender → ISSUED_BY → org`, traverse `org → ISSUES → tender` and collect companies awarded those tenders → **shared buyer competitors**.
4. Rank by total shared evidence count (shared tenders + shared buyers).

All intermediate steps are recorded in `shared_tenders` and `shared_buyers` lists with `evidence_path` strings like `CMP-A → awarded_to → TEN-1 ← awarded_to ← CMP-B`.

---

## Path Finding

`find_path` and `shortest_path` use **BFS in Python** over `biz_relations`.

**Complexity per hop:** `O(log N + fan-out)` — each neighbour lookup uses the `(source_uid, kind)` composite index.

**BFS termination:** early-exit on first path found.

**Scalability note:** Pure-Python BFS over SQLite is adequate for typical 3–6 hop paths even at millions of entities, because the visited set is bounded by graph diameter, not graph size. For sub-second latency at billions of nodes, replace `BizQueryEngine._bfs_path()` with a compiled graph extension (e.g., `sqlite-graph`, `duckdb-pgq`) or an external engine (Neo4j, Memgraph). The calling interface is unchanged.

---

## Migration Path to Other Backends

The `BizRepository` is the **only class** that touches SQLite tables. To migrate to PostgreSQL or Neo4j:

1. Implement `BizRepositoryPG` or `BizRepositoryNeo4j` with the same public method signatures.
2. Swap the instance in `GraphDB.biz_repo` (or inject at `KGServer.__init__`).
3. Zero changes to `BizQueryEngine`, importers, MCP tools, or CLI.

The `GraphDB` (code graph) is independent and can stay on SQLite indefinitely.

---

## Relationship Intelligence Engine (v4)

### Purpose

Answers **WHY** two entities are connected, not just that they are. Infers indirect relationships from graph structure, calculates weighted evidence strength, and produces natural-language explanations.

### Design Principles

- **Inference-first**: derives relationships that are not explicitly stored — shared buyers, subcontractor chains, recurring partnerships, clusters — purely from graph traversal.
- **Weighted evidence**: `strength` (0–1) is computed with geometric decay so each additional piece of evidence contributes less than the previous (diminishing returns).
- **Explainable**: every result includes `evidence_paths` — human-readable hop chains justifying each inference.
- **Confidence-scored**: `confidence = 0.5 × strength + 0.5 × min(1.0, evidence_count / 3.0)`.
- **Read-only**: no writes, no schema changes, runs purely on top of `BizRepository`.

### Relationship Intelligence API

| Method | MCP Tool | CLI Command | Description |
|---|---|---|---|
| `explain(uid_a, uid_b)` | `rie_explain` | `rie-explain <a> <b>` | Full WHY explanation with natural-language text |
| `relationship_strength(uid_a, uid_b)` | `rie_strength` | `rie-strength <a> <b>` | Numeric strength + per-signal breakdown |
| `shortest_path(uid_a, uid_b)` | `rie_path` | `rie-path <a> <b>` | BFS path with hop weights |
| `infer_relationships(uid)` | `rie_infer` | `rie-infer <uid>` | All inferred indirect relationships |
| `recurring_partnerships(uid)` | `rie_partnerships` | — | Co-appearing companies (≥N events) |
| `subcontractor_chains(uid)` | — | — | Likely subcontractor chains |
| `industry_clusters(industry_uid)` | `rie_industry_cluster` | `rie-clusters <name>` | All companies in an industry cluster |
| `geographic_clusters(location)` | `rie_geo_cluster` | `rie-clusters <name> --geo` | All companies in a city/province |
| `organization_influence(org_uid)` | `rie_org_influence` | — | Buyer org's influence on the network |
| `shared_buyers(uid_a, uid_b)` | — | — | Orgs that commissioned both |
| `shared_competitors(uid_a, uid_b)` | — | — | Companies competing against both |

### Strength / Confidence Formulas

**Per-relation weights** (highest → lowest):

```
awarded_to: 1.0    works_with: 0.9    contracted_for: 0.8
submitted_bid: 0.7 employed_by: 0.6   participated_in: 0.6
in_industry: 0.4   in_city: 0.3       in_province: 0.2
default: 0.3
```

**Strength aggregation** (geometric decay):

```
strength = min(1.0, Σ weight_i × 0.7^i)  (sorted descending, 0-indexed)
```

**Confidence** blends strength with volume:

```
confidence = min(1.0, 0.5 × strength + 0.5 × min(1.0, evidence_count / 3.0))
```

### Inference Algorithms

**Shared-buyer detection**: `company → awarded_to → tender → issued_by → org`. Two companies sharing an org are inferred industry peers.

**Subcontractor chain detection**: `company A → PARTICIPATED_IN → tender ← AWARDED_TO ← company B`. B likely subcontracted A.

**Recurring partnerships**: count co-appearances across `AWARDED_TO / SUBMITTED_BID / PARTICIPATED_IN` events. ≥ N co-appearances → recurring partnership.

**Industry / geographic clusters**: reverse traverse `IN_INDUSTRY` / `IN_CITY` / `IN_PROVINCE` edges from a single node.

**Organization influence score**: `min(1.0, 0.1 × company_count)` — a buyer org that commissioned 10+ distinct companies has influence_score = 1.0.

---

## Competitive Intelligence Engine (v5)

### Purpose

Answers **HOW WELL** a company competes in its markets, and **WHO** it competes against. Analyses competitive dynamics directly from the business graph.

### Design Principles

- **Graph-first**: every metric derives from existing edges — no external data, no hard-coded assumptions.
- **Explainable**: every result includes `evidence` — the graph triples justifying each figure.
- **HHI-based concentration**: Herfindahl–Hirschman Index (0–1) for any market scope.
- **Multi-dimensional market share**: slice by company, year, buyer, city, province, or industry.
- **Composite pressure scoring**: blends competitor density, win pressure, co-bidder intensity, and buyer HHI.

### Competitive Intelligence API

| Method | MCP Tool | CLI Command | Description |
|---|---|---|---|
| `competitor_profile(uid)` | `cei_competitor_profile` | `cei-profile <uid>` | Full competitive profile (all sub-queries) |
| `win_rate(uid)` | `cei_win_rate` | `cei-win-rate <uid>` | Win/loss rates + bid frequency |
| `growth_trend(uid)` | `cei_growth_trend` | `cei-growth <uid>` | Year-over-year activity trend |
| `direct_competitors(uid)` | `cei_direct_competitors` | `cei-competitors <uid>` | Ranked direct competitors by co-occurrence |
| `emerging_competitors(uid)` | `cei_emerging_competitors` | `cei-competitors <uid> --emerging` | Rising challengers in the same markets |
| `co_bidders(uid)` | `cei_co_bidders` | — | Companies frequently bidding alongside |
| `common_losers(uid)` | — | — | Companies frequently losing to this winner |
| `buyer_preferences(buyer_uid)` | `cei_buyer_preferences` | — | Buyer's preferred suppliers with win rates |
| `market_concentration(scope_uid)` | `cei_market_concentration` | — | HHI + dominant suppliers for a market |
| `market_share(scope_uid, by)` | `cei_market_share` | `cei-market-share <uid>` | % share breakdown by any dimension |
| `dominant_suppliers(scope_uid)` | — | — | Top-N winners ranked by award count |
| `challenger_companies(scope_uid)` | — | — | Rising challengers vs incumbents |
| `competitor_rankings(scope_uid, by)` | `cei_competitor_rankings` | `cei-rankings <uid>` | Ranked company list by wins/rate/share |
| `competitive_pressure(uid)` | `cei_competitive_pressure` | `cei-pressure <uid>` | Composite pressure score (0–1) |

### Pressure Score Formula

```
pressure = 0.30 × competitor_density
         + 0.35 × (1 − win_rate)        # win_pressure
         + 0.20 × co_bidder_intensity
         + 0.15 × buyer_hhi
```

Where:
- `competitor_density = min(1.0, unique_competitors / 20)`
- `co_bidder_intensity = min(1.0, avg_co_bidders_per_tender / 5)`
- `buyer_hhi` = average HHI of the company's top 3 buyer orgs

**Pressure levels**: `low` < 0.35 ≤ `medium` < 0.65 ≤ `high`

### HHI Formula

```
HHI = Σ (market_share_i)^2    where market_share_i = wins_i / total_wins
```

- HHI ≥ 0.25 → highly concentrated
- 0.15 ≤ HHI < 0.25 → moderately concentrated
- HHI < 0.15 → competitive

### Market Scope

All scope-based methods (`market_concentration`, `market_share`, `competitor_rankings`, `dominant_suppliers`, `challenger_companies`) accept a UID from any of:
- **ORG** — buyer organisation (tenders it issued)
- **IND** — industry (companies in that industry and their tenders)
- **CTY** — city (companies located in that city and their tenders)
- **PRV** — province (companies in that province and their tenders)
- **CMP** — company (the company's own tenders)

---

## Opportunity Intelligence Engine (v7)

### Purpose

Answers **SHOULD WE BID?** for every tender in the graph from the perspective of a given company. Produces a 0–100 Opportunity Score, a recommendation label, and a full explainability report.

### Design Principles

- **Graph-first**: every dimension (capability, history, competition, geography, …) derives from existing graph edges — no external data.
- **Explainable**: every result includes `evidence` (graph triples), `assumptions`, `weak_evidence`, `missing_information`, `reasoning_chain`, and `confidence`.
- **Read-only**: no writes. Layered above `BizRepository`.
- **Composable**: each method returns an independent dict; `opportunity_profile` assembles all of them.
- **Score range**: 0–100; higher = more attractive opportunity.

### Scoring Dimensions (weights sum to 100)

| Dimension            | Weight | Description |
|----------------------|--------|-------------|
| `capability_fit`     | 15     | Industry/capability overlap between company and tender |
| `buyer_history`      | 15     | Historical win rate with this specific buyer |
| `industry_history`   | 10     | Win rate in the tender's industry |
| `value_fit`          | 10     | Contract value matches company's typical range |
| `geographic_fit`     | 10     | Company location vs tender/buyer location |
| `competition_level`  | 10     | Fewer bidders → higher score |
| `buyer_attractiveness`| 10    | Buyer's procurement volume (relationship potential) |
| `strategic_importance`| 10    | New buyer, new industry, or geographic expansion |
| `workload_impact`    |  5     | Company's current bid activity (capacity) |
| `win_probability`    |  5     | Blended overall win rate |

### Recommendation Labels

| Score Range | Label |
|-------------|-------|
| ≥ 75        | Strong Pursue |
| ≥ 55        | Pursue |
| ≥ 40 (strategic dimension ≥ 0.6) | Strategic Investment |
| ≥ 30        | Monitor |
| < 30        | Ignore |

### Public API (10 methods)

| Method | Description |
|--------|-------------|
| `opportunity_profile(company_uid, tender_uid)` | Full profile: all sub-queries in one response |
| `opportunity_score(company_uid, tender_uid)` | 0–100 score with dimension breakdown |
| `opportunity_recommendation(company_uid, tender_uid)` | Label + why-pursue/ignore + next actions |
| `opportunity_explain(company_uid, tender_uid)` | Full explainability report |
| `opportunity_timeline(company_uid, tender_uid)` | Urgency, prep effort, deadline risk, comparable opportunities |
| `opportunity_risk(company_uid, tender_uid)` | Risk factors with severity + mitigations |
| `portfolio_impact(company_uid, tender_uid)` | Expected revenue, diversification, strategic value |
| `similar_opportunities(company_uid, tender_uid, limit)` | Historical tenders by similarity (buyer/industry/value) |
| `best_opportunities(company_uid, limit)` | Score ALL tenders; return top-N ranked |
| `executive_summary(company_uid, limit)` | CEO-dashboard: top opportunities, risks, next actions |

### MCP Tools (10 × `oie_*`)

`oie_opportunity_profile`, `oie_opportunity_score`, `oie_opportunity_recommendation`,
`oie_opportunity_explain`, `oie_opportunity_timeline`, `oie_opportunity_risk`,
`oie_portfolio_impact`, `oie_similar_opportunities`, `oie_best_opportunities`,
`oie_executive_summary`

---

## Buyer Intelligence Engine (v6)

### Purpose

Answers **WHAT** procurement organisations buy, **WHO** they buy from, **WHEN** they buy, and **HOW LIKELY** they are to issue a tender soon. Profiles buyer organisations entirely from graph traversal.

### Design Principles

- **Graph-first**: all metrics derive from existing `ISSUED_BY`, `AWARDED_TO`, `SUBMITTED_BID`, and `IN_INDUSTRY` edges.
- **Explainable**: every result carries `evidence` triples (entity → relation → entity) and a `confidence` score.
- **Read-only**: no writes. Layered above `BizRepository`.
- **Composable**: each method is independent; `buyer_profile` assembles all of them.

### Buyer Intelligence API

| Method | MCP Tool | CLI Command | Description |
|---|---|---|---|
| `buyer_profile(uid)` | `bie_buyer_profile` | `bie-profile <uid>` | Full profile (all sub-queries assembled) |
| `buyer_summary(uid)` | `bie_buyer_summary` | — | Lightweight summary: tenders, suppliers, HHI, top supplier |
| `supplier_roster(uid)` | `bie_supplier_roster` | `bie-suppliers <uid>` | All suppliers with award counts + win rates |
| `preferred_suppliers(uid)` | `bie_preferred_suppliers` | `bie-suppliers <uid> --preferred` | Suppliers awarded ≥ N times |
| `supplier_loyalty(uid)` | `bie_supplier_loyalty` | `bie-loyalty <uid>` | Loyalty index per supplier + overall loyalty score |
| `supplier_diversity(uid)` | `bie_supplier_diversity` | `bie-score <uid> --diversity` | Diversity score (1 − HHI) |
| `buying_patterns(uid)` | `bie_buying_patterns` | `bie-patterns <uid>` | Cadence, avg value, avg bidders, peak month, busiest year |
| `procurement_seasonality(uid)` | `bie_procurement_seasonality` | `bie-patterns <uid> --seasonality` | Monthly + quarterly distribution |
| `preferred_industries(uid)` | `bie_preferred_industries` | — | Industries most procured from |
| `preferred_contract_sizes(uid)` | `bie_preferred_contract_sizes` | — | Contract-value bucket distribution |
| `avg_procurement_value(uid)` | `bie_avg_procurement_value` | — | Average, median, min, max, total value |
| `avg_bidder_count(uid)` | `bie_avg_bidder_count` | — | Avg, min, max bidders + single-bidder rate |
| `award_concentration(uid)` | `bie_award_concentration` | `bie-concentration <uid>` | HHI of awards to suppliers |
| `buyer_competitiveness(uid)` | `bie_buyer_competitiveness` | `bie-score <uid>` | Competitiveness score (0–1) |
| `buyer_timeline(uid)` | `bie_buyer_timeline` | `bie-timeline <uid>` | Year-by-year procurement timeline |
| `tender_forecast(uid)` | `bie_tender_forecast` | `bie-forecast <uid>` | Cadence-based tender probability + timing |

### Scoring Formulas

**Loyalty score** = √(HHI of award counts per supplier)
- ≥ 0.50 → `high`
- 0.25–0.49 → `medium`
- < 0.25 → `low`

**Diversity score** = 1 − HHI(award counts)
- ≥ 0.75 → `high`
- 0.50–0.74 → `medium`
- 0.25–0.49 → `low`
- < 0.25 → `very_low`

**Buyer competitiveness score** — blends three components:
```
competitiveness = 0.50 × avg_bidder_score
                + 0.30 × diversity_score
                + 0.20 × open_tender_rate
```
- ≥ 0.65 → `highly_competitive`
- 0.35–0.64 → `moderately_competitive`
- < 0.35 → `low_competition`

**Tender forecast** — cadence model:
```
cadence_months = (span_years × 12) / (tender_count − 1)
months_since_last = (now − last_tender_date).months
probability = min(1.0, months_since_last / cadence_months)
```

---

## MCP Server

The single MCP server (`tkg-mcp`) exposes **73 tools** total:

- **14 code-graph tools** (`kg_*`): search, entity detail, file outline, callers, callees, inheritance, imports, API routes, SQL tables, table usage, subgraph, context pack, stats, reindex.
- **10 business-graph tools** (`biz_*`): search, entity, neighbors, find_path, related_companies, contracts, import, stats, entity_history, list.
- **14 company intelligence tools** (`cie_*`): profile, summary, stats, buyers, competitors, contracts, tenders, timeline, locations, industries, top_competitors, companies_by_city, companies_by_province, most_connected.
- **8 relationship intelligence tools** (`rie_*`): explain, strength, path, infer, partnerships, industry_cluster, geo_cluster, org_influence.
- **11 competitive intelligence tools** (`cei_*`): competitor_profile, win_rate, growth_trend, direct_competitors, emerging_competitors, co_bidders, buyer_preferences, market_concentration, market_share, competitor_rankings, competitive_pressure.
- **16 buyer intelligence tools** (`bie_*`): buyer_profile, buyer_summary, supplier_roster, preferred_suppliers, supplier_loyalty, supplier_diversity, buying_patterns, procurement_seasonality, preferred_industries, preferred_contract_sizes, avg_procurement_value, avg_bidder_count, award_concentration, buyer_competitiveness, buyer_timeline, tender_forecast.

```json
{
  "mcpServers": {
    "tkg": {
      "command": "tkg-mcp",
      "args": ["--repo", "/path/to/bc-tender-scraper"]
    }
  }
}
```

---

## CLI Reference

### Code Graph

```bash
tkg index <repo>                     # Index a repository
tkg search <query> --repo <repo>     # Search entities
tkg outline <file> --repo <repo>     # File outline
tkg callers <qname> --repo <repo>    # Who calls this?
tkg callees <qname> --repo <repo>    # What does this call?
tkg routes --repo <repo>             # All API routes
tkg tables --repo <repo>             # All SQL tables
tkg context "<task>" --repo <repo>   # Token-budgeted context pack
tkg stats --repo <repo>              # Index stats
```

### Business Graph

```bash
tkg biz-import <file> --repo <repo>  # Import from CSV/JSON/TenderScope file
tkg biz-search <query> --repo <repo> # Search business entities
tkg biz-entity <uid> --repo <repo>   # Full entity detail by UID
tkg biz-neighbors <uid> --repo <repo># One-hop neighbours
tkg biz-path <uid1> <uid2> --repo <repo> # Path between entities
tkg biz-stats --repo <repo>          # Business graph stats
```

### Company Intelligence Engine

```bash
tkg cie-profile <uid> --repo <repo>       # Full explainable company profile
tkg cie-summary <uid> --repo <repo>       # Lightweight overview + confidence score
tkg cie-tenders <uid> --repo <repo>       # Tenders won and submitted
tkg cie-contracts <uid> --repo <repo>     # Awarded contracts sorted by value
tkg cie-competitors <uid> --repo <repo>   # Competitor analysis
tkg cie-timeline <uid> --repo <repo>      # Chronological activity timeline
tkg cie-most-connected --repo <repo>      # Most connected companies
```

### Relationship Intelligence Engine

```bash
tkg rie-explain <uid_a> <uid_b> --repo <repo>   # WHY are A and B connected?
tkg rie-strength <uid_a> <uid_b> --repo <repo>  # Numeric strength + signal breakdown
tkg rie-path <uid_a> <uid_b> --repo <repo>      # BFS shortest path
tkg rie-infer <uid> --repo <repo>               # All inferred indirect relationships
tkg rie-clusters <name> --repo <repo>           # Industry cluster (by name or UID)
tkg rie-clusters <name> --geo --repo <repo>     # Geographic cluster
```

### Competitive Intelligence Engine

```bash
tkg cei-profile <uid> --repo <repo>                     # Full competitive profile
tkg cei-win-rate <uid> --repo <repo>                    # Win rate, loss rate, bid frequency
tkg cei-growth <uid> --repo <repo>                      # Year-over-year growth trend
tkg cei-competitors <uid> --repo <repo>                 # Direct competitors
tkg cei-competitors <uid> --emerging --repo <repo>      # Emerging competitors
tkg cei-market-share <scope_uid> --by company --repo <repo>  # Market share by company
tkg cei-market-share <scope_uid> --by year --repo <repo>     # Market share by year
tkg cei-rankings <scope_uid> --by wins --repo <repo>         # Rankings by win count
tkg cei-rankings <scope_uid> --by win_rate --repo <repo>     # Rankings by win rate
tkg cei-pressure <uid> --repo <repo>                    # Competitive pressure score
```

### Buyer Intelligence Engine

```bash
tkg bie-profile <uid> --repo <repo>                     # Full buyer profile
tkg bie-suppliers <uid> --repo <repo>                   # Supplier roster (all winners)
tkg bie-suppliers <uid> --preferred --repo <repo>       # Preferred suppliers (awarded ≥2 times)
tkg bie-suppliers <uid> --preferred --min-awards 3 --repo <repo>  # Custom threshold
tkg bie-patterns <uid> --repo <repo>                    # Buying patterns (cadence, avg value, …)
tkg bie-patterns <uid> --seasonality --repo <repo>      # Monthly/quarterly seasonality
tkg bie-timeline <uid> --repo <repo>                    # Year-by-year procurement timeline
tkg bie-score <uid> --repo <repo>                       # Buyer competitiveness score
tkg bie-score <uid> --diversity --repo <repo>           # Supplier diversity score
tkg bie-forecast <uid> --repo <repo>                    # Tender forecast probability + timing
tkg bie-concentration <uid> --repo <repo>               # Award concentration (HHI)
tkg bie-loyalty <uid> --repo <repo>                     # Supplier loyalty index
```

### Opportunity Intelligence Engine

```bash
tkg oie-profile  <company_uid> <tender_uid> --repo <repo>  # Full opportunity profile
tkg oie-score    <company_uid> <tender_uid> --repo <repo>  # 0–100 score + dimension breakdown
tkg oie-recommend <company_uid> <tender_uid> --repo <repo> # Recommendation label + rationale
tkg oie-explain  <company_uid> <tender_uid> --repo <repo>  # Full explainability report
tkg oie-best     <company_uid> --limit 10 --repo <repo>    # Top-N opportunities for a company
tkg oie-timeline <company_uid> <tender_uid> --repo <repo>  # Urgency, prep effort, deadline risk
tkg oie-risk     <company_uid> <tender_uid> --repo <repo>  # Risk factors + mitigations
tkg oie-portfolio <company_uid> <tender_uid> --repo <repo> # Expected revenue + strategic value
tkg oie-similar  <company_uid> <tender_uid> --repo <repo>  # Similar historical opportunities
tkg oie-executive <company_uid> --limit 5 --repo <repo>    # CEO-dashboard executive summary
```

### Executive Decision Engine

```bash
tkg ede-decision   <company_uid> --limit 5 --repo <repo>   # Full executive decision package (all engines)
tkg ede-situation  <company_uid> --repo <repo>              # Situational awareness: summary, win rate, trend
tkg ede-market     <company_uid> --repo <repo>              # Market position: pressure, classification, rivals
tkg ede-priorities <company_uid> --repo <repo>              # Ranked strategic priorities with actions
tkg ede-risks      <company_uid> --repo <repo>              # Consolidated risk register from all engines
```

---

## Integration Audit (v0.9.0)

### Findings and fixes

| Area | Finding | Resolution |
|---|---|---|
| `cli._get_ede()` | Opened DB at `Path(repo)` directly, bypassing `.tkg/graph.db` subdirectory | Fixed to match `_get_biz_engine()` pattern: `repo_path / ".tkg" / "graph.db"` |
| `EDE.market_position()` | Read `pressure.get("pressure_score")` — key does not exist in CeI output; always returned default `0.5` | Fixed to read `competitive_pressure_score` (the actual CeI key) |
| `EDE.risk_register()` | Same wrong key (`pressure_score`) for competitive risk threshold checks | Fixed to read `competitive_pressure_score` |

### MCP tool routing guidance

The `biz_*` tools remain in place for backward compatibility but agents should prefer intelligence-engine tools for analytical queries:

| Legacy tool | Preferred alternative | Reason |
|---|---|---|
| `biz_entity` (on a company UID) | `cie_profile` | Adds evidence, stats, buyers, contracts, timeline, industries |
| `biz_related_companies` | `cei_direct_competitors` or `cie_competitors` | Ranked, evidence-backed; not just a 2-hop graph walk |
| `biz_related_companies` (partner discovery) | `rie_partnerships` or `rie_infer` | Weighted strength, indirect inference |
| `biz_contracts` | `cie_contracts` | Adds total_value, average_value, award dates |
| `biz_find_path` (between companies) | `rie_path` or `rie_explain` | Evidence-backed path with strength scoring |
| `oie_executive_summary` | `ede_executive_decision` | OIE scope = opportunities only; EDE = all five engines |

### Integration test file

`tests/test_integration_audit.py` — 42 tests proving:
- `biz_related_companies` UIDs are a subset reachable via CIE/CeI competitor discovery
- `cie_contracts` (awarded wins) is a subset of `biz_contracts` (wins + bids)
- `biz_entity` for a company is a subset of `cie_profile` (which adds computed fields)
- `oie_executive_summary` scope is opportunity-only; EDE keys absent from OIE response
- EDE delegates to each sub-engine: win rate, pressure score, pipeline ordering all match direct engine calls
- `_get_ede()` and `_get_biz_engine()` resolve identical DB paths
- `KGServer._dispatch` routes every tool name to the correct engine method

---

## File Structure

```
src/tenderscope_kg/
├── __init__.py               # Package exports (all eight layers)
├── models.py                 # Code graph: Entity, Relation, EntityKind, RelationKind
├── db.py                     # Code graph: GraphDB (SQLite layer + biz_repo property)
├── indexer.py                # Code graph: repository walker + file parsers dispatcher
├── query_engine.py           # Code graph: QueryEngine (14 tools)
├── mcp_server.py             # MCP server: 91 tools (14 kg_ + 10 biz_ + 14 cie_ + 8 rie_ + 11 cei_ + 16 bie_ + 10 oie_ + 8 ede_)
├── cli.py                    # CLI: tkg command group (all commands)
├── biz_models.py             # Business graph: BizEntity, BizRelation, UIDs, kinds
├── biz_repository.py         # Business graph: BizRepository (SQLite layer)
├── biz_query_engine.py       # Business graph: BizQueryEngine (12 methods)
├── company_intelligence.py   # CIE: CompanyIntelligenceEngine (14 methods, read-only)
├── relationship_intelligence.py # RIE: RelationshipIntelligenceEngine (11 methods, read-only)
├── competitive_intelligence.py  # CeI: CompetitiveIntelligenceEngine (14 methods, read-only)
├── buyer_intelligence.py        # BIE: BuyerIntelligenceEngine (16 methods, read-only)
├── opportunity_intelligence.py  # OIE: OpportunityIntelligenceEngine (10 methods, read-only)
├── executive_decision.py        # EDE: ExecutiveDecisionEngine (8 methods, orchestration-only)
├── parsers/
│   ├── python_parser.py      # Python AST parser
│   ├── js_parser.py          # JS/TS regex parser
│   ├── sql_parser.py         # SQL DDL/DML parser
│   └── config_parser.py      # JSON/YAML/TOML/.env/Actions parser
└── importers/
    ├── __init__.py
    ├── base.py               # BaseImporter ABC
    ├── csv_importer.py       # Generic schema-driven CSV importer
    ├── json_importer.py      # Array + envelope JSON importer
    ├── tenderscope_importer.py # TenderScope scraper output importer
    └── bc_tender_importer.py # BC tender scraper importer (tenders/awards/permits)
```
