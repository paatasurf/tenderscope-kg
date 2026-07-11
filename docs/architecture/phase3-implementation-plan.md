# Phase 3 — Master Implementation Program

**Objective:** Convert all approved architectural recommendations into a concrete, executable 6–12 month implementation plan.  
**Constraint:** No code, no migrations, no PRs, no speculative technologies.  
**Status:** Planning only.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Executive Summary

This plan consolidates the outputs of four prior reviews:

- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\phase2-canonical-entity-graph-review.md`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architecture-readiness-review.md`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architectural-stress-test.md`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\final-architectural-roadmap.md`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\integration-compatibility-validation.md`

**Approved recommendations in scope:**

| ID | Recommendation | Verdict | Complexity |
|---|---|---|---|
| D | Graph confidence scoring for CI/EDE | Approve (pilot) | Low–Medium |
| E | Improved person-name heuristic | Approve | Low |
| G | REST API versioning + pagination | Approve | Low |
| H | Standardized company identity contract | Approve | Low |
| I | Evidence-hash utility | Approve | Low |
| J | Audit log for identity changes | Approve | Low |
| K | Separate memory database configuration | Approve | Low |
| L | Health checks for EngineSet | Approve | Low |
| M | Circuit breaker for Claude API in Narrator | Approve | Low |

**Deferred recommendations (gated):**

| ID | Recommendation | Gate |
|---|---|---|
| A | Validation gate before canonical promotion | Phase 2.1 prototype numbers + human review |
| B | New entity roles | After A is stable and queries updated |
| C | External identifier disambiguation | External data source secured |
| F | Authority migration to tenderscope-kg | After A–E stable + full migration design |

**Master schedule:**

- **Phase 1:** Foundation & safety (G, H, K, L, M) — deployable independently.
- **Phase 2:** Data quality improvements (D, E) — deployable after Phase 1.
- **Phase 3:** Observability & shared utilities (I, J) — deployable after Phase 1.
- **Phase 4:** Canonical model modernization (A, B) — only if Phase 2.1 validation passes and human approval is granted.

---

## 2. Approved Recommendations — Detailed Plans

### D. Graph confidence scoring for CI/EDE

**Implementation order:**
1. Backfill `confidence` on existing `COMPANY` nodes using the prototype formula.
2. Update `bc_scraper_pg_importer` to compute and write `confidence` for new nodes.
3. Add `MIN_COMPANY_CONFIDENCE` configuration (default `0.0`).
4. Update CI and EDE engines to filter or down-weight nodes below threshold.
5. Deploy with threshold at `0.0`; raise gradually.

**Technical dependencies:**
- `BizEntity` attribute model (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- `IdentityEvidence` on relations (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- `EXTERNAL_ID_KEYS` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\kinds.py:1-180`).
- `CompetitiveIntelligenceEngine` and `ExecutiveDecisionEngine` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\executive_decision.py:1-661`).

**Repositories affected:**
- `tenderscope-kg` (graph importer, engines, config).
- `bc-tender-scraper` (importer, if confidence computation is moved there later).

**Estimated complexity:** Low–Medium.

**Regression risk:** Low. No identifier changes; no schema changes; threshold defaults to `0.0`.

**Rollback strategy:** Set `MIN_COMPANY_CONFIDENCE=0.0`. No data revert required.

**Required automated tests:**
- Unit test for confidence formula on sample companies.
- Test that CI output with threshold `0.0` matches pre-change output.
- Test that CI output with threshold `1.0` excludes low-confidence nodes.
- Test that `company_identity` includes `confidence` field.

**Production validation checklist:**
- [ ] Run `prototype_graph_confidence.py` against production snapshot.
- [ ] Verify p50, p90, p95, p99 percentiles.
- [ ] Confirm threshold `0.0` produces identical CI results.
- [ ] Confirm chosen threshold keeps >80% of real companies.
- [ ] Manual review of 20 excluded companies.
- [ ] Monitor narrator cache hit rate for 48 hours.

**Monitoring after deployment:**
- CI competitor count per sample company.
- EDE priority/risk distribution.
- Narrator cache hit rate.
- Claude API costs (if EDE changes reduce calls).

**Success criteria:**
- Graph confidence backfilled for 100% of `COMPANY` nodes.
- CI output with threshold `0.0` is byte-for-byte identical to pre-change baseline.
- Chosen threshold improves CI precision without losing >5% of real companies.
- No API errors.

**Failure criteria:**
- >5% real companies excluded at chosen threshold.
- CI output breaks for known sample companies.
- Cache hit rate drops >10%.

---

### E. Improved person-name heuristic

**Implementation order:**
1. Build a labelled regression test set (known persons vs. real companies).
2. Add city/trade deny-lists as configuration files.
3. Update `is_probable_person_name` to consult deny-lists.
4. Add feature flag `PERSON_NAME_HEURISTIC_V2`.
5. Deploy flag disabled; run on sample; validate.
6. Enable flag gradually.

**Technical dependencies:**
- `company_name_heuristics.py` (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_name_heuristics.py`).
- `CompanyResolver` (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_resolution.py:49-294`).
- CI filter logic (`@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\db\company_analytics.py`).

**Repositories affected:**
- `bc-tender-scraper`.

**Estimated complexity:** Low.

**Regression risk:** Low. Config-only; no ID changes.

**Rollback strategy:** Revert `PERSON_NAME_HEURISTIC_V2` flag to false or revert config files.

**Required automated tests:**
- Regression tests: each known person name must still be flagged as person.
- Regression tests: each known real company must not be flagged as person.
- CI output comparison with flag on vs. off.

**Production validation checklist:**
- [ ] Run heuristic on production sample of 1,000 company names.
- [ ] Compare classifications against baseline.
- [ ] Verify no new false negatives in top 100 active companies.
- [ ] Verify CI competitor count changes only for expected samples.

**Monitoring after deployment:**
- Person-name classification rate.
- CI false positive rate (via manual sample).
- User feedback on Telegram.

**Success criteria:**
- Fewer generic-bucket / person-name competitors in CI.
- No new false negatives in regression set.
- <0.1% of active companies reclassified as person.

**Failure criteria:**
- Any verified real company is reclassified as person.
- CI competitor count drops unexpectedly for important companies.

---

### G. REST API versioning and pagination

**Implementation order:**
1. Add `/api/v1/graph/...` endpoints with pagination.
2. Keep `/api/graph/...` as deprecated aliases.
3. Add `Link` headers or standard pagination metadata.
4. Update OpenAPI schema / Pydantic models.
5. Mark old endpoints deprecated; schedule removal.
6. Notify MCP clients that tool paths are unchanged.

**Technical dependencies:**
- `rest_server.py` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`).
- `BizRepository.find` supports `limit` and `offset` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).

