# Architecture Readiness Review

**Status:** Review only. No implementation, no production changes, no migrations, no schema changes.  
**Scope:** Full TenderScope platform: bc-tender-scraper, tenderscope-kg, GraphDB, voice agent, cache, memory, deployment.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Executive Summary

This review evaluates three candidate architecture improvements before any code is committed to production:

- **Recommendation A:** Validation gate before canonical promotion.
- **Recommendation B:** New entity roles (`verified_company`, `probable_company`, `probable_person`, `generic_bucket`, `placeholder`, `unresolved`).
- **Recommendation D:** Graph confidence scoring for CI/EDE consumption.

Two additional recommendations are discussed as follow-up work:

- **Recommendation C:** External identifier disambiguation.
- **Recommendation E:** Improved person-name heuristic.
- **Recommendation F (long-term):** Migrate canonical authority to tenderscope-kg `company_uid`.

**Final verdicts:**

| Recommendation | Verdict | Rationale (one line) |
|---|---|---|
| A | **DEFER** | High value but requires measured validation and coordinated query updates before rollout. |
| B | **DEFER** | Useful model, but role proliferation increases maintenance and must be validated against all consumers. |
| C | **DEFER** | Strong enabler for B and D, but depends on external identifier data source integration. |
| D | **APPROVE** (low-risk pilot) | Uses existing graph evidence; no schema or canonical changes; can be implemented behind a threshold flag. |
| E | **APPROVE** (low-risk patch) | Incremental heuristic improvement; no data migration; can be rolled back instantly. |
| F | **REJECT** (for now) | Correct long-term direction but too disruptive for current phase; revisit after A–E are stable. |

---

## 2. Platform Architecture Overview

### 2.1 Components

| Component | Role | Technology |
|---|---|---|
| **bc-tender-scraper** | Relational canonical company registry, CI computation, permit/award ingestion. | Python, SQLAlchemy, PostgreSQL |
| **tenderscope-kg** | Graph-native company registry, intelligence engines, MCP/REST transports. | Python, FastAPI/MCP, GraphDB (SQLite/PG) |
| **GraphDB** | Business graph storage (entities + relations + evidence). | SQLite (dev) / PostgreSQL (prod) |
| **PostgreSQL** | Relational storage for permits, companies, awards, analytics, voice memory. | PostgreSQL |
| **Company Resolution** | Normalized-key matching and conflict detection in bc-tender-scraper. | Python regex + SQL index |
| **Canonical Resolution** | Deterministic merge groups and entity role assignment in bc-tender-scraper. | Python + SQL |
| **Competitive Intelligence (CI)** | SQL/graph cohort analysis and competitor ranking. | SQLAlchemy + graph queries |
| **Executive Decision Engine (EDE)** | Orchestrates five intelligence engines into an executive brief. | Python |
| **Voice Agent / Orchestrator** | Plans, executes tools, narrates, manages context. | Python + Claude + n8n |
| **Strategic Memory** | Fire-and-forget timeline/semantic persistence for company context. | PostgreSQL + async writers |
| **Session Memory** | Per-session active company identity. | PostgreSQL + in-memory fallback |
| **Narrator** | Generates natural-language responses from evidence. | Claude API |
| **Narrator Cache** | Dual-layer cache for narrator responses. | In-memory + PostgreSQL, 7-day TTL |
| **Cache layers** | Reasoning/live caches for tool results. | In-memory / Redis / PostgreSQL |
| **Telegram Bot** | User-facing chat interface. | Telegram API |
| **n8n** | Workflow automation consuming agent output. | n8n |
| **Railway** | Deployment platform. | Railway |

### 2.2 Shared Identifiers & Data Structures

