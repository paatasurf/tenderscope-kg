# Architectural Stress Test

**Scope:** Evaluate whether the current TenderScope architecture can evolve naturally over 3–5 years without a major rewrite.  
**Constraint:** Do not redesign the platform. Only recommend changes that solve demonstrated scalability or maintainability problems.  
**Status:** Review only. No implementation.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Executive Summary

The current architecture **can evolve incrementally** for 3–5 years, but only if three boundaries are respected:

1. **Identity boundary:** `company_uid` from tenderscope-kg must become the universal company identifier for all new services. `Company.id` from bc-tender-scraper should be treated as a legacy attribute, not a cross-service key.
2. **Logic boundary:** Business logic must stay inside `EngineSet`; transports, customer apps, and integrations must remain thin.
3. **Data boundary:** New services must not read bc-tender-scraper SQL tables directly. They must consume the graph or a stable REST/MCP API.

If these boundaries are violated, adding 10–20 services will create an unmaintainable mesh of direct SQL dependencies and N×N mapping tables.

**Overall verdict:** The platform is viable for organic growth, but two capabilities should be added early: **(a) an event log for company-state changes**, and **(b) an external cache backend** (Redis) shared across instances. Everything else can wait until a concrete bottleneck appears.

---

## 2. Current Platform Boundaries

```text
┌─────────────────────────────────────────────────────────────────────────────┐
│                         External consumers                                  │
│  Telegram Bot ── n8n ── Customer apps ── Public API ── Enterprise systems │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ MCP / REST
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         tenderscope-kg                                      │
│  EngineSet (CIE, RIE, CeI, BIE, OIE, EDE)                                    │
│  GraphDB (COMPANY, ALIAS, SAME_AS, evidence)                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      │ import / external IDs
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                      bc-tender-scraper                                        │
│  Permit / Award / Builder tables                                            │
│  Company Resolution + Canonical Merge + entity_role                         │
└─────────────────────────────────────────────────────────────────────────────┘
```

The strongest boundary today is between **EngineSet** and **transports**. The weakest boundary is between **scraper SQL schema** and the rest of the platform.

---

## 3. Scenario Analysis

### 3.1 Adding 10–20 new services

#### What already scales well
- **`company_uid` model** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`) is immutable and stable; new services can reference it without caring about merge history.
- **GraphDB** provides a unified, queryable company model. New services can read entities, relations, and evidence without learning scraper SQL.
- **EngineSet pattern** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py:1-64`) keeps business logic transport-agnostic; new transports can be added without touching engines.
- **External identifiers as attributes** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\kinds.py:1-180`) mean new identifiers require no schema migration.

#### What becomes a bottleneck
- **Identity authority split:** bc-tender-scraper owns `Company.id` and `entity_role`; tenderscope-kg owns `company_uid`. New services will have to choose or maintain two mappings.
- **Graph importer is a single sync point:** All SQL → graph flow goes through `bc_scraper_pg_importer`. If a new service writes company data to PostgreSQL but does not trigger the importer, the graph becomes stale.
- **No event bus:** Services cannot observe company changes without polling SQL or graph.
- **Scraper SQL schema becomes integration contract:** Any table change risks breaking services that read it directly.

#### What should remain unchanged
- Immutable `company_uid` and write-once `canonical_name` semantics.
- EngineSet as the single business-logic layer.
- Transport-agnostic domain models (`BizEntity`, `BizRelation`, `IdentityEvidence`).

#### What should be generalized
- A **stable company identity contract** published via REST/MCP, not SQL schema.
- An **event log / change data capture** for company-state changes.
- Generic **memory store interfaces** so new services can plug in their own storage.

#### What should never be coupled
- New services to bc-tender-scraper SQL tables.
- New services to voice-n8n-agent orchestrator internals.
- New services to specific transport implementation details.

#### Recommendation
Add an event log for company-state changes **only after** the second or third new service needs to observe changes. Do not build it prematurely. Prior to that, require all new services to consume tenderscope-kg REST/MCP and use `company_uid`.

---

### 3.2 Multiple AI agents

#### What already scales well
- **MCP transport** is designed for AI agents. New agents can use the same tool surface.
- **EngineSet exposes tools uniformly** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\mcp_server.py:1250-1700`).
- **Evidence-backed responses** (`IdentityEvidence`) make multi-agent outputs auditable.
- `company_uid` gives agents a stable entity reference.