**Repositories affected:**
- `tenderscope-kg`.

**Estimated complexity:** Low.

**Regression risk:** Low if old endpoints remain.

**Rollback strategy:** Revert `rest_server.py` changes; keep old endpoints active.

**Required automated tests:**
- Test old `/api/graph/...` endpoints still return 200.
- Test new `/api/v1/graph/...` endpoints return same data with pagination metadata.
- Test pagination boundaries (limit, offset, last page).
- Test MCP tools unchanged.

**Production validation checklist:**
- [ ] Smoke test all old endpoints.
- [ ] Smoke test all new endpoints.
- [ ] Verify pagination through full company list on a snapshot.
- [ ] Confirm bc-tender-scraper REST client can migrate to v1.

**Monitoring after deployment:**
- HTTP 404/500 rate on old and new endpoints.
- Request latency by endpoint.
- Pagination metadata correctness.

**Success criteria:**
- Old endpoints serve 200s with deprecation headers.
- New endpoints serve correct paginated data.
- No MCP breakage.
- Customer app integration tests pass.

**Failure criteria:**
- Any endpoint returns 500.
- Pagination produces duplicate or missing items.
- MCP tool contract changes.

---

### H. Standardized company identity contract

**Implementation order:**
1. Define Pydantic model for company identity response.
2. Document `company_identity` response shape in markdown.
3. Add contract tests to CI.
4. Publish contract as part of REST API docs.
5. Ensure `CompanyIdentity` dataclass and API response match exactly.

**Technical dependencies:**
- `CompanyIdentity` dataclass (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py:1-217`).
- `repo.company_identity(uid)` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- `engine.company_identity(uid)` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\biz_query_engine.py`).

**Repositories affected:**
- `tenderscope-kg` (docs + schema).
- `docs/architecture/` (contract document).

**Estimated complexity:** Low.

**Regression risk:** Low.

**Rollback strategy:** Revert documentation and schema changes.

**Required automated tests:**
- Contract test: response JSON matches documented schema.
- Test that all documented fields are present.
- Test that extra fields are additive only.