| Identifier / Structure | Owner | Consumers | Notes |
|---|---|---|---|
| `Company.id` (integer) | bc-tender-scraper | CI, permits, awards, analytics, Graph importer | Stable within scraper; used as `scraper_id` in graph. |
| `company_uid` (CMP-*) | tenderscope-kg | Graph, engines, voice agent, REST/MCP | Immutable; the long-term canonical identity. |
| `canonical_name` | both | Deduplication key | Write-once in graph; normalized key in scraper. |
| `entity_role` | bc-tender-scraper | SQL analytics, CI, Graph importer | Current values: canonical, standalone, applicant_alias, probable_person. |
| `BizEntity.confidence` | tenderscope-kg | Graph engines, CI, EDE | Per-entity quality score. |
| `IdentityEvidence` | tenderscope-kg | ALIAS_OF / SAME_AS edges | Confidence, reason, explanation, evidence list. |
| `evidence_hash` | voice agent | NarratorCache key | Includes upstream data hash; self-invalidates on data change. |
| `engine_version` / `prompt_version` | voice agent | NarratorCache key | Versioned for controlled cache invalidation. |
| `session_id` → `(company_id, company_name)` | Session Memory | Orchestrator | Continuity across user messages. |
| `company_id` / `company_uid` | strategic memory | Orchestrator | Key for timeline/semantic artifacts. |

---

## 3. Dependency Map

### 3.1 Upstream / downstream dependencies

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│ Raw ingestion (scrapers, imports, APIs)                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ bc-tender-scraper                                                           │
│  ├─ Permit / Award / Builder records (PostgreSQL)                           │
│  ├─ Company Resolution (normalized-key matching)                            │
│  ├─ Canonical Resolution / Merge (entity_role assignment)                   │
│  ├─ Company Name Heuristics (person / generic detection)                    │
│  └─ Competitive Intelligence (SQL analytics)                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ scraper_id + canonical_name
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ tenderscope-kg GraphDB                                                      │
│  ├─ COMPANY nodes (uid, canonical_name, confidence, attributes)             │
│  ├─ COMPANY_ALIAS nodes + ALIAS_OF edges (IdentityEvidence)               │
│  ├─ SAME_AS merge candidates (IdentityEvidence)                             │
│  └─ Business relations (AWARDED_TO, SUBMITTED_BID, etc.)                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ MCP / REST
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ tenderscope-kg Intelligence Engines                                         │
│  ├─ Company Intelligence Engine (CIE)                                         │
│  ├─ Relationship Intelligence Engine (RIE)                                  │
│  ├─ Competitive Intelligence Engine (CeI)                                   │
│  ├─ Buyer Intelligence Engine (BIE)                                         │
│  ├─ Opportunity Intelligence Engine (OIE)                                   │
│  └─ Executive Decision Engine (EDE)                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ tool results + evidence hash
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ voice-n8n-agent Orchestrator                                                │
│  ├─ Planner / Meta-reasoner / Budget manager                                │
│  ├─ Claim validator / Hypothesis manager                                  │
│  ├─ Session Memory (company_id continuity)                                  │
│  ├─ Strategic Memory (timeline + semantic artifacts) [feature-flagged]      │
│  ├─ Narrator (Claude API)                                                   │
│  ├─ Narrator Cache (evidence_hash key)                                      │
│  └─ Intelligence Orchestrator                                             │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ User-facing interfaces                                                      │
│  ├─ Telegram Bot                                                            │
│  └─ n8n workflows                                                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Shared dependencies matrix

| Consumer | Depends on | Shared identifier | Failure mode if dependency changes |
|---|---|---|---|
| Graph importer | bc-tender-scraper `Company` rows | `Company.id` → `scraper_id` | Duplicate companies if IDs diverge. |
| Graph engines | GraphDB `COMPANY` nodes | `company_uid` | Wrong results if nodes are missing or misclassified. |
| EDE | All five engines | `company_uid` | Incomplete executive brief if one engine is inconsistent. |
| Orchestrator | tenderscope-kg tools | `company_uid` / `scraper_id` | Session context breaks if company cannot be resolved. |
| Narrator | Tool results + strategic context | `evidence_hash` | Narrator may use stale cached response if hash logic is wrong. |
| NarratorCache | Evidence hash + versions | `evidence_hash` | Cache misses or stale hits. |
| Session Memory | `company_id` + `company_name` | `session_id` | Continuity broken if active company changes identity. |
| Strategic Memory | `company_id` / `company_uid` | `company_id` | Historical facts attached to wrong entity. |
| Telegram / n8n | Final narration | N/A | Only exposed to final output; indirect risk. |

---

## 4. Company Lifecycle Data Flow

