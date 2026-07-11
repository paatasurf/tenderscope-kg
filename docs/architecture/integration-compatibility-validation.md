# Platform-Wide Integration & Compatibility Validation

**Objective:** Verify that no proposed architectural improvement introduces regressions or hidden coupling before any implementation begins.  
**Constraint:** No code changes. No migrations. No schema changes.  
**Status:** Engineering validation only.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Scope & Method

This report validates the interaction between every major platform component and every proposed architectural recommendation.

### Components reviewed

- bc-tender-scraper
- tenderscope-kg
- voice-n8n-agent
- GraphDB / Graph
- Company Resolution
- Canonical Merge / Canonical Resolution
- Company UID (`company_uid`)
- Company IDs (`Company.id` / `scraper_id`)
- Registry
- Google Enrichment
- Competitive Intelligence (CI)
- Business Development Intelligence (BDI)
- Executive Decision Engine (EDE)
- Morning Brief
- Session Memory
- Strategic Memory
- Narrator Cache
- Evidence Hash
- Railway deployment
- n8n workflows
- Telegram bot
- REST API
- MCP interfaces

### Recommendations validated

- **A.** Validation gate before canonical promotion.
- **B.** New entity roles (`verified_company`, `probable_company`, `probable_person`, `generic_bucket`, `placeholder`, `unresolved`).
- **C.** External identifier disambiguation.
- **D.** Graph confidence scoring for CI/EDE.
- **E.** Improved person-name heuristic.
- **F.** Migrate canonical authority to tenderscope-kg.
- **G.** REST API versioning and pagination.
- **H.** Standardized company identity contract.
- **I.** Evidence-hash utility.
- **J.** Audit log for identity changes.
- **K.** Separate memory database configuration.
- **L.** Health checks for EngineSet.
- **M.** Circuit breaker for Claude API in Narrator.

### Evidence base

All conclusions are derived from the current implementation in these files:

- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\kinds.py:1-180`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:1-299`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py:1-64`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\mcp_server.py:1250-1700`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\executive_decision.py:1-661`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator.py:739-793`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\memory.py:1-178`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\strategic_memory.py:330-480`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\orchestrator.py:62-128` and `790-903`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\core\config.py:150-228`
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\deps.py:95-216`
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_resolution.py:49-294`
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_canonical_merge.py:170-235`
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_name_heuristics.py`

---

## 2. Platform Dependency Map

### 2.1 Component interaction graph