**Production validation checklist:**
- [ ] Verify `/api/v1/graph/companies/{uid}/identity` matches contract.
- [ ] Verify MCP `company_identity` tool matches contract.
- [ ] Check that no consumer relies on undocumented fields.

**Monitoring after deployment:**
- No specific monitoring needed beyond API error rates.

**Success criteria:**
- Contract document published.
- All consumers pass contract tests.
- No breaking field changes.

**Failure criteria:**
- Contract tests fail.
- Consumer breaks due to field shape change.

---

### I. Evidence-hash utility

**Implementation order:**
1. Create a deterministic hash function in `tenderscope-kg` for tool-result evidence.
2. Add version byte to the hash input.
3. Replace ad-hoc hash generation in `voice-n8n-agent` with the utility.
4. Add feature flag `EVIDENCE_HASH_V2` to switch implementations.
5. Validate that old and new hashes match for identical inputs during transition.

**Technical dependencies:**
- `NarratorCache` key generation (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- Strategic memory context hashing (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\orchestrator.py:790-903`).
- Evidence shapes produced by engines.

**Repositories affected:**
- `tenderscope-kg` (new utility).
- `voice-n8n-agent` (adopt utility).

**Estimated complexity:** Low.

**Regression risk:** Low if versioned and tested.

**Rollback strategy:** Revert to `EVIDENCE_HASH_V1` flag.

**Required automated tests:**
- Test that identical evidence produces identical hash.
- Test that changing any evidence field changes hash.
- Test cross-version compatibility.
- Test NarratorCache still hits after transition.

**Production validation checklist:**
- [ ] Run old and new hash side-by-side on 1,000 sample evidence objects.
- [ ] Verify zero mismatches.
- [ ] Monitor cache hit rate for 48 hours after switch.

**Monitoring after deployment:**
- NarratorCache hit rate.
- Cache miss rate by evidence shape.
- Strategic memory context hash collisions.

**Success criteria:**
- Hash outputs deterministic and collision-resistant.
- Cache hit rate unchanged.
- No cache thrash after switch.

**Failure criteria:**
- Hash mismatches between old and new utility.
- Cache hit rate drops >10%.
- Duplicate or stale narrations observed.

---

### J. Audit log for identity changes

**Implementation order:**
1. Design `identity_audit_log` table (append-only).
2. Add write calls to `resolve_company_uid`, `attach_identifier`, and future merge operations.
3. Add read-only REST/MCP endpoint for audit log lookup.
4. Add retention policy (e.g., 2 years).
5. Load test append-only writes.

**Technical dependencies:**
- `repo.resolve_company_uid()` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- `repo.attach_identifier()` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:180-299`).
- `bc_scraper_pg_importer` company import logic.

**Repositories affected:**
- `tenderscope-kg`.

**Estimated complexity:** Low.

**Regression risk:** Low. Writes are append-only and do not affect runtime logic.

**Rollback strategy:** Disable audit writes; optionally drop table.

**Required automated tests:**
- Test that creating a company writes an audit row.
- Test that attaching an identifier writes an audit row.
- Test that reading audit log returns correct entries.
- Test that audit writes do not fail primary operation on error.

**Production validation checklist:**
- [ ] Verify audit rows after a batch import.
- [ ] Verify audit log endpoint returns correct data.
- [ ] Confirm no measurable latency increase on identity operations.
- [ ] Confirm retention policy works.

**Monitoring after deployment:**
- Audit log write rate.
- Storage growth of audit table.
- Latency of identity operations (p95).

**Success criteria:**
- 100% of identity operations logged.
- Audit endpoint returns correct history.
- Latency increase <5%.
- Storage growth predictable.

**Failure criteria:**
- Audit writes fail silently.
- Identity operation latency increases >10%.
- Audit table grows unexpectedly large.

---

### K. Separate memory database configuration

**Implementation order:**
1. Add environment variables: `SESSION_MEMORY_DATABASE_URL`, `STRATEGIC_MEMORY_DATABASE_URL`, `NARRATOR_CACHE_DATABASE_URL`.
2. Default each to the main `DATABASE_URL` for backward compatibility.
3. Update `SessionMemory`, `StrategicMemoryStore`, and `NarratorCache` initializers to accept optional connection string.
4. Update `deps.py` to pass correct URLs.
5. Document operational split in deployment guide.

**Technical dependencies:**
- `SessionMemory` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\memory.py:1-178`).
- `StrategicMemoryStore` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\strategic_memory.py:330-480`).
- `NarratorCache` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- `deps.py` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\deps.py:95-216`).

**Repositories affected:**
- `voice-n8n-agent`.

**Estimated complexity:** Low.

**Regression risk:** Low. Default behavior unchanged.

**Rollback strategy:** Revert to shared `DATABASE_URL`.

**Required automated tests:**
- Test that each component connects to the configured URL.
- Test fallback to main `DATABASE_URL` when specific URL not set.
- Test session restore after restart.
- Test strategic memory write/read.
- Test narrator cache hit/miss.

**Production validation checklist:**
- [ ] Set distinct URLs in staging.
- [ ] Verify each component creates tables in correct database.
- [ ] Verify in-memory fallback still works if DB unavailable.
- [ ] Run load test and confirm no cross-DB contention.

**Monitoring after deployment:**
- Connection pool health per DB.
- Query latency per DB.
- Memory/cache hit rates.

**Success criteria:**
- Each memory component uses its configured database.
- Main business DB is not starved by memory/cache traffic.
- Fallback behavior preserved.

**Failure criteria:**
- Component writes to wrong database.
- Connection pool exhaustion.
- Fallback behavior broken.

---

### L. Health checks for EngineSet

**Implementation order:**
1. Add `/api/v1/graph/health` returning liveness status.
2. Add `/api/v1/graph/ready` returning readiness: repository connected, each engine initialized.
3. Add per-engine `health()` method returning `ok` / `degraded` / `down`.
4. Configure Railway health checks to use `/ready`.
5. Add health metrics to logs.

**Technical dependencies:**
- `EngineSet` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py:1-64`).
- `open_repository()` and connection checking (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\__init__.py:1-100`).
- `rest_server.py` (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\rest_server.py`).