```text
Permit record
    │
    ▼
Company Discovery
    │  • extracts applicant / contractor / builder candidates
    ▼
Identity Parser
    │  • splits DBA / O/A / c/o / JV / person names
    ▼
Company Resolution
    │  • normalized-key lookup
    │  • person-name skip
    │  • conflict review
    │  • creates new Company row as standalone
    ▼
Canonical Resolution / Merge
    │  • groups by normalized key / parsed identity
    │  • assigns entity_role (canonical, applicant_alias, etc.)
    ▼
PostgreSQL (bc-tender-scraper)
    │  • Company, Permit, Award, Analytics tables
    │  • CI computed from SQL analytics
    ▼
Graph Import (bc_scraper_pg_importer)
    │  • resolve_company_uid(name, source, attributes)
    │  • attach scraper_id as external identifier
    │  • ALIAS_OF edges with IdentityEvidence
    ▼
GraphDB (tenderscope-kg)
    │  • COMPANY / COMPANY_ALIAS nodes
    │  • relations with evidence
    ▼
Competitive Intelligence Engine (CeI)
    │  • graph-based competitor analysis
    ▼
Executive Decision Engine (EDE)
    │  • blends CIE, RIE, CeI, BIE, OIE
    │  • produces priorities, risks, narrative
    ▼
Orchestrator / Planner
    │  • decides which tools to call
    │  • writes strategic memory (if enabled)
    ▼
Narrator + Narrator Cache
    │  • generates natural-language response
    │  • caches by evidence_hash
    ▼
Voice Agent / Telegram / n8n
    │  • delivers response to user
    ▼
User response
```

**Critical invariant:** The same company must be reachable by `Company.id` (scraper), `company_uid` (graph), and `session_id` (voice agent) throughout its lifecycle. Any change that breaks this mapping is high-risk.

---

## 5. Proposed Changes & Detailed Assessment

### 5.1 Recommendation A – Validation gate before canonical promotion

#### Description
Prevent generic-bucket and probable-person names from being promoted to `entity_role='canonical'` during canonical merge. Single-member generic-bucket groups should remain `standalone` or move to a new `generic_bucket` role.

#### Components affected
- bc-tender-scraper: `company_canonical_merge.py`, `parsed_identity_canonical_merge.py`, `company_resolution.py`.
- PostgreSQL: `Company.entity_role` updates.
- Graph importer: fewer canonical generic-bucket nodes imported.
- CI: fewer false competitors.
- EDE: improved market position / risk register.
- Narrator / NarratorCache: better narrations via evidence_hash invalidation.

#### Indirect side effects
- Permit/award foreign keys to `Company` remain valid; only the role changes.
- Graph `scraper_id` → `company_uid` mapping remains stable.
- CI cohort counts decrease for subjects whose competitors were generic buckets.
- Analytics dashboards that group by `entity_role` will see new/moved categories.

#### Cache effects
- **Reasoning/live caches:** May contain old competitor lists until TTL expires. If a user re-requests the same company, the old cached result could be served until the TTL expires or the cache key changes.
- **NarratorCache:** Self-healing because `evidence_hash` includes CI output. If CI output changes, hash changes → fresh narration.

#### Hash effects
- `evidence_hash` for affected companies changes because CI output changes. This is correct behavior.
- `engine_version` and `prompt_version` are unaffected.

#### Session effects
- Session Memory stores `(company_id, company_name)`. If a previously canonical company becomes `standalone`/`generic_bucket`, the stored name is still valid; the company still exists.
- No session restoration breakage.

#### Graph effects
- Graph importer may choose not to import demoted rows as `COMPANY` or may import them as `COMPANY_ALIAS` / placeholder.
- If already imported, existing `company_uid` nodes remain; their `name` and `confidence` can be updated without changing UID.

#### API effects
- bc-tender-scraper API responses include `entity_role`. Consumers that filter by `canonical` will see fewer rows.
- tenderscope-kg REST/MCP responses improve because upstream data improves.

#### Database effects
- `Company.entity_role` updates for affected rows.
- No foreign key changes.
- No schema migration.
- Analytics aggregates may need recomputation.