#### What becomes a bottleneck
- **Single EngineSet instance:** All agents share the same engines. If agents need different permissions (e.g., read-only vs. write), there is no access control layer today.
- **No agent identity:** There is no `agent_id` in tool calls or session memory.
- **Session Memory is per-session, not per-agent:** An agent working on behalf of a user has no isolated context.
- **NarratorCache keys** include `evidence_hash` and versions, but no `agent_id`. Different agents might need different narrations for the same evidence.
- **Strategic Memory writes** are fire-and-forget; with multiple agents, race conditions or conflicting writes become possible.

#### What should remain unchanged
- Tool exposure pattern through EngineSet.
- Evidence and confidence model.
- `company_uid` as the canonical company reference.

#### What should be generalized
- **Agent identity and permissions** in transport layer.
- **Agent-specific memory/context** isolation.
- **NarratorCache key** to include `agent_id` or `agent_persona`.

#### What should never be coupled
- Agent-specific prompts to EngineSet business logic.
- One agent's memory to another agent's session.
- Agent access control to engine internals.

#### Recommendation
Introduce an `agent_id` and agent-level permissions **when the second AI agent is deployed**, not before. Until then, the current MCP model is sufficient.

---

### 3.3 Multiple customer-facing applications

#### What already scales well
- **REST transport** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`) is thin and can serve multiple apps.
- **EngineSet provides unified backend**; each app sees the same data.
- **Feature flags** in voice-n8n-agent (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\core\config.py:150-228`) show how to roll out behavior gradually per app.

#### What becomes a bottleneck
- **No API versioning:** Current REST endpoints are experimental. Customer apps need stability guarantees.
- **No authentication/authorization:** Customer apps cannot safely share a public transport without auth.
- **No rate limiting:** One heavy app can starve others.
- **Response focus mismatch:** Different apps may need different narration styles or data shapes. Pushing this into engines violates transport-agnostic design.
- **No app-specific observability:** It will be hard to attribute load or errors to a specific app.

#### What should remain unchanged
- Transport-only REST layer.
- Shared EngineSet and `company_uid` identity.
- Domain models.

#### What should be generalized
- **API versioning** from day one (`/api/v1/graph/...`).
- **Authentication and rate limiting** at the transport layer.
- **App-specific response formatting** as thin wrappers, not engine changes.
- **Per-app metrics and logging**.

#### What should never be coupled
- Customer app UI to EngineSet internals.
- App-specific auth into business logic.
- One app's cache keys to another app's data.

#### Recommendation
Version the REST API **before the first external customer app is integrated**. Add auth and rate limiting at the same time. Do not change engine logic for app-specific formatting.

---

### 3.4 Public APIs

#### What already scales well
- EngineSet provides a stable backend.
- REST transport can be exposed publicly.
- Immutable `company_uid` gives external consumers a stable identifier.

#### What becomes a bottleneck
- **No versioning or deprecation policy:** Breaking changes are hard to manage.
- **No usage quotas or SLAs:** External traffic can overwhelm internal workloads.
- **No request logging / audit trail** for external API calls.
- **Schema leakage:** Public API may expose internal `entity_role` values that are still evolving.
- **Rate limiting and abuse protection missing.**

#### What should remain unchanged
- Business logic in EngineSet.
- `company_uid` as public identifier.
- Evidence model.

#### What should be generalized
- **API versioning and deprecation policy.**
- **Authentication, rate limiting, quotas.**
- **Public API documentation and SDKs.**
- **Separate public API gateway** to isolate external traffic from internal MCP/REST.

#### What should never be coupled
- Public API schema to internal SQL schema.
- Public API to MCP tool names.
- Breaking changes to existing consumers.

#### Recommendation
Deploy a dedicated public API gateway **when the first external developer is onboarded**. Do not expose the internal REST transport directly.

---