**Repositories affected:**
- `tenderscope-kg`.

**Estimated complexity:** Low.

**Regression risk:** Low.

**Rollback strategy:** Remove endpoint; revert Railway health check URL.

**Required automated tests:**
- Test `/health` returns 200.
- Test `/ready` returns 503 when repository is unavailable.
- Test each engine reports status correctly.
- Test Railway health check configuration.

**Production validation checklist:**
- [ ] Verify `/ready` fails during simulated DB outage.
- [ ] Verify Railway restarts container on failed readiness probe.
- [ ] Verify `/health` remains 200 even when `/ready` fails.
- [ ] Check health logs include all engine statuses.

**Monitoring after deployment:**
- Health check pass/fail rate.
- Time to readiness after startup.
- Per-engine health status.

**Success criteria:**
- `/health` and `/ready` endpoints return correct statuses.
- Railway detects failures before user-visible errors.
- Startup readiness time <30 seconds.

**Failure criteria:**
- Health check returns 200 when DB is down.
- Readiness probe too slow, causing premature restarts.
- Missing engine in health status.

---

### M. Circuit breaker for Claude API in Narrator

**Implementation order:**
1. Add a circuit breaker wrapper around `narrate()` LLM call.
2. Configure timeout, failure threshold, and recovery window.
3. Add fallback response generator that returns a graceful message.
4. Cache fallback responses separately to avoid hammering the API.
5. Add metrics: circuit state, fallback rate, timeout rate.