#### Deployment effects
- Requires a canonical merge run in production.
- Rollback requires restoring the previous `entity_role` values or re-running the old merge.
- Both old and new merge results can coexist during a blue/green cut-over if the pipeline is idempotent.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| Migration stops halfway | Some companies have new roles, others old. CI results become inconsistent. | Run merge in a transaction; use rollback snapshot; validate before committing. |
| Deployment rolls back | Revert `entity_role` to previous snapshot; re-run CI. | Keep merge snapshots; CI is deterministic. |
| Cache contains old objects | Old competitor lists served until TTL. | Set short TTL during rollout or purge cache keys for affected companies. |
| Graph contains stale entities | Demoted companies still exist as `COMPANY`. | Graph importer should update `confidence` or reclassify on next import; no ID change. |
| Sessions reference old IDs | Still valid; role change does not invalidate IDs. | No action needed. |
| Narrator cache outdated | Evidence hash changes → fresh narration. | No action needed. |
| Two versions run simultaneously | New version may filter competitors; old version may not. | Temporary inconsistency; acceptable if users see mixed results during cut-over. |

#### Deployment strategy
1. **Pre:** Run `prototype_canonical_impact.py` against production snapshot; confirm <0.5% canonical change.
2. **Deploy:** Ship code behind feature flag `VALIDATE_CANONICAL_PROMOTION`.
3. **Migration:** Run canonical merge in dry-run mode; review output; apply in transaction with snapshot.
4. **Cache:** Clear reasoning/live caches for affected companies; NarratorCache self-heals.
5. **Monitoring:** Track CI competitor count, EDE risk register changes, narrator cache hit rate.
6. **Validation:** Manual review of top 50 changes; compare CI output before/after.
7. **Rollback:** Restore `entity_role` snapshot; re-run CI.
8. **Success criteria:** <0.5% canonical change; no real companies lost; cache hit rate stable.
9. **Failure criteria:** >0.5% canonical change; verified companies demoted; user complaints.

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | High — eliminates root cause of generic-bucket canonicals. |
| Risk | Medium — heuristic errors could demote real companies. |
| Complexity | Medium — requires merge logic change and reprocessing. |
| Production impact | Medium — CI and analytics output changes. |
| Dependencies | Requires B or a new query filter to exclude `generic_bucket` from CI. |
| Regression probability | Low–Medium if validated first. |
| Rollback difficulty | Low — role snapshot + re-merge. |
| **Recommendation** | **DEFER** until Phase 2.1 numbers confirm <0.5% change and no real-company loss. |

---

### 5.2 Recommendation B – New entity roles

#### Description
Introduce richer entity roles: `verified_company`, `probable_company`, `probable_person`, `generic_bucket`, `placeholder`, `unresolved`.

#### Components affected
- bc-tender-scraper: role constants, merge logic, SQL analytics filters, CI filters, API responses.
- PostgreSQL: `Company.entity_role` enum/varchar values.
- Graph importer: mapping of roles to graph kinds / confidence.
- CI / EDE: new role filters.
- Voice agent: indirect via improved CI/EDE.

#### Indirect side effects
- Every SQL query and API consumer that filters by `entity_role` must be updated.
- Graph importer may need to map `generic_bucket` to `COMPANY_ALIAS` or skip.
- Analytics dashboards need new role categories.

#### Cache effects
- Similar to Recommendation A: CI output changes → evidence_hash changes → cache self-heals.
- Reasoning cache may serve stale results until TTL.

#### Hash effects
- `evidence_hash` changes when CI output changes.

#### Session effects
- No impact; sessions store IDs, not roles.

#### Graph effects
- If `generic_bucket` rows are imported as `COMPANY_ALIAS`, new `ALIAS_OF` edges are created. This is safe but changes the graph structure.
- If `placeholder` role is introduced, graph may need a placeholder entity kind or skip import.

#### API effects
- API responses expose new `entity_role` values. Consumers must handle them.
- Backward compatibility: old values remain valid; new values are additive.

#### Database effects
- `entity_role` values change for some rows.
- No schema migration if `entity_role` is a free-text/varchar field.
- If it is an enum, a migration is required.