```text
                                 External sources
                         (scrapers, APIs, manual imports)
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                       bc-tender-scraper                                     │
│  ├─ Permit / Award / Builder tables (PostgreSQL)                           │
│  ├─ Company Resolution (normalized-key matching)                          │
│  ├─ Canonical Merge / entity_role assignment                              │
│  ├─ Google Enrichment (external enrichment on Company rows)                 │
│  ├─ Competitive Intelligence (SQL cohort analysis)                          │
│  └─ Business Development Intelligence (SQL analytics)                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ Company.id, canonical_name, entity_role
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         Graph Import                                        │
│  bc_scraper_pg_importer → resolve_company_uid()                             │
│  attaches scraper_id as external ID                                         │
│  creates ALIAS_OF edges with IdentityEvidence                               │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         tenderscope-kg GraphDB                              │
│  COMPANY, COMPANY_ALIAS, SAME_AS, business relations                       │
│  company_uid (immutable), canonical_name (write-once), confidence         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ EngineSet (CIE, RIE, CeI, BIE, OIE, EDE)
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         tenderscope-kg Intelligence                         │
│  ├─ Competitive Intelligence (graph cohorts)                                │
│  ├─ Business Development Intelligence                                       │
│  ├─ Executive Decision Engine ( blends all engines )                        │
│  ├─ Morning Brief (likely EDE-derived)                                    │
│  └─ Company identity/profile APIs                                         │
└─────────────────────────────────────────────────────────────────────────────┘
                                       │
                                       │ MCP / REST
                                       ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         voice-n8n-agent                                     │
│  ├─ Orchestrator                                                            │
│  ├─ Session Memory (company_id per session)                                 │
│  ├─ Strategic Memory (timeline + semantic, feature-flagged)                   │
│  ├─ Narrator (Claude API)                                                   │
│  ├─ Narrator Cache (evidence_hash key)                                      │
│  ├─ Opportunity cache / reasoning caches                                  │
│  └─ Telegram bot + n8n workflows                                            │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 2.2 Shared identifiers & state

| Identifier / State | Producer | Consumers | Migration risk |
|---|---|---|---|
| `Company.id` (integer) | bc-tender-scraper | Graph importer (as `scraper_id`), CI, BDI, Session Memory | High if reassigned |
| `company_uid` (CMP-*) | tenderscope-kg | Graph engines, voice agent, REST/MCP, strategic memory | Must never change |
| `canonical_name` | bc-tender-scraper → graph | Deduplication key everywhere | Write-once; do not update |
| `external_ids` | bc-tender-scraper / importers | Graph, identity APIs | Additive only |
| `entity_role` | Canonical Merge | SQL analytics, CI, BDI, graph importer | Metadata; can change |
| `evidence_hash` | Orchestrator / Narrator | NarratorCache, strategic memory context | Version-sensitive |
| `session_id` → company | Session Memory | Orchestrator, Telegram | Stable if IDs stable |

### 2.3 Synchronization points

1. **SQL → Graph:** `bc_scraper_pg_importer` maps `Company` rows to graph nodes. Not transactional across databases.
2. **Graph → Engines:** Engines read graph at request time. Stale graph = stale engine results.
3. **Engines → Orchestrator:** Tool results flow into evidence hash and strategic memory.
4. **Orchestrator → Narrator:** Evidence hash keys narrator cache.
5. **Session Memory → Orchestrator:** Continuity across messages.
6. **Strategic Memory → Orchestrator:** Context loading for future narrations.
7. **Telegram / n8n:** Consume final narration; no back-write.

---

## 3. Compatibility Matrix

### 3.1 Identifier stability by recommendation

| Rec | company_id | company_uid | canonical_name | external IDs | cache keys | evidence_hash |
|---|---|---|---|---|---|---|
| A. Validation gate | Stable | Stable | Stable | N/A | Self-heals | Self-heals |
| B. New roles | Stable | Stable | Stable | N/A | Self-heals | Self-heals |
| C. External IDs | Stable | Stable | Stable | Additive | Self-heals | Self-heals |
| D. Graph confidence | Stable | Stable | Stable | N/A | Self-heals | Self-heals |
| E. Person-name heuristic | Stable | Stable | Stable | N/A | Self-heals | Self-heals |
| F. Authority migration | Legacy | Stable | Stable | N/A | Must migrate | Must migrate |
| G. REST versioning | Stable | Stable | Stable | N/A | Stable | Stable |
| H. Identity contract | Stable | Stable | Stable | N/A | Stable | Stable |
| I. Evidence-hash utility | Stable | Stable | Stable | N/A | Stable | Versioned |
| J. Audit log | Stable | Stable | Stable | N/A | N/A | N/A |
| K. Memory DB config | Stable | Stable | Stable | N/A | Stable | Stable |
| L. Health checks | Stable | Stable | Stable | N/A | N/A | N/A |
| M. Claude circuit breaker | Stable | Stable | Stable | N/A | Stable | Stable |

### 3.2 API compatibility

| Rec | REST contract | MCP tools | Breaking change? |
|---|---|---|---|
| A | No change | No change | No |
| B | No change; `entity_role` values additive if consumers ignore unknown | No change | No, if old values preserved |
| C | No change; more external IDs possible | No change | No |
| D | No change; CI/EDE output changes | No change | No |
| E | No change; CI output changes | No change | No |
| F | New UID authoritative; dual-ID support required | New UID authoritative | Yes, unless dual-ID period |
| G | Versioned; old endpoints deprecated | No change | No, if aliases kept |
| H | Documented shape; fields additive | No change | No |
| I | No change | No change | No |
| J | New read-only audit endpoint | No change | No |
| K | No change | No change | No |
| L | New `/ready` endpoint | No change | No |
| M | No change | No change | No |

### 3.3 Cache compatibility

| Cache | A | B | C | D | E | F | G | H | I | J | K | L | M |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Session Memory | Safe | Safe | Safe | Safe | Safe | Must migrate to UID | Safe | Safe | Safe | Safe | Safe | Safe | Safe |
| Strategic Memory | Self-heals | Self-heals | Self-heals | Self-heals | Self-heals | Must migrate keys | Safe | Safe | Safe | Safe | Safe | Safe | Safe |
| Narrator Cache | Self-heals | Self-heals | Self-heals | Self-heals | Self-heals | Must migrate keys | Safe | Safe | Safe if versioned | Safe | Safe | Safe | Safe |
| Evidence cache | Self-heals | Self-heals | Self-heals | Self-heals | Self-heals | Must migrate | Safe | Safe | Safe if versioned | Safe | Safe | Safe | Safe |
| Opportunity cache | Self-heals | Self-heals | Self-heals | Self-heals | Self-heals | Must migrate | Safe | Safe | Self-heals | Safe | Safe | Safe | Safe |

**Self-heals** means cache entries are invalidated correctly by `evidence_hash` changes. **Must migrate** means the cache key format changes and a backfill or dual-key strategy is required.

---

## 4. Per-Recommendation Validation

### A. Validation gate before canonical promotion

#### 1. Components affected
**Direct:** bc-tender-scraper Canonical Merge, `Company.entity_role`.  
**Indirect:** CI, BDI, Graph importer, GraphDB, EDE, Narrator, NarratorCache, Strategic Memory, Session Memory, REST/MCP responses.

#### 2. Identifier changes
- `company_id`: stable.
- `company_uid`: stable.
- `canonical_name`: stable.
- `external_ids`: N/A.
- `cache_keys` / `evidence_hash`: self-heal.

#### 3. Cache compatibility
All caches safe. CI output changes invalidate narrator/evidence caches correctly.

#### 4. API compatibility
No breaking change. `entity_role` values remain the same set initially.

#### 5. Graph synchronization
Graph importer sees the same `Company` rows; only `entity_role` changes. If importer maps role to graph kind, synchronization logic must be reviewed.

#### 6. Deployment safety
- **Independent deploy:** No (requires scraper canonical merge + graph re-import + cache clear).
- **Feature flag:** Yes — `VALIDATE_CANONICAL_PROMOTION`.
- **Staged rollout:** Yes — dry-run → sample validation → full merge.
- **Rollback support:** Yes — restore `entity_role` snapshot.

#### 7. Production validation
- Run `prototype_canonical_impact.py` against production snapshot.
- Verify <0.5% canonical entities change.
- Manual review top 50 affected companies.
- Compare CI competitor lists before/after for sample companies.
- Verify narrator cache hit rate after rollout.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Heuristic misclassifies real companies | Medium |
| Business | CI competitor lists shrink | Low |
| Operational | Canonical merge reprocessing | Medium |
| Regression | Queries filtering by `canonical` may exclude legitimate companies | Medium |
| Data integrity | No ID changes; only metadata | Low |

---

### B. New entity roles

#### 1. Components affected
**Direct:** bc-tender-scraper role constants, Canonical Merge, SQL analytics filters, CI filters, Graph importer role mapping.  
**Indirect:** BDI, EDE, REST/MCP consumers, dashboards.

#### 2. Identifier changes
- All identifiers stable.
- `entity_role` values change but are metadata.

#### 3. Cache compatibility
All caches safe; CI output changes self-heal.

#### 4. API compatibility
No breaking change if old role values remain and new values are additive. Risk if consumers do strict enum validation.

#### 5. Graph synchronization
If `generic_bucket` maps to `COMPANY_ALIAS` or skipped in graph, synchronization logic must be updated. Existing graph UIDs remain stable.

#### 6. Deployment safety
- **Independent deploy:** No (requires query updates first).
- **Feature flag:** Yes — `NEW_ENTITY_ROLES`.
- **Staged rollout:** Yes — queries first, data second.
- **Rollback support:** Yes — revert role values and query filters.

#### 7. Production validation
- Enumerate every SQL query and API consumer that filters by `entity_role`.
- Run role projection on production snapshot.
- Validate CI output for each role.
- Test REST/MCP responses with new role values.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Missed query updates | Medium |
| Business | Analytics dashboards show new categories | Low |
| Operational | Rollback requires reverting both data and queries | Medium |
| Regression | Consumers with strict enum validation break | Medium |
| Data integrity | No ID changes | Low |

---

### C. External identifier disambiguation

#### 1. Components affected
**Direct:** bc-tender-scraper importer, `CompanyResolver`, `Company.attributes`; tenderscope-kg `attach_identifier`, `EXTERNAL_ID_KEYS`.  
**Indirect:** Graph confidence, CI, EDE, identity APIs.

#### 2. Identifier changes
- `company_id`: stable.
- `company_uid`: stable unless duplicates merge.
- `canonical_name`: stable.
- `external_ids`: additive.
- Cache: self-heals.

#### 3. Cache compatibility
Safe. If duplicate companies merge, cache keys referencing old UIDs must be handled.

#### 4. API compatibility
No breaking change. `external_ids` dictionary may gain keys.

#### 5. Graph synchronization
Importer must propagate identifiers to graph attributes. No schema change.

#### 6. Deployment safety
- **Independent deploy:** Partially (requires identifier data source).
- **Feature flag:** Yes — `EXTERNAL_IDENTIFIER_ENRICHMENT`.
- **Staged rollout:** Yes — backfill in batches.
- **Rollback support:** Yes — remove identifier attributes.

#### 7. Production validation
- Validate identifier uniqueness.
- Review collisions before merging duplicates.
- Verify graph `external_ids` after import.
- Test `company_identity` API responses.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Wrong identifier causes bad merge | Medium |
| Business | More accurate competitor matching | Low |
| Operational | Identifier data source integration | Medium |
| Regression | Duplicate merges change graph topology | Medium |
| Data integrity | Attributes additive; UID stable unless merge | Low–Medium |

---

### D. Graph confidence for CI/EDE

#### 1. Components affected
**Direct:** tenderscope-kg graph importer, `BizEntity.confidence`, CI engine, EDE.  
**Indirect:** Narrator, NarratorCache, Strategic Memory, REST/MCP responses.

#### 2. Identifier changes
All identifiers stable.

#### 3. Cache compatibility
Safe. `evidence_hash` self-heals when CI/EDE output changes.

#### 4. API compatibility
No breaking change. May add `confidence` field to responses.

#### 5. Graph synchronization
Importer computes confidence and writes to graph. SQL and graph remain synchronized; confidence is a derived graph property.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Yes — `MIN_COMPANY_CONFIDENCE` threshold (default `0.0`).
- **Staged rollout:** Yes — raise threshold gradually.
- **Rollback support:** Yes — set threshold to `0.0`.

#### 7. Production validation
- Run `prototype_graph_confidence.py`.
- Verify percentile distribution.
- Choose threshold keeping >80% real companies.
- Compare CI output before/after threshold.
- Monitor narrator cache hit rate.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Threshold calibration | Low–Medium |
| Business | Improved CI/EDE quality | Low |
| Operational | Backfill confidence values | Low |
| Regression | Low-confidence real companies excluded | Medium |
| Data integrity | No ID or schema change | Low |

---

### E. Improved person-name heuristic

#### 1. Components affected
**Direct:** bc-tender-scraper `company_name_heuristics.py`, `CompanyResolver`, CI post-filter.  
**Indirect:** EDE, Narrator, cache.

#### 2. Identifier changes
All stable.

#### 3. Cache compatibility
Safe; CI output changes self-heal.

#### 4. API compatibility
No breaking change.

#### 5. Graph synchronization
No graph synchronization impact if heuristic stays in scraper.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Yes — `PERSON_NAME_HEURISTIC_V2`.
- **Staged rollout:** Yes — sample validation.
- **Rollback support:** Yes — revert config.

#### 7. Production validation
- Build labelled regression set.
- Run heuristic on sample.
- Compare false positives and false negatives.
- Validate CI output.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | New false negatives | Low |
| Business | Cleaner competitor lists | Low |
| Operational | Config-only change | Low |
| Regression | Misclassified real companies | Low |
| Data integrity | No ID or schema change | Low |

---

### F. Migrate canonical authority to tenderscope-kg

#### 1. Components affected
**Direct:** All components: bc-tender-scraper, tenderscope-kg, voice-n8n-agent, caches, memory, Telegram, n8n, REST, MCP.

#### 2. Identifier changes
- `company_id`: becomes legacy; must remain supported.
- `company_uid`: becomes primary.
- `canonical_name`: stable.
- `external_ids`: stable.
- Cache keys: must migrate from `company_id` to `company_uid`.
- `evidence_hash`: must include `company_uid` consistently.

#### 3. Cache compatibility
**All caches must migrate.** Session Memory, Strategic Memory, NarratorCache, Evidence cache, Opportunity cache.

#### 4. API compatibility
Breaking change unless dual-ID period is maintained. Both `Company.id` and `company_uid` must be accepted.

#### 5. Graph synchronization
Graph becomes authoritative. SQL must sync to graph, reversing current direction. This is the largest synchronization change.

#### 6. Deployment safety
- **Independent deploy:** No.
- **Feature flag:** Required — `KG_AUTHORITY_MODE`.
- **Staged rollout:** Dual-write + dual-read period mandatory.
- **Rollback support:** Complex; requires UID→ID remapping plan.

#### 7. Production validation
- Dual-write consistency checks.
- Cache key migration verification.
- Session restoration tests with UID.
- n8n workflow tests.
- Rollback drill.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Identity authority inversion | High |
| Business | Potential data inconsistencies visible to users | High |
| Operational | Dual-write period complexity | High |
| Regression | Cache/session/strategic memory breakage | High |
| Data integrity | UID stable, but ID→UID mapping must be perfect | High |

---

### G. REST API versioning and pagination

#### 1. Components affected
**Direct:** `rest_server.py`.  
**Indirect:** Customer apps, bc-tender-scraper REST client, public API consumers.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No cache impact.

#### 4. API compatibility
Backward compatible if old endpoints remain as deprecated aliases.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Not required.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — revert to old endpoints.

#### 7. Production validation
- Verify old endpoints still serve.
- Test pagination on large datasets.
- Test MCP unchanged.

#### 8. Risk matrix
| Type | Risk | Score |
|---|---|---|
| Technical | Low | Low |
| Business | Low | Low |
| Operational | Low | Low |
| Regression | Low if aliases kept | Low |
| Data integrity | None | Low |

---

### H. Standardized company identity contract

#### 1. Components affected
**Direct:** REST/MCP responses, documentation.  
**Indirect:** Customer apps, enterprise integrations, new services.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No impact.

#### 4. API compatibility
No breaking change; formalizes existing shape.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Not required.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — revert docs/schema.

#### 7. Production validation
- Verify response shape matches contract.
- Add contract tests to CI.

#### 8. Risk matrix
All categories Low.

---

### I. Evidence-hash utility

#### 1. Components affected
**Direct:** `voice-n8n-agent` cache generation.  
**Indirect:** NarratorCache, Evidence cache, Strategic Memory context hash.

#### 2. Identifier changes
None. Hash algorithm may be versioned.

#### 3. Cache compatibility
Safe if versioned. A new utility can produce the same hash as current code during transition.

#### 4. API compatibility
No impact.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Yes — `EVIDENCE_HASH_V2`.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — revert utility version.

#### 7. Production validation
- Compare old and new hash outputs on sample evidence.
- Verify cache hit/miss consistency.

#### 8. Risk matrix
All categories Low.

---

### J. Audit log for identity changes

#### 1. Components affected
**Direct:** tenderscope-kg repository operations.  
**Indirect:** Debugging, compliance, future event consumers.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No impact.

#### 4. API compatibility
No breaking change; adds read-only endpoint.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Not required.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — drop table / stop writes.

#### 7. Production validation
- Verify writes on company creation, alias attach, external ID attach.
- Load test append-only writes.

#### 8. Risk matrix
All categories Low.

---

### K. Separate memory database configuration

#### 1. Components affected
**Direct:** `voice-n8n-agent` config and initialization.  
**Indirect:** Session Memory, Strategic Memory, NarratorCache.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No impact if connection string stays the same by default.

#### 4. API compatibility
No impact.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Not required.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — revert to shared `DATABASE_URL`.

#### 7. Production validation
- Verify each memory component connects to correct DB.
- Test failover to in-memory fallback.

#### 8. Risk matrix
All categories Low.

---

### L. Health checks for EngineSet

#### 1. Components affected
**Direct:** `rest_server.py`, `server_engines.py`.  
**Indirect:** Railway deployment, monitoring.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No impact.

#### 4. API compatibility
No breaking change.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Not required.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — remove endpoint.

#### 7. Production validation
- Verify `/ready` returns correct status when DB is unavailable.
- Verify Railway health check behavior.

#### 8. Risk matrix
All categories Low.

---

### M. Circuit breaker for Claude API in Narrator

#### 1. Components affected
**Direct:** `narrator.py`.  
**Indirect:** NarratorCache, Telegram user experience.

#### 2. Identifier changes
None.

#### 3. Cache compatibility
No impact; fallback responses can be cached separately.

#### 4. API compatibility
No impact.

#### 5. Graph synchronization
None.

#### 6. Deployment safety
- **Independent deploy:** Yes.
- **Feature flag:** Yes — `CLAUDE_CIRCUIT_BREAKER`.
- **Staged rollout:** Yes.
- **Rollback support:** Yes — disable circuit breaker.

#### 7. Production validation
- Simulate Claude timeout.
- Verify fallback response is served.
- Verify circuit opens/closes correctly.

#### 8. Risk matrix
All categories Low.

---

## 5. Risk Summary Matrix

| Rec | Technical | Business | Operational | Regression | Data Integrity |
|---|---|---|---|---|---|
| A | Medium | Low | Medium | Medium | Low |
| B | Medium | Low | Medium | Medium | Low |
| C | Medium | Low | Medium | Medium | Low–Medium |
| D | Low–Medium | Low | Low | Medium | Low |
| E | Low | Low | Low | Low | Low |
| F | High | High | High | High | High |
| G | Low | Low | Low | Low | Low |
| H | Low | Low | Low | Low | Low |
| I | Low | Low | Low | Low | Low |
| J | Low | Low | Low | Low | Low |
| K | Low | Low | Low | Low | Low |
| L | Low | Low | Low | Low | Low |
| M | Low | Low | Low | Low | Low |

---

## 6. Deployment & Rollback Strategy

### 6.1 General rules

1. **Never deploy A, B, or F without feature flags.**
2. **Always keep a snapshot of `Company.entity_role` before A or B.**
3. **Always keep a graph DB backup before C or F.**
4. **Always dual-ID support during F.**
5. **Cache layers must have short TTL or explicit purge during A–F.**
6. **Strategic Memory must remain disabled until A–D are validated.**

### 6.2 Rollback procedures

| Rec | Rollback action | Time to recover |
|---|---|---|
| A | Restore `entity_role` snapshot; re-run CI; purge caches. | Minutes to hours |
| B | Revert role values; revert query filters; purge caches. | Minutes to hours |
| C | Remove external ID attributes; revert duplicate merges. | Hours |
| D | Set `MIN_COMPANY_CONFIDENCE` to `0.0`. | Seconds |
| E | Revert heuristic config. | Seconds |
| F | Dual-read fallback; remap UIDs to IDs; restore scraper authority. | Days |
| G | Revert to old endpoint paths. | Minutes |
| H | Revert documentation/schema. | Minutes |
| I | Revert to old hash utility. | Minutes |
| J | Stop audit writes; optionally drop table. | Minutes |
| K | Revert to shared `DATABASE_URL`. | Minutes |
| L | Remove endpoint. | Minutes |
| M | Disable circuit breaker. | Seconds |

---

## 7. Regression Test Checklist

### 7.1 Identity & data integrity
- [ ] `company_uid` remains immutable across all recommendations.
- [ ] `canonical_name` remains write-once.
- [ ] `Company.id` ↔ `scraper_id` ↔ `company_uid` mappings remain consistent.
- [ ] External IDs are additive only.

### 7.2 Cache
- [ ] Session Memory restores the same company after restart.
- [ ] Strategic Memory artifacts remain attached to the correct UID.
- [ ] NarratorCache invalidates when evidence changes.
- [ ] No stale narrations served after A–E.

### 7.3 APIs
- [ ] MCP tool names and arguments unchanged.
- [ ] REST endpoints remain backward compatible during G.
- [ ] Customer apps receive expected fields.

### 7.4 Graph synchronization
- [ ] SQL `Company` row count matches graph `COMPANY` + `COMPANY_ALIAS` nodes after import.
- [ ] `scraper_id` attribute on graph nodes matches scraper `Company.id`.
- [ ] Graph confidence values are populated for all nodes after D.

### 7.5 Voice agent
- [ ] Telegram conversation continuity preserved.
- [ ] n8n workflows receive expected payloads.
- [ ] Fallback narration works when Claude is unavailable (M).

### 7.6 Deployment
- [ ] Feature flags can disable each recommendation independently.
- [ ] Rollback procedure tested in staging.
- [ ] Health checks report failure before users notice.

---

## 8. Final Recommended Implementation Order

### 8.1 Dependency graph

```text
Independent foundation:
  G (REST versioning) ──┐
  H (Identity contract)─┤
  I (Evidence-hash)    ├─ enables safe future changes
  J (Audit log)        ├─ observability
  K (Memory DB config)─┤
  L (Health checks)   ┤
  M (Claude CB)       ┘