**Technical dependencies:**
- `Narrator.narrate()` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator.py:739-793`).
- `NarratorCache` (`@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py:1-251`).
- Claude API client configuration.

**Repositories affected:**
- `voice-n8n-agent`.

**Estimated complexity:** Low.

**Regression risk:** Low. Defensive only.

**Rollback strategy:** Disable circuit breaker via flag.

**Required automated tests:**
- Test circuit opens after N failures.
- Test fallback response is returned when circuit is open.
- Test circuit closes after recovery window.
- Test timeout fires correctly.
- Test fallback is cached separately.

**Production validation checklist:**
- [ ] Simulate Claude API timeout in staging.
- [ ] Verify fallback response is served to Telegram.
- [ ] Verify circuit opens and closes correctly.
- [ ] Verify no duplicate API calls during open circuit.
- [ ] Monitor fallback rate for 48 hours.

**Monitoring after deployment:**
- Circuit state transitions.
- Fallback response rate.
- Claude API timeout/failure rate.
- User satisfaction (Telegram feedback).

**Success criteria:**
- User receives a graceful fallback instead of an error when Claude is down.
- Claude API is not hammered during outages.
- Circuit recovers automatically.
- Fallback rate <1% under normal conditions.

**Failure criteria:**
- Fallback rate >5% in normal conditions.
- Circuit fails to close after recovery.
- Users see error messages instead of fallback.

---

## 3. Phase Breakdown

### Phase 1 — Foundation & Safety (Weeks 1–6)

**Goal:** Establish safe, stable boundaries without changing business logic or data.

**Recommendations:** G, H, K, L, M.

**What Cursor should implement:**
- G: Add `/api/v1/graph/...` endpoints, keep old aliases, add pagination.
- H: Add Pydantic models and contract documentation.
- K: Add env vars and pass them to memory/cache initializers.
- L: Add health and readiness endpoints.
- M: Add circuit breaker wrapper around Claude API calls.

**What requires architectural review before merge:**
- REST API path naming convention (`/api/v1/graph/...`).
- Health check semantics (liveness vs. readiness).
- Company identity contract schema (fields and types).

**What requires production validation:**
- Old REST endpoints remain 200.
- Pagination produces correct results.
- Memory components connect to correct DBs.
- Health checks detect DB failures.
- Fallback works when Claude is unavailable.

**What requires human approval:**
- Final API contract shape.
- Railway health check configuration.
- Circuit breaker thresholds (timeout, failure count, recovery window).

**Compatibility validated for:**
- bc-tender-scraper, tenderscope-kg, voice-n8n-agent, Graph, Registry, Company Identity, Session Memory, Strategic Memory, Narrator, Evidence cache, Opportunity cache, Railway, n8n, Telegram, EDE. All safe because no business logic or identifiers change.

---

### Phase 2 — Data Quality Improvements (Weeks 7–16)

**Goal:** Improve data quality without changing canonical model or identifiers.

**Recommendations:** D, E.

**What Cursor should implement:**
- D: Backfill graph confidence, update importer, add threshold config, update CI/EDE filters.
- E: Add deny-list config, update heuristic, add feature flag.

**What requires architectural review before merge:**
- Graph confidence formula and weights.
- Threshold calibration policy.
- Heuristic rule set (city/trade deny-lists).

**What requires production validation:**
- Run `prototype_graph_confidence.py` on production snapshot.
- Run `prototype_canonical_impact.py` for E validation.
- Manual review of excluded companies at chosen threshold.
- Regression test of person-name heuristic.

**What requires human approval:**
- Initial `MIN_COMPANY_CONFIDENCE` threshold (start at `0.0`).
- Any threshold increase above `0.0`.
- Final deny-list rules.

**Compatibility validated for:**
- All components. CI/EDE output changes self-heal via `evidence_hash`. Session and Strategic Memory keyed by stable IDs.

---

### Phase 3 — Observability & Shared Utilities (Weeks 17–24)

**Goal:** Improve observability and centralize shared utilities introduced in Phase 1/2.

**Recommendations:** I, J.

**What Cursor should implement:**
- I: Create deterministic evidence-hash utility in tenderscope-kg; adopt in voice-n8n-agent.
- J: Add `identity_audit_log` table, writes on identity operations, read-only endpoint.

**What requires architectural review before merge:**
- Evidence-hash algorithm and versioning strategy.
- Audit log schema and retention policy.
- Which identity operations must be logged.

**What requires production validation:**
- Side-by-side hash comparison for 1,000 samples.
- Audit log rows after batch import.
- Latency impact of audit writes.

**What requires human approval:**
- Audit log retention period.
- Hash version cut-over date.

**Compatibility validated for:**
- All components. Append-only audit log has no runtime impact. Hash utility is backwards-compatible if versioned.

---

### Phase 4 — Canonical Model Modernization (Weeks 25+, gated)

**Goal:** Modernize canonical entity model only after Phase 2.1 validation proves it is safe.

**Recommendations:** A, B.

**Gate conditions:**
- `prototype_canonical_impact.py` shows <0.5% canonical entities changing.
- No verified real companies are downgraded.
- All SQL queries and CI filters updated to handle new roles.
- Human approval obtained.

**What Cursor should implement:**
- A: Add validation gate in canonical merge; add feature flag; re-run merge.
- B: Add new role constants and projection logic; update SQL/CI filters; update graph importer mapping.

**What requires architectural review before merge:**
- Validation gate rules.
- New role semantics and mappings.
- Graph importer role-to-kind mapping.

**What requires production validation:**
- Full production snapshot run of `prototype_canonical_impact.py`.
- Manual review of top 50 affected companies.
- CI output comparison before/after.
- Graph importer validation.

**What requires human approval:**
- Committing role changes to production data.
- Enabling new entity roles in production queries.
- Any decision to proceed if impact >0.5%.

**Compatibility validated for:**
- All components. Identifiers stable; only metadata changes. Cache self-heals. Graph UIDs remain stable.

---

## 4. Dependency Graph & Parallelism

```text
Phase 1 (Foundation & Safety)
  ├── G. REST API versioning ────────┐
  ├── H. Identity contract ──────────┤ All parallel, no dependencies
  ├── K. Memory DB config ────────────┤
  ├── L. Health checks ──────────────┤
  └── M. Claude circuit breaker ────┘