#### Deployment effects
- Requires updating all SQL filters before the role values are written.
- Two-phase rollout: (1) add new roles and queries, (2) populate roles.
- Rollback: revert role values and query filters.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| Migration stops halfway | Mixed old/new roles; queries may misclassify. | Run in transaction; keep old role values until all queries are updated. |
| Deployment rolls back | Revert roles and query filters. | Snapshot roles before migration. |
| Cache contains old objects | Stale results until TTL or hash invalidation. | Short TTL during rollout. |
| Graph contains stale entities | Old `COMPANY` nodes for generic buckets. | Re-run importer or update confidence. |
| Sessions reference old IDs | No impact. | — |
| Narrator cache outdated | Self-heals. | — |
| Two versions run simultaneously | New roles may be unknown to old code. | Ensure old code treats unknown roles as safe default (e.g. exclude from CI). |

#### Deployment strategy
1. **Pre:** Update all SQL analytics and CI filters to handle new roles.
2. **Deploy:** Add role constants and projection logic behind flag `NEW_ENTITY_ROLES`.
3. **Migration:** Populate new roles in shadow column or in-place after queries are updated.
4. **Cache:** Clear CI/reasoning caches.
5. **Monitoring:** Track query results, CI output, API errors.
6. **Validation:** Confirm no query breaks; no API consumer errors.
7. **Rollback:** Revert role values and filters.
8. **Success criteria:** All queries handle new roles; no API errors; CI stable.
9. **Failure criteria:** Query errors; consumer crashes; unexpected CI loss.

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | High — explicit trust model. |
| Risk | Medium — role churn and query updates. |
| Complexity | Medium — touches many SQL queries. |
| Production impact | Medium — CI/analytics filters change. |
| Dependencies | Best paired with A and E. |
| Regression probability | Medium — missed query updates cause bugs. |
| Rollback difficulty | Medium — revert roles + filters. |
| **Recommendation** | **DEFER** until after A is validated and all query consumers are enumerated. |

---

### 5.3 Recommendation C – External identifier disambiguation

#### Description
Attach external identifiers (BC Registry, BN, GST) during import and use them to break ties in `CompanyResolver`.

#### Components affected
- bc-tender-scraper: importer, `CompanyResolver`, `Company.attributes`.
- tenderscope-kg: `attach_identifier`, `EXTERNAL_ID_KEYS`, `company_identity`.
- Graph importer: identifier attribute propagation.

#### Indirect side effects
- More accurate merges; some currently separate companies may merge.
- Graph confidence scores improve due to external ID evidence.

#### Cache / hash / session effects
- Minimal. Merges change IDs only for duplicates, which is a separate, careful process.

#### Graph effects
- More `BizEntity.attributes` populated; confidence rises.
- Potential merge of duplicate `COMPANY` nodes via `SAME_AS`.

#### API / database / deployment effects
- Requires data source for identifiers.
- Backfill existing companies.
- Potential duplicates need manual review.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| Identifier data source is wrong | Companies merge incorrectly. | Validate identifier uniqueness; manual review for collisions. |
| Backfill stops halfway | Some companies have IDs, others don't. | Idempotent backfill; resume safely. |
| Rollback | Remove identifiers; no ID changes. | Snapshot identifier state. |

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | High — improves merge accuracy and graph confidence. |
| Risk | Medium — wrong identifiers cause bad merges. |
| Complexity | Medium — data source integration + backfill. |
| Production impact | Low–Medium — mostly attribute additions. |
| Dependencies | Enables D and improves B. |
| Regression probability | Low if identifiers are validated. |
| Rollback difficulty | Low — remove attributes. |
| **Recommendation** | **DEFER** until an external identifier data source is secured and validated. |

---

### 5.4 Recommendation D – Graph confidence for CI/EDE

#### Description
Use the existing `BizEntity.confidence` attribute (and computed confidence from evidence) to filter or down-weight low-confidence companies in CI and EDE.

#### Components affected
- tenderscope-kg: importer sets `confidence`, CeI, EDE.
- bc-tender-scraper: Graph importer may need to set confidence.
- Voice agent: receives better CI/EDE output.

#### Indirect side effects
- CI competitor lists exclude low-confidence graph nodes.
- EDE market position / risk register improve.
- NarratorCache self-heals.

#### Cache effects
- Reasoning/live caches may serve stale low-confidence results until TTL.
- NarratorCache self-heals via evidence_hash.

#### Hash effects
- `evidence_hash` changes when CI output changes.

#### Session effects
- No impact.

