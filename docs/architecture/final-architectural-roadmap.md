# Final Architectural Roadmap

**Objective:** Identify what to implement now, what to defer, what to reject, and what permanent architectural foundations should remain stable for 3–5 years.  
**Constraint:** Every recommendation must be supported by evidence from the current implementation. No fashionable patterns. No rewrites without measurable benefit.  
**Status:** Review only. No implementation.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## Method

This roadmap synthesizes four previous artifacts:

1. `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\phase2-canonical-entity-graph-review.md` — root-cause analysis.
2. `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\entity-validation-impact-analysis.md` — validation plan.
3. `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architecture-readiness-review.md` — platform-wide impact assessment.
4. `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architectural-stress-test.md` — 3–5 year scalability analysis.

Each recommendation below maps to a concrete file, class, or pattern already present in the codebase.

---

## 1. Approve Now (Next 6–12 Months)

These changes solve demonstrated problems, require no architecture rewrite, and carry low risk.

### 1.1 Recommendation D – Graph confidence scoring for CI/EDE

**Why now:** Graph confidence is a read-time attribute. It requires no schema change, no canonical merge change, and no Company ID change. It can be toggled with a threshold configuration.

**Production evidence:**
- `BizEntity` already carries `attributes` and can store `confidence` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- `IdentityEvidence` already carries `confidence`, `reason`, `explanation` on every `ALIAS_OF` / `SAME_AS` edge (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- The prototype script `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\scripts\prototype_graph_confidence.py` demonstrates a working formula.
- CI/EDE can filter at query time without touching GraphDB internals.

**Implementation:**
1. Backfill `confidence` on existing `COMPANY` nodes using the prototype formula.
2. Update `bc_scraper_pg_importer` to compute and set `confidence` on new nodes.
3. Add `MIN_COMPANY_CONFIDENCE` config (default `0.0`).
4. Update CI/EDE queries to exclude nodes below threshold.
5. Raise threshold gradually while monitoring.

**Risk:** Threshold calibration. Mitigate by starting at `0.0` and raising incrementally.

### 1.2 Recommendation E – Improved person-name heuristic

**Why now:** The heuristic is localized, config-driven, and has no data migration risk. It directly reduces false positives in CI.

**Production evidence:**
- `is_probable_person_name` exists in `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_name_heuristics.py`.
- `is_generic_bucket_company_name` exists in `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\parsed_identity_canonical_merge.py`.
- `CompanyResolver` uses these heuristics to skip person names (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_resolution.py:49-294`).
- The impact is contained to CI filtering; no Company IDs change.

**Implementation:**
1. Add city/trade deny-lists as configuration files.
2. Build a regression test set of known person names vs. real companies.
3. Deploy new deny-lists behind a feature flag.
4. Validate against regression set before enabling.

**Risk:** New false negatives. Mitigate with regression tests.

### 1.3 REST API versioning and pagination

**Why now:** The REST transport is new and experimental. Fixing the contract now is cheap; fixing it later breaks consumers.

**Production evidence:**
- REST endpoints currently live at `/api/graph/...` with no version prefix (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`).
- `BizRepository.find` already accepts `limit` and `offset` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- The stress test identified missing versioning and pagination as the first bottleneck for customer apps.

**Implementation:**
1. Move endpoints to `/api/v1/graph/...`.
2. Add pagination to `GET /api/v1/graph/companies` and `GET /api/v1/graph/companies/search`.
3. Return `Link` headers or standard pagination metadata.
4. Keep old `/api/graph/...` as deprecated aliases for a transition period.

**Risk:** Minimal. Only affects transport layer.

### 1.4 Standardized company identity contract

**Why now:** New services need a stable definition of a company. Without it, they will read scraper SQL directly.

**Production evidence:**
- `CompanyIdentity` dataclass already defines the contract: `uid`, `display_name`, `aliases`, `external_ids`, `merge_candidates` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- `repo.company_identity(uid)` exposes this contract (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- `engine.company_identity(uid)` provides the API-layer dict (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\biz_query_engine.py`).

**Implementation:**
1. Document the JSON shape returned by `/api/v1/graph/companies/{uid}/identity`.
2. Add OpenAPI schema or Pydantic model for the response.
3. Publish the contract as the only supported cross-service representation.

**Risk:** Minimal. Documentation + schema only.

### 1.5 Evidence-hash contract as a shared service

**Why now:** NarratorCache uses `evidence_hash` to self-heal. As more consumers (agents, apps) cache results, inconsistent hash generation will cause stale or duplicate computations.

**Production evidence:**
- `NarratorCache` key includes `evidence_hash`, `engine_version`, `prompt_version`, and `strategic_context_hash` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- The Orchestrator writes strategic memory based on live tool observations and evidence (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\planner.py:377-465`).
- There is no central helper for generating `evidence_hash` from tool results.

**Implementation:**
1. Create a small utility in `tenderscope-kg` that deterministically hashes a list of tool results/evidence.
2. Use that utility in `voice-n8n-agent` for `NarratorCache` keys and strategic memory writes.
3. Make the hash algorithm versioned.

**Risk:** Low. Refactoring only; no behavior change.

### 1.6 Audit log for company identity changes

**Why now:** Canonical merges, role changes, and external ID attachments need to be observable and debuggable. The graph already stores evidence, but the event of the change itself is not logged.

**Production evidence:**
- `CompanyResolver` can create new companies and detect conflicts (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_resolution.py:49-294`).
- `repo.resolve_company_uid` can create new `COMPANY` nodes (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- `repo.attach_identifier` merges external IDs (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- No append-only log of these decisions exists.

**Implementation:**
1. Add an `identity_audit_log` table in tenderscope-kg with columns: `uid`, `action`, `previous_state`, `new_state`, `source`, `timestamp`.
2. Write to it from `resolve_company_uid`, `attach_identifier`, and future merge operations.
3. Expose read-only endpoint for debugging.

**Risk:** Low. Append-only log does not affect runtime logic.

### 1.7 Separate database connection configuration for voice memory

**Why now:** Session Memory, Strategic Memory, and NarratorCache currently share the same PostgreSQL instance. A runaway memory write or cache fill can starve core business queries.

**Production evidence:**
- `SessionMemory` uses PostgreSQL or in-memory fallback (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\memory.py:1-178`).
- `StrategicMemoryStore` appends timeline and semantic data (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\strategic_memory.py:330-480`).
- `NarratorCache` uses PostgreSQL (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- All three are configurable via environment variables but not isolated by default.

**Implementation:**
1. Add distinct `DATABASE_URL` env vars: `SESSION_MEMORY_DATABASE_URL`, `STRATEGIC_MEMORY_DATABASE_URL`, `NARRATOR_CACHE_DATABASE_URL`.
2. Default them to the main `DATABASE_URL` for backward compatibility.
3. Document the ability to split them operationally.

**Risk:** Low. No schema change; only config flexibility.

### 1.8 Health checks and dependency status for EngineSet

**Why now:** As the platform grows, Railway needs to know if tenderscope-kg is healthy. A single failing engine should be detectable without user-visible errors.

**Production evidence:**
- `EngineSet` aggregates all engines (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py:1-64`).
- REST server has `/api/graph/health` but no per-engine status.
- `KGServer` initializes repository, database, and all engines at startup (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\mcp_server.py:1250-1700`).

**Implementation:**
1. Add `/api/v1/graph/health` returning overall status.
2. Add `/api/v1/graph/ready` that checks repository connection and critical engine availability.
3. Log per-engine initialization status.

**Risk:** Minimal. Observability only.

### 1.9 Circuit breaker and timeout for Claude API in Narrator

**Why now:** Narrator depends on Claude API. If Claude is slow or down, user requests hang or fail entirely. This is the single biggest external dependency risk in the voice agent.

**Production evidence:**
- `Narrator.narrate` makes an external LLM call (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator.py:739-793`).
- `NarratorCache` reduces calls but does not eliminate them (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- No circuit breaker or fallback narration is present.

**Implementation:**
1. Add a timeout to `narrate` calls.
2. Add a circuit breaker that returns a graceful fallback message when Claude is unavailable.
3. Cache fallback responses separately so retries do not hammer the API.

**Risk:** Low. Defensive improvement.

---

## 2. Approve Later (Triggered by Growth)

These are valuable, but only at a specific production scale or condition. Implementing them now adds complexity without solving a current problem.

### 2.1 Event log / CDC for company-state changes

**Trigger:** When a second service needs to react to company changes in near real time (not just poll the graph).

**Why it becomes valuable:** Without events, every new service maintains its own polling or mapping table. This scales badly.

**Production evidence:**
- Graph importer is the only SQL→Graph sync mechanism today (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\importers\bc_scraper_pg_importer.py`).
- No pub/sub or change-data-capture exists.

### 2.2 Redis as shared cache backend

**Trigger:** When running more than one instance of tenderscope-kg or voice-n8n-agent.

**Why it becomes valuable:** In-process caches do not share state across instances, causing duplicate work and inconsistent responses.

**Production evidence:**
- `NarratorCache` uses in-memory dict + PostgreSQL (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- Reasoning/live caches appear to be in-memory.

### 2.3 Async worker queue for heavy computations

**Trigger:** When request latency from CI/EDE computations exceeds acceptable thresholds.

**Why it becomes valuable:** Keeps request handlers responsive while offloading expensive work.

**Production evidence:**
- CI cohort queries and EDE blending can be expensive on large datasets.
- Current code runs synchronously in request handlers.

### 2.4 Dedicated full-text search engine

**Trigger:** When PostgreSQL full-text search latency or relevance degrades with millions of company names.

**Why it becomes valuable:** Search quality directly affects user experience and agent accuracy.

**Production evidence:**
- `BizRepository.search_fts` uses database FTS (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- No dedicated search index exists.

### 2.5 Public API gateway

**Trigger:** When external developers or enterprise customers need stable public APIs.

**Why it becomes valuable:** Isolates external traffic, provides auth, rate limits, and usage analytics.

**Production evidence:**
- REST transport is internal/experimental today (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`).
- No auth, rate limiting, or versioning exists yet.

### 2.6 Agent identity and permissions for MCP

**Trigger:** When deploying a second AI agent with different access levels.

**Why it becomes valuable:** Without it, all agents share the same tool surface and memory context.

**Production evidence:**
- MCP server exposes all tools to any client (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\mcp_server.py:1250-1700`).
- No `agent_id` or RBAC in tool calls.

### 2.7 Graph partitioning / sharding

**Trigger:** When graph exceeds a few million nodes and single-instance PostgreSQL becomes the bottleneck.

**Why it becomes valuable:** Query latency and storage limits require horizontal scaling.

**Production evidence:**
- Graph uses a single backend selected by `open_repository` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`).
- No partitioning strategy exists.

### 2.8 Recommendation F – Migrate canonical authority to tenderscope-kg

**Trigger:** When A, B, C, D, and E are stable and the identity split becomes a measurable drag.

**Why it becomes valuable:** Single source of truth eliminates N×N mappings.

**Production evidence:**
- `company_uid` is already immutable and stable.
- `resolve_company_uid` is already the single safe entry point for identity (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).

---

## 3. Reject (Unnecessary, Premature, or High Complexity / Low Value)

### 3.1 Microservices decomposition now

**Why reject:** The current service boundaries (scraper, KG, voice agent) match real ownership and data boundaries. Splitting further now (e.g., separate CI service, separate EDE service) would add network latency, distributed tracing, and deployment coordination without solving a current bottleneck.

**Evidence:** CI and EDE share the same repository and evidence model. Separating them would require defining a new inter-service contract.

### 3.2 Event sourcing now

**Why reject:** Event sourcing is powerful but introduces significant complexity: event schemas, projections, replay, snapshotting. The current audit log proposal provides 80% of the value with 10% of the complexity.

**Evidence:** No current use case requires temporal queries or state reconstruction.

### 3.3 Replace SQLite/PostgreSQL GraphDB with a separate graph database now

**Why reject:** The BizRepository abstraction supports switching backends, but a dedicated graph database adds operational cost. PostgreSQL with proper indexing can scale to tens of millions of nodes before a dedicated graph DB is justified.

**Evidence:** Current backend selection is already abstracted (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`).

### 3.4 Kubernetes migration now

**Why reject:** Railway provides managed deployment sufficient for current and medium-term scale. Kubernetes adds operational burden without a demonstrated deployment limitation.

**Evidence:** Current deployment is on Railway; no portability or multi-cloud requirement exists.

### 3.5 Multi-tenant schema isolation now

**Why reject:** No enterprise multi-tenant requirement is demonstrated. Adding tenant-aware schemas now would complicate every query and migration.

**Evidence:** No tenant ID or organization model exists in the current domain.

### 3.6 Real-time streaming infrastructure now

**Why reject:** There is no streaming use case today. Batch imports and request-time queries are sufficient.

**Evidence:** Pipeline runs are batch (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\run.py:1-64`).

### 3.7 Replace SQLAlchemy with a different ORM now

**Why reject:** SQLAlchemy is not a demonstrated bottleneck. Rewriting all models and queries would be high risk, low reward.

**Evidence:** Repository abstraction already isolates domain models from SQLAlchemy details.

### 3.8 Service mesh now

**Why reject:** Three services do not need a service mesh. mTLS, traffic shaping, and observability can be achieved with simpler tools when needed.

**Evidence:** Internal communication is currently via Railway network and MCP/REST.

### 3.9 Recommendation B – New entity roles now

**Why reject (for now):** The role model is useful, but introducing six roles before validating the validation gate (Recommendation A) creates query churn. Roles should follow from confidence and identity improvements, not precede them.

**Evidence:** Current queries and CI filters rely on existing role values (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\db\company_canonical_constants.py`).

---

## 4. Architectural Foundation (Keep Stable for 5+ Years)

These components have already proven their value and should remain the permanent foundation. They can be extended but not replaced without a major rewrite.

### 4.1 EngineSet and transport-agnostic business logic

**Why permanent:** It is the core architectural decision that lets MCP, REST, and future transports share one business logic layer. This prevents duplication and drift.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py:1-64`

### 4.2 Company identity model

**Why permanent:** The triple `company_uid` (immutable) + `canonical_name` (write-once dedup key) + `name` (mutable display) correctly separates identity from metadata.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`

### 4.3 GraphDB as the business graph

**Why permanent:** Entities, relations, and evidence provide a unified model for company identity, aliases, and business activity. This is the right abstraction for CI, EDE, and future engines.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\db.py:1-120`, `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\kinds.py:1-180`

### 4.4 BizRepository abstraction

**Why permanent:** It decouples business logic from SQLite/PostgreSQL specifics. This is essential for multi-database evolution.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:1-299`, `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`

### 4.5 Identity resolution pipeline

**Why permanent:** `repo.resolve_company_uid(name, source, attributes)` is the single safe entry point. It ensures canonical lookup, alias resolution, and new entity creation are centralized.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`

### 4.6 IdentityEvidence on relations

**Why permanent:** Auditable confidence, reason, and explanation on every `ALIAS_OF` / `SAME_AS` edge is the right model for deterministic, explainable company matching.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`

### 4.7 Evidence-based cache invalidation

**Why permanent:** Hashing evidence rather than timestamps or IDs ensures cache entries self-heal when underlying data changes. This is critical as more consumers cache results.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`

### 4.8 Feature flags for memory and cache

**Why permanent:** Strategic memory and cache features are rolled out behind flags. This pattern should continue for any new memory, cache, or agent behavior.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\core\config.py:150-228`, `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\planner.py:377-465`

### 4.9 Domain models (BizEntity, BizRelation, CompanyIdentity)

**Why permanent:** These dataclasses are the stable vocabulary of the platform. New fields can be added to `attributes`, but the core shapes should remain.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`

### 4.10 Dual MCP + REST transport pattern

**Why permanent:** MCP is optimal for AI agents; REST is optimal for customer apps and integrations. Keeping both thin and backed by the same EngineSet is the right long-term shape.

**Evidence:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\mcp_server.py:1250-1700`, `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`

---

## 5. Implementation Order for the Next 12 Months

1. **Q1:** REST API versioning + pagination; standardized company identity contract.
2. **Q2:** Recommendation D pilot (graph confidence with threshold at 0, then gradual raise).
3. **Q3:** Recommendation E (person-name heuristic improvement) + evidence-hash shared utility.
4. **Q4:** Audit log for identity changes + separate memory DB config + health checks + Claude circuit breaker.

This sequence keeps risk low, builds on existing foundations, and prepares the platform for the deferred items in Section 2.

---

## 6. Summary Table

| Recommendation | Section | Risk | Why |
|---|---|---|---|
| D. Graph confidence | Approve Now | Low | Read-time attribute; no schema change |
| E. Person-name heuristic | Approve Now | Low | Local config change; no migration |
| REST API versioning | Approve Now | Low | Transport-only |
| Company identity contract | Approve Now | Low | Documentation + schema |
| Evidence-hash utility | Approve Now | Low | Refactoring only |
| Audit log for identity | Approve Now | Low | Append-only |
| Separate memory DB config | Approve Now | Low | Config only |
| Health checks | Approve Now | Low | Observability |
| Claude circuit breaker | Approve Now | Low | Defensive |
| Event log / CDC | Approve Later | Medium | Triggered by second service |
| Redis shared cache | Approve Later | Medium | Triggered by multi-instance |
| Async worker queue | Approve Later | Medium | Triggered by latency |
| Dedicated search engine | Approve Later | Medium | Triggered by FTS degradation |
| Public API gateway | Approve Later | Medium | Triggered by external developers |
| Agent identity RBAC | Approve Later | Medium | Triggered by second agent |
| Graph partitioning | Approve Later | High | Triggered by dataset size |
| F. Authority migration | Approve Later | Very high | Triggered by stability of A–E |
| Microservices now | Reject | High | Premature |
| Event sourcing now | Reject | High | Complexity > value |
| Separate graph DB now | Reject | Medium | No demonstrated bottleneck |
| Kubernetes now | Reject | High | No Railway limitation |
| Multi-tenant schemas now | Reject | High | No requirement |
| Streaming infrastructure now | Reject | High | No use case |
| Replace SQLAlchemy now | Reject | High | No bottleneck |
| Service mesh now | Reject | High | Overkill |
| B. New entity roles now | Reject | Medium | Wait for A validation |

No architectural rewrites. No fashionable patterns. Every item is incremental and tied to current code or a demonstrated future bottleneck.