Phase 2 (Data Quality)
  ├── D. Graph confidence ────────────┐ Depends on Phase 1 G/H (stable API contract)
  └── E. Person-name heuristic ──────┘ Independent within Phase 2

Phase 3 (Observability & Utilities)
  ├── I. Evidence-hash utility ───────┐ Depends on Phase 1 G/H (stable APIs and Phase 2 D if confidence included in hash)
  └── J. Audit log ──────────────────┘ Independent within Phase 3; depends on Phase 1 stable repo operations

Phase 4 (Canonical Model)
  ├── A. Validation gate ─────────────┐ Sequential: A before B
  └── B. New roles ──────────────────┘ Gated by Phase 2.1 numbers

Cross-phase gates:
  Phase 1 must complete before Phase 2 and Phase 3.
  Phase 2.1 validation numbers must be approved before Phase 4 begins.
  Phase 4 should not begin until Phase 2 D and Phase 3 I are stable.

Never parallel:
  A and B must not be in the same release.
  F (authority migration) is out of 12-month scope and must never be combined with A–E.
```

### Parallel execution table

| Work | Can run in parallel with | Must wait for | Notes |
|---|---|---|---|
| G | H, K, L, M | Nothing | Independent transport work. |
| H | G, K, L, M | Nothing | Independent docs/schema work. |
| K | G, H, L, M | Nothing | Independent config work. |
| L | G, H, K, M | Nothing | Independent observability work. |
| M | G, H, K, L | Nothing | Independent defensive work. |
| D | E | Phase 1 G/H | Needs stable API for validation. |
| E | D | Phase 1 K/L (optional) | No hard dependency, but safer after foundation. |
| I | J | Phase 1 G/H, Phase 2 D | Confidence field should be included in hash. |
| J | I | Phase 1 G/H | Independent but needs stable repo operations. |
| A | Nothing in Phase 4 | Phase 2.1 validation, Phase 3 I/J | Sequential within Phase 4. |
| B | Nothing in Phase 4 | A completed and stable | Must follow A. |

---

## 5. Cursor Delegation Matrix

**Safe to delegate to Cursor (implementation only, after plan approval):**

| Task | Why safe |
|---|---|
| G. REST API versioning + pagination | Transport-only; well-defined scope; tests are straightforward. |
| H. Identity contract Pydantic models + docs | Schema-only; no logic changes. |
| K. Memory DB config env vars | Config-only; default behavior preserved. |
| L. Health check endpoints | Observability-only; does not affect runtime. |
| M. Claude circuit breaker | Defensive wrapper; isolated to Narrator. |
| D. Graph confidence backfill script | Read-only prototype exists; can be converted to production backfill. |
| E. Person-name heuristic config update | Local heuristic change; flag-gated. |
| I. Evidence-hash utility | Pure function; extensive testable. |
| J. Audit log table + writes | Append-only; does not affect runtime decisions. |

**Requires human review before Cursor implements:**

| Task | Why human review |
|---|---|
| D. CI/EDE threshold logic | Threshold affects production results and user-facing answers. |
| D. Confidence formula weights | Business decision about what constitutes trust. |
| E. Final deny-list rules | Domain expertise needed to avoid false negatives. |
| A. Validation gate rules | Changes canonical promotion; affects data quality. |
| B. New role semantics | API contract and query behavior changes. |
| H. Final contract schema | Consumers depend on this shape. |

**Requires human approval before production deploy:**

| Task | Why human approval |
|---|---|
| A. Enabling validation gate in production | Changes production entity roles. |
| B. Enabling new roles in production | Changes query behavior and analytics. |
| D. Any threshold >0.0 | Affects which companies appear in CI/EDE. |
| E. Enabling heuristic V2 in production | Affects company classification. |
| M. Circuit breaker thresholds | Affects user experience during outages. |
| K. Splitting memory DB in production | Operational change; affects cost and failover. |

---

## 6. Remaining Architectural Uncertainties

Before implementation begins, these questions must be answered by the team:

1. **Graph confidence formula:** Is the weighting in the prototype (external IDs 30%, aliases 25%, etc.) acceptable as the production formula, or does it need calibration with labeled data?

2. **External identifier data source:** Is there a reliable, ongoing source for BC Registry / BN / GST numbers? Without it, Recommendation C cannot proceed, and D's confidence scores may remain low for many companies.

3. **CI/EDE threshold policy:** Who decides the threshold increase? Is it a weekly review, or tied to automated metrics?

4. **New entity role semantics:** Are `placeholder` and `unresolved` defined as explicit states in the scraper pipeline, or will they be derived from review/conflict queues?

5. **Strategic Memory rollout:** When will Strategic Memory be enabled? It must remain disabled until Phase 4 is complete, because bad entities would be persisted as historical facts.

6. **Railway multi-instance plan:** Does the deployment plan include running multiple instances? If yes, Redis/shared cache must be prioritized earlier.

7. **Public API consumers:** Are there any existing external consumers of `/api/graph/...` that would be broken by versioning? If yes, the deprecation period must be longer.

8. **n8n payload contract:** Does n8n parse REST/MCP responses by specific field names? If yes, H must include n8n compatibility verification.

9. **Morning Brief dependency:** Does Morning Brief derive directly from EDE, or does it have its own cache? If the latter, that cache must be included in the compatibility matrix for D.

10. **Authority migration timeline:** Is F on the 3-year, 5-year, or never roadmap? This determines whether new services should be built with `company_uid` as primary key from day one.

---

## 7. Master Schedule (6–12 Months)

| Phase | Weeks | Recommendations | Human Gates |
|---|---|---|---|
| 1 | 1–6 | G, H, K, L, M | API contract review, Railway health check config, circuit breaker thresholds |
| 2 | 7–16 | D, E | Confidence threshold approval, heuristic deny-list approval |
| 3 | 17–24 | I, J | Audit retention approval, hash version cut-over |
| 4 | 25+ | A, B | Phase 2.1 validation approval, role semantics approval, production merge approval |

**Critical path:** G/H → D → I → A → B.  
**Parallelizable work:** K, L, M can run alongside Phase 1. E can run alongside D. J can run alongside I.

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Phase 2.1 shows >0.5% canonical impact for A | Medium | High | Do not proceed to Phase 4; tune heuristic; revisit later. |
| Threshold calibration for D excludes real companies | Medium | Medium | Start at 0.0; raise slowly; monitor manually. |
| Heuristic E introduces false negatives | Low | Medium | Regression test set; flag-gated rollout. |
| REST API versioning breaks existing consumer | Low | Medium | Keep deprecated aliases; monitor 404s. |
| Memory DB split causes connection pool issues | Low | Medium | Defaults to shared DB; test failover. |
| Claude circuit breaker degrades UX | Low | Low | Tune thresholds; fallback message review. |
| Audit log writes add latency | Low | Low | Async or fire-and-forget writes; load test. |

---

## 9. Definition of Done for the Program

1. Phase 1 is deployed and all health checks, API contracts, and defensive measures are in production.
2. Phase 2 D is deployed with confidence backfilled and threshold at a calibrated, approved value.
3. Phase 2 E is deployed and false-negative rate is within acceptable bounds.
4. Phase 3 I and J are deployed and verified.
5. Phase 2.1 validation numbers are reviewed and either approved or rejected for Phase 4.
6. If approved, Phase 4 A and B are deployed with full production validation.
7. No implementation of F (authority migration) begins until Phase 4 is stable.

---

## 10. Files & References

- Phase 2 review: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\phase2-canonical-entity-graph-review.md`
- Architecture readiness review: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architecture-readiness-review.md`
- Stress test: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architectural-stress-test.md`
- Final roadmap: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\final-architectural-roadmap.md`
- Integration validation: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\integration-compatibility-validation.md`
- Prototypes: `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\scripts\prototype_canonical_impact.py` and `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\scripts\prototype_graph_confidence.py`