### 3.5 Enterprise integrations

#### What already scales well
- **n8n workflows** already support integrations.
- **REST transport** can be consumed by enterprise systems.
- **External ID model** (`EXTERNAL_ID_KEYS`) allows linking to enterprise identifiers without schema changes.

#### What becomes a bottleneck
- **No webhook / event subscription model:** Enterprise systems must poll.
- **No bulk export / sync API:** Large enterprise datasets cannot be exported efficiently.
- **No audit log for identity changes:** Enterprises need compliance trails.
- **No enterprise identity federation** (SAML/OAuth/SCIM).
- **No data residency controls:** Multi-region enterprise customers cannot be supported.

#### What should remain unchanged
- Evidence-based entity model.
- Immutable UIDs.
- External ID attribute model.

#### What should be generalized
- **Webhook / event subscription** for company-state changes.
- **Bulk export API** (paginated, async).
- **Audit logging** for identity and relation changes.
- **Enterprise auth and data residency controls**.

#### What should never be coupled
- Enterprise system schemas to internal models.
- Integration logic to core engines.
- Customer-specific transformations to business logic.

#### Recommendation
Add webhooks and audit logging **when the first enterprise integration requires them**. Do not build bulk export until a concrete customer asks for it.

---

### 3.6 Multiple databases

#### What already scales well
- **BizRepository abstraction** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:1-299`) supports SQLite and PostgreSQL backends.
- **`open_repository` factory** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`) selects backend by environment variable.
- **Domain models are storage-agnostic.**

#### What becomes a bottleneck
- **No distributed transactions:** GraphDB and scraper DB are separate. A canonical merge in scraper and a graph import are not atomic.
- **All memory stores may share one database:** Session Memory, Strategic Memory, NarratorCache, and business data could compete for the same PostgreSQL instance.
- **No read-replica strategy.**
- **No sharding strategy** for the graph if it grows to hundreds of millions of nodes.
- **Connection pooling not visible** in current code.

#### What should remain unchanged
- BizRepository abstraction.
- `open_repository` factory.
- Domain models decoupled from storage.

#### What should be generalized
- **Database-per-service boundary** eventually: scraper DB, graph DB, memory DB.
- **Read replica configuration** for graph and scraper reads.
- **Connection pooling and health checks.**
- **Point-in-time backup per database.**

#### What should never be coupled
- Business logic to a specific SQL dialect.
- One service's DB schema to another service's queries.
- Direct cross-DB joins (must go through API or graph).

#### Recommendation
Keep BizRepository. Split databases by service **only when operational metrics show contention**, not before. Introduce read replicas **when query latency degrades**.

---

### 3.7 Significantly larger datasets

#### What already scales well
- PostgreSQL backend for graph.
- Batch imports in `bc_scraper_pg_importer`.
- Immutable UIDs avoid reprocessing.
- Caching reduces repeated computation.

#### What becomes a bottleneck
- **Graph traversal performance:** `get_neighbors` on dense companies (e.g., large general contractors with thousands of relations) may degrade.
- **Full graph scans** (like those in prototype scripts) will not scale to millions of nodes.
- **Full-text search** via SQLite FTS or PostgreSQL tsvector may become slow or inaccurate at scale.
- **NarratorCache with 7-day TTL** will grow indefinitely unless evicted.
- **CI cohort queries** on SQL may become slow as company/permit tables grow.
- **EDE blending** may become expensive if it pulls many relations per request.

#### What should remain unchanged
- Batch import pattern.
- Immutable identity model.
- Repository abstraction.

#### What should be generalized
- **Graph indexing strategy** (materialized views, adjacency indexes).
- **Pagination on all list/search queries.**
- **Dedicated full-text search engine** (e.g., Meilisearch or Elasticsearch) when PostgreSQL FTS becomes insufficient.
- **Cache TTL and eviction policies** by dataset size.
- **Async / background processing** for heavy CI and EDE computations.

#### What should never be coupled
- Dataset-size assumptions to query logic.
- Batch processing to synchronous API calls.
- Analytics to real-time request path.