#### Graph effects
- No schema change. Only reads existing `confidence` attribute.
- Importer may need to compute and set confidence during import.

#### API effects
- CI/EDE responses change (fewer low-confidence competitors). API schema unchanged.

#### Database effects
- No schema change.
- May require backfill of `confidence` values for existing graph nodes.

#### Deployment effects
- Add confidence threshold configuration.
- Deploy importer changes to set confidence.
- Deploy CI/EDE changes to read confidence.
- Rollback: disable threshold or set to 0.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| Threshold too aggressive | Real companies excluded. | Start with threshold=0; raise gradually; monitor. |
| Confidence not backfilled | Existing nodes have default confidence. | Lazy or batch backfill. |
| Deployment rolls back | Revert threshold to 0. | Safe. |
| Two versions run simultaneously | New version filters; old version does not. | Temporary inconsistency; acceptable. |

#### Deployment strategy
1. **Pre:** Run `prototype_graph_confidence.py`; choose threshold.
2. **Deploy:** Importer changes to set `confidence` (backward compatible).
3. **Deploy:** CI/EDE changes to respect `confidence >= threshold` (threshold default 0).
4. **Gradual raise:** Increase threshold while monitoring CI competitor counts.
5. **Cache:** Clear reasoning caches; NarratorCache self-heals.
6. **Monitoring:** Track excluded companies, user feedback, cache hit rate.
7. **Rollback:** Set threshold to 0.
8. **Success criteria:** <5% real-company loss; improved CI precision; stable cache.
9. **Failure criteria:** >5% real-company loss; user complaints.

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | High — improves decision quality with minimal disruption. |
| Risk | Low–Medium — threshold calibration risk. |
| Complexity | Low–Medium — importer + CI/EDE filter. |
| Production impact | Low — no schema or canonical changes. |
| Dependencies | Best after C (external IDs) for better confidence scores. |
| Regression probability | Low if threshold is conservative. |
| Rollback difficulty | Very low — toggle threshold. |
| **Recommendation** | **APPROVE** as a low-risk pilot. Start with threshold=0 and raise gradually. |

---

### 5.5 Recommendation E – Improved person-name heuristic

#### Description
Augment the regex person-name heuristic with city/trade deny-lists and optional graph cross-check.

#### Components affected
- bc-tender-scraper: `company_name_heuristics.py`, resolution, CI post-filter.
- Graph: optional cross-check for nodes with business relationships.

#### Indirect side effects
- Fewer false positives in CI; fewer real companies misclassified as persons.
- Slight risk of new false negatives (genuine person names passing).

#### Cache / hash / session effects
- CI output changes → evidence_hash changes → cache self-heals.
- Reasoning cache may be stale until TTL.

#### Graph effects
- Optional: if graph cross-check is used, `CompanyResolver` reads graph edges. This introduces a new dependency from scraper to graph, which is currently one-way (scraper → graph). This should be done carefully to avoid circular dependency.

#### API / database / deployment effects
- No schema change.
- No data migration.
- New deny-lists can be deployed as config.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| New rule too broad | Real companies classified as persons. | Add regression test cases; review sample. |
| Rollback | Revert deny-list config. | Instant. |
| Graph cross-check adds latency | Resolution slows down. | Make optional; cache graph lookups. |

#### Deployment strategy
1. **Pre:** Build labelled regression set (known persons vs real companies).
2. **Deploy:** Add deny-list rules behind feature flag.
3. **Run:** Reprocess a sample; compare classifications.
4. **Monitoring:** Track person-name classification rate and CI false positives.
5. **Rollback:** Revert config.
6. **Success criteria:** Fewer false positives; no new false negatives in regression set.
7. **Failure criteria:** New false negatives exceed threshold.

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | Medium — reduces false positives. |
| Risk | Low — config-only change. |
| Complexity | Low. |
| Production impact | Low. |
| Dependencies | None. |
| Regression probability | Low if tested against regression set. |
| Rollback difficulty | Very low — config revert. |
| **Recommendation** | **APPROVE** as an incremental patch. Avoid graph cross-check until scraper→graph read path is architected. |

---

### 5.6 Recommendation F – Migrate canonical authority to tenderscope-kg