Low-risk data improvements:
  E (Person-name heuristic) ──┐
  D (Graph confidence)        ├─ safe to deploy together
  C (External IDs, optional)   ┘

Canonical model changes (must be validated first):
  A (Validation gate) ──► B (New roles)

Authority migration (depends on everything above):
  A, B, C, D, E, G, H, I stable ──► F (Authority migration)
```

### 8.2 Optimal deployment sequence

**Phase 1 — Foundation (now, all independent, all low risk):**
1. G. REST API versioning + pagination.
2. H. Standardized company identity contract.
3. I. Evidence-hash utility.
4. J. Audit log for identity changes.
5. K. Separate memory database configuration.
6. L. Health checks for EngineSet.
7. M. Circuit breaker for Claude API.

**Phase 2 — Safe data improvements (after Phase 1, in any order):**
8. E. Improved person-name heuristic.
9. D. Graph confidence scoring (threshold starts at 0.0).
10. C. External identifier disambiguation (if data source ready).

**Phase 3 — Canonical model changes (after Phase 2 validation):**
11. A. Validation gate before canonical promotion.
12. B. New entity roles (after A is stable and queries updated).

**Phase 4 — Authority migration (only after all above stable):**
13. F. Migrate canonical authority to tenderscope-kg.

### 8.3 What should never be implemented together

- **A and B** should not be deployed in the same release. Validate A first, then introduce new roles.
- **F** should never be deployed with any other recommendation. It requires its own release, dual-write period, and dedicated rollback plan.
- **C and F** should not be combined. External ID work should stabilize before authority migration.
- **B and F** should not be combined. Roles must stabilize before KG becomes authoritative.

---

## 9. Hidden Dependencies Identified

1. **NarratorCache depends on CI output consistency.** Any recommendation that changes CI (A, B, D, E) must ensure `evidence_hash` includes the changed fields.
2. **Strategic Memory is fire-and-forget but company-keyed.** If F changes the primary company key from `company_id` to `company_uid`, existing strategic memory entries become orphaned.
3. **Session Memory stores `company_id` integer.** It is safe for A–E but must be migrated for F.
4. **Google Enrichment writes to scraper `Company` rows.** Any change to scraper schema or role logic affects enrichment data interpretation.
5. **n8n workflows likely parse REST/MCP responses by field name.** Adding new fields is safe; renaming or removing fields is breaking.
6. **Telegram bot depends on Orchestrator, which depends on Session Memory.** A session restore bug under F would break conversation continuity.
7. **Morning Brief likely derives from EDE.** Any EDE change (D) changes Morning Brief output indirectly.
8. **Graph importer assumes scraper `entity_role` semantics.** New roles (B) require importer mapping updates.

---

## 10. Conclusion

The platform can absorb all low-risk recommendations (D, E, G–M) independently without regressions. The medium-risk recommendations (A, B, C) require feature flags and staged validation but do not require rewrites. The high-risk recommendation (F) must be deferred until all other recommendations are stable and a full dual-ID migration is designed.

The most important pre-implementation work is **not coding** but **validation**: running the two prototype scripts against production-scale data to confirm the impact of A and D before any production merge.