#### Recommendation
Add pagination to all list endpoints **now**, before dataset grows. Introduce a dedicated search engine **only when** PostgreSQL FTS latency exceeds acceptable thresholds. Move heavy CI/EDE computations to background workers **when request latency degrades**.

---

### 3.8 Higher request volume

#### What already scales well
- **Stateless REST transport** can be horizontally scaled.
- **NarratorCache** reduces expensive Claude API calls.
- **EngineSet** can run behind a load balancer if it is stateless.

#### What becomes a bottleneck
- **Claude API is external and rate-limited.** Narration is the most expensive and slowest operation.
- **Synchronous DB connections** may exhaust pools under load.
- **In-process caches** do not share state across instances; cache hit rate drops with horizontal scaling.
- **NarratorCache PostgreSQL table** may become a hot table.
- **No async worker queue** for long-running CI/EDE computations.
- **Session Memory PostgreSQL** may become contended.

#### What should remain unchanged
- Stateless transport layer.
- Caching strategy.
- Evidence-based model.

#### What should be generalized
- **Shared cache backend** (Redis) for multi-instance deployments.
- **Async worker queue** (Celery, RQ, or Railway background workers) for CI/EDE heavy tasks.
- **Connection pooling** and circuit breakers.
- **Rate limiting and backpressure** for external APIs.
- **Horizontal scaling of transport layer**.

#### What should never be coupled
- Request handler lifecycle to long-running computation.
- Per-instance in-memory cache to correctness.
- External API availability to core data reads.

#### Recommendation
Replace in-process caches with Redis **when you run more than one instance**. Add an async worker queue **when request latency from heavy computations becomes unacceptable**. Claude API rate limits should be managed with a circuit breaker and fallback narration.

---

## 4. Cross-Cutting Risks

| Risk | When it appears | Mitigation |
|---|---|---|
| Identity authority split | 2nd or 3rd new service | Adopt `company_uid` as universal ID; treat scraper `Company.id` as legacy. |
| SQL schema as integration contract | 1st service that reads scraper directly | Enforce REST/MCP consumption; never grant SQL access to new services. |
| In-process cache inconsistency | 2nd running instance | Move to Redis. |
| Public API instability | 1st external customer app | Version the API and add gateway. |
| Graph query latency | Dataset > few million nodes | Add indexes, pagination, and possibly separate search engine. |
| Claude API bottleneck | High request volume | Circuit breaker + fallback narration + cache warming. |
| Multi-agent collision | 2nd AI agent | Add agent identity and isolated contexts. |

---

## 5. What to Do Now, Soon, and Later

### Now (before growth accelerates)
1. **Add pagination** to all REST/MCP list and search endpoints.
2. **Version the REST API** (`/api/v1/graph/...`).
3. **Document the company identity contract**: `company_uid`, `canonical_name`, `display_name`, `entity_role`, `confidence`, `external_ids`.
4. **Require all new services to use `company_uid`** and consume tenderscope-kg APIs.

### Soon (within 12 months, when second service/agent/app appears)
1. **Add an event log / CDC** for company-state changes.
2. **Add Redis as shared cache backend** when running multiple instances.
3. **Add agent identity and permissions** for MCP layer.
4. **Add authentication and rate limiting** to REST transport.

### Later (only when concrete bottleneck appears)
1. **Split databases by service** if operational metrics show contention.
2. **Add async worker queue** when request latency degrades.
3. **Add dedicated search engine** when PostgreSQL FTS is insufficient.
4. **Add public API gateway** when external developers onboard.
5. **Reconsider Recommendation F** (authority migration to KG) when A–E are stable.

---

## 6. Conclusion

The current TenderScope architecture **can evolve incrementally** for 3–5 years, provided that:

- `company_uid` becomes the universal cross-service identifier.
- New services consume the platform via REST/MCP, not scraper SQL.
- Business logic stays in EngineSet; transports and apps stay thin.
- Caching and memory are moved to shared backends as the deployment grows beyond a single instance.

No major rewrite is required if these boundaries are enforced. The two most important near-term investments are **REST API versioning** and **company identity contract documentation**. Everything else can be added reactively as real bottlenecks appear.