#### Description
Make tenderscope-kg the system of record for canonical company identity. bc-tender-scraper continues to scrape and normalize, but canonical decisions happen through `repo.resolve_company_uid`.

#### Components affected
- All components: bc-tender-scraper, tenderscope-kg, voice agent, cache, memory, importers.

#### Indirect side effects
- Fundamental change to identity authority.
- Requires migration of foreign keys and cache keys.

#### Cache / hash / session effects
- Massive: cache keys that use `Company.id` must migrate to `company_uid`.
- Session Memory must store `company_uid`.
- NarratorCache keys change.

#### Graph effects
- Graph becomes authoritative; all `company_uid` values must be stable.

#### API / database / deployment effects
- API consumers must support both `Company.id` and `company_uid` during transition.
- Database foreign keys must be migrated.
- Railway deployment must be coordinated.

#### Failure analysis
| Scenario | Effect | Mitigation |
|---|---|---|
| Migration stops halfway | Mixed authority; inconsistent identities. | Phased migration with dual-write period. |
| Rollback | Complex; may require remapping UIDs back to IDs. | Extensive backups and runbooks. |
| Two versions run | Identity drift between old and new canonical sources. | Use feature flags and dual-read logic. |

#### Final engineering recommendation

| Dimension | Assessment |
|---|---|
| Benefit | Very high — single source of truth, immutable UIDs. |
| Risk | Very high — touches every system. |
| Complexity | Very high. |
| Production impact | Very high. |
| Dependencies | Requires A–E stable. |
| Regression probability | High if rushed. |
| Rollback difficulty | High. |
| **Recommendation** | **REJECT** for current phase. Revisit after A–E are stable and a full migration design is complete. |

---

## 6. Synchronization Review

### 6.1 What must stay synchronized

| State | Authority | Sync mechanism | Risk if out of sync |
|---|---|---|---|
| Canonical company state | bc-tender-scraper | Canonical merge runs + graph import | Graph has wrong canonical nodes. |
| Graph state | tenderscope-kg | Graph import from scraper + engine reads | Engines use stale data. |
| SQL state | PostgreSQL | SQLAlchemy + pipeline jobs | CI reads wrong company rows. |
| Cache state | In-memory / PostgreSQL | TTL + evidence_hash | Stale results served. |
| Strategic Memory | PostgreSQL | Orchestrator writes | Historical facts attach to wrong entity. |
| Session Memory | PostgreSQL | Orchestrator reads/writes | Conversation continuity breaks. |
| Narrator Cache | In-memory + PostgreSQL | evidence_hash | Stale narrations. |
| EDE | In-memory computation | Engine reads graph | Inconsistent executive brief. |
| Voice Agent | Runtime | Orchestrator | User sees wrong company context. |

### 6.2 Proposed synchronization guarantees

For **D (graph confidence)**, the system remains synchronized because:
- `confidence` is a read-time attribute; no canonical state changes.
- CI/EDE read `confidence` at query time; old cached results expire via TTL/hash.

For **A/B (role changes)**, the system remains synchronized because:
- `Company.id` and `company_uid` are stable.
- Roles are metadata; graph importer reclassifies on next import.
- Cache invalidates via `evidence_hash` or TTL.

For **F (authority migration)**, synchronization requires:
- Dual-write period where both scraper IDs and UIDs are valid.
- Backfill of `company_uid` everywhere.
- Cache key migration.
- This is why F is rejected for now.

---

## 7. Backward Compatibility

| Requirement | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| Existing APIs remain compatible | ✓ (schema unchanged) | ⚠ (new role values) | ✓ | ✓ | ✓ | ✗ (new authoritative IDs) |
| Existing Company IDs stable | ✓ | ✓ | ✓ | ✓ | ✓ | ⚠ (authority shift) |
| Cache keys remain valid | ✓ (self-heal) | ✓ (self-heal) | ✓ | ✓ | ✓ | ✗ (must migrate) |
| Session restoration works | ✓ | ✓ | ✓ | ✓ | ✓ | ⚠ (store UID) |
| Strategic Memory compatible | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ (must migrate) |
| NarratorCache compatible | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ (key changes) |
| Telegram conversations continue | ✓ | ✓ | ✓ | ✓ | ✓ | ⚠ (if session breaks) |
| n8n workflows unchanged | ✓ | ✓ | ✓ | ✓ | ✓ | ✗ (if API changes) |

---

## 8. Failure Analysis Summary

| Scenario | A | B | C | D | E | F |
|---|---|---|---|---|---|---|
| Migration stops halfway | Inconsistent roles | Inconsistent roles | Partial IDs | Threshold partial | Config partial | Authority split |
| Deployment rolls back | Revert roles | Revert roles | Remove IDs | Set threshold=0 | Revert config | Complex remap |
| Cache old objects | TTL/hash heals | TTL/hash heals | Minimal | TTL/hash heals | TTL/hash heals | Must migrate keys |
| Graph stale entities | Re-import | Re-import | Update attrs | Recompute confidence | N/A | Graph becomes source |
| Sessions old IDs | Still valid | Still valid | Still valid | Still valid | Still valid | Must update storage |
| Narrator cache outdated | Self-heals | Self-heals | Self-heals | Self-heals | Self-heals | Must update keys |
| Two versions simultaneously | Acceptable | Acceptable if old code handles unknown roles | Acceptable | Acceptable | Acceptable | Dangerous |

---

## 9. Deployment Strategy Summary

### Recommended order

1. **Phase 1 (now):** E (person-name heuristic patch) + D pilot (graph confidence with threshold=0).
2. **Phase 2:** A (validation gate) + B (new roles) together, after Phase 2.1 numbers confirm safety.
3. **Phase 3:** C (external identifiers) to improve confidence and merge quality.
4. **Phase 4 (future):** F (authority migration) only after 1–3 are stable.

### Cache strategy
- Use `evidence_hash` for narrator cache self-healing.
- For reasoning/live caches, use short TTL during rollout or explicit purge for affected companies.
- Monitor cache hit rates after each change.

### Rollback strategy
- E: revert config.
- D: set threshold to 0.
- A/B: restore `entity_role` snapshot and re-run CI.
- C: remove external ID attributes.
- F: complex; not undertaken until full runbook exists.

### Monitoring plan
- CI competitor count per sample company.
- EDE risk/priority changes.
- Narrator cache hit rate.
- API error rates.
- User feedback (Telegram).

### Validation checklist
- [ ] Prototype numbers confirm <0.5% canonical change for A.
- [ ] Manual review of top 50 affected companies.
- [ ] All SQL queries and API consumers handle new roles for B.
- [ ] Graph confidence threshold keeps >80% real companies for D.
- [ ] Regression tests pass for E.
- [ ] No strategic memory enabled until validation complete.

### Success criteria
- A/B: <0.5% real-company loss; stable CI; no API errors.
- D: improved CI precision; <5% real-company loss at chosen threshold.
- E: fewer false positives; no new false negatives in regression set.

### Failure criteria
- A/B: >0.5% real-company loss; verified companies demoted; query errors.
- D: >5% real-company loss; user complaints.
- E: new false negatives in regression set.

---

## 10. Final Recommendation Table

| Recommendation | Benefit | Risk | Complexity | Production Impact | Dependencies | Regression Probability | Rollback Difficulty | Verdict |
|---|---|---|---|---|---|---|---|---|
| A. Validation gate | High | Medium | Medium | Medium | B or filter | Low–Medium | Low | **DEFER** |
| B. New entity roles | High | Medium | Medium | Medium | A, E | Medium | Medium | **DEFER** |
| C. External identifiers | High | Medium | Medium | Low–Medium | D | Low | Low | **DEFER** |
| D. Graph confidence | High | Low–Medium | Low–Medium | Low | C (optional) | Low | Very low | **APPROVE** (pilot) |
| E. Person-name heuristic | Medium | Low | Low | Low | None | Low | Very low | **APPROVE** |
| F. Authority migration | Very high | Very high | Very high | Very high | A–E stable | High | High | **REJECT** (now) |

---

## 11. Files & References

- Phase 2 review: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\phase2-canonical-entity-graph-review.md`
- Phase 2.1 validation template: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\production-validation-go-no-go.md`
- bc-tender-scraper prototype: `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\scripts\prototype_canonical_impact.py`
- tenderscope-kg prototype: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\scripts\prototype_graph_confidence.py`
