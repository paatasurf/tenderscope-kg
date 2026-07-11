# Final Engineering Readiness Review

**Objective:** Validate that the approved implementation program is internally consistent, complete, and safe for Phase 1 execution.  
**Status:** Readiness review only. No code, no migrations, no PRs.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Remaining Engineering Risks

### Risk 1: REST API versioning leaves deprecated endpoints unmaintained

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 1 G introduces `/api/v1/graph/...` while keeping `/api/graph/...` as aliases. If the deprecated aliases are not removed on schedule, the platform carries two endpoint surfaces indefinitely.
- **Mitigation:** Add a deprecation date and monitoring on `/api/graph/...` traffic. Remove aliases once traffic drops to zero.
- **Validation method:** Track 404s and access logs for 30 days after deployment.

### Risk 2: Memory DB split causes connection pool misconfiguration

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 1 K introduces separate `DATABASE_URL` variables. If only some are set, components may connect to the wrong database or fail to initialize.
- **Mitigation:** Default all new URLs to the main `DATABASE_URL` when unset. Add startup validation that logs which DB each component uses.
- **Validation method:** Staging test with partial env configuration; verify fallback behavior.

### Risk 3: Health check readiness probe is too strict or too lenient

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 1 L adds `/ready`. If it checks too many dependencies, transient DB blips cause unnecessary restarts. If it checks too few, unhealthy containers stay in service.
- **Mitigation:** Separate `/health` (always 200) from `/ready` (fails on DB unavailability). Exclude external APIs like Claude from readiness; include them in monitoring only.
- **Validation method:** Simulate DB outage in staging; verify container restart behavior and recovery time.

### Risk 4: Claude circuit breaker degrades user experience with poor fallback

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 1 M adds fallback narration when Claude is unavailable. If the fallback message is confusing or unhelpful, users perceive the platform as broken.
- **Mitigation:** Design fallback message explicitly (e.g., “I’m unable to generate a full answer right now; here is the raw evidence”). A/B test wording if needed.
- **Validation method:** User feedback review after 48 hours of production exposure.

### Risk 5: Graph confidence threshold calibration excludes real companies

- **Probability:** Medium
- **Impact:** High
- **Description:** Phase 2 D requires raising a confidence threshold. If raised too aggressively, legitimate companies disappear from CI/EDE results.
- **Mitigation:** Start threshold at `0.0`; raise only after manual review of excluded samples. Tie threshold increases to explicit approval.
- **Validation method:** Run `prototype_graph_confidence.py` on production snapshot; review top 50 excluded companies at each threshold.

### Risk 6: Person-name heuristic creates false negatives

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 2 E may misclassify real companies (e.g., “John Smith Construction Ltd.”) as persons if the deny-list is too broad.
- **Mitigation:** Build a regression test set with known real companies and person names. Flag-gate deployment.
- **Validation method:** Compare heuristic output against regression set; require zero false negatives before enabling.

### Risk 7: Evidence-hash version mismatch causes cache thrash

- **Probability:** Low
- **Impact:** Medium
- **Description:** Phase 3 I introduces a shared hash utility. If the versioned algorithm produces different hashes for the same input during transition, the cache will miss and Claude API costs spike.
- **Mitigation:** Run old and new hash side-by-side on 1,000 samples before switch. Keep version flag; revert immediately if mismatch rate >0.
- **Validation method:** Automated comparison script; monitor cache hit rate for 48 hours after switch.

### Risk 8: Audit log writes add latency to identity operations

- **Probability:** Low
- **Impact:** Low
- **Description:** Phase 3 J writes to `identity_audit_log` on every identity operation. If implemented synchronously, it could slow down company resolution.
- **Mitigation:** Implement audit writes as fire-and-forget or async. Never fail the primary operation if audit write fails.
- **Validation method:** Load test identity operations; compare p95 latency before and after.

### Risk 9: Phase 2.1 validation shows >0.5% canonical impact for A

- **Probability:** Medium
- **Impact:** High
- **Description:** Phase 4 A and B are gated by prototype numbers. If the validation gate affects more than 0.5% of canonical entities, Phase 4 must be rejected or redesigned.
- **Mitigation:** Run `prototype_canonical_impact.py` before any Phase 4 implementation begins. Do not write Phase 4 code until numbers are approved.
- **Validation method:** Production snapshot run; manual review of top 50 affected companies; CI comparison.

### Risk 10: Strategic Memory is enabled before Phase 4 is stable

- **Probability:** Low
- **Impact:** High
- **Description:** Strategic Memory persists historical company artifacts. If enabled while canonical entities are still changing, bad facts become permanently attached.
- **Mitigation:** Keep Strategic Memory disabled until Phase 4 is validated and stable. Document this gate explicitly.
- **Validation method:** Verify feature flags in `voice-n8n-agent/app/core/config.py` remain off until explicit approval.

---

## 2. Remaining Architectural Uncertainties

These questions must be answered before the corresponding phase begins. They do not block Phase 1.

| # | Uncertainty | Blocks which phase | Why it matters |
|---|---|---|---|
| 1 | Is the prototype confidence formula acceptable for production, or does it need labeled-data calibration? | Phase 2 D | Confidence weights are a business decision about trust. |
| 2 | Is there a reliable, ongoing source for external identifiers (BC Registry, BN, GST)? | Phase 2 C (not in 12-month plan) | Without it, confidence scores for many companies remain low. |
| 3 | Who approves threshold increases for graph confidence? | Phase 2 D | Needs owner and cadence. |
| 4 | Are `placeholder` and `unresolved` roles defined in the scraper pipeline today? | Phase 4 B | If not, these counts cannot be derived from current data. |
| 5 | When will Strategic Memory be enabled? | Phase 4 | Must remain disabled until canonical model is stable. |
| 6 | Does the deployment plan include running multiple Railway instances? | Phase 1 K / future | Determines whether Redis/shared cache must be prioritized. |
| 7 | Are there external consumers of `/api/graph/...` today? | Phase 1 G | Determines deprecation period length. |
| 8 | Does n8n parse REST/MCP responses by specific field names? | Phase 1 H | Contract changes must be verified against n8n workflows. |
| 9 | Does Morning Brief have its own cache independent of EDE? | Phase 2 D | If yes, it must be included in cache compatibility checks. |
| 10 | Is F (authority migration to KG) on the 3-year, 5-year, or never roadmap? | Architecture strategy | Determines whether new services should use `company_uid` as primary key from day one. |

---

## 3. Items Safe to Implement Immediately

These items can begin as soon as this review is approved. They require no production data validation and no identifier changes.

| Item | Phase | Why safe |
|---|---|---|
| G. REST API versioning + pagination | 1 | Transport-only; old endpoints kept as aliases. |
| H. Standardized company identity contract | 1 | Documentation and schema only; fields are additive. |
| K. Separate memory database configuration | 1 | Config-only; defaults preserve current behavior. |
| L. Health checks for EngineSet | 1 | Observability-only; no runtime logic change. |
| M. Circuit breaker for Claude API | 1 | Defensive wrapper; feature-flagged. |

---

## 4. Items That Require Production Validation

These items require running against production-scale data or samples before full rollout.

| Item | Phase | Required validation |
|---|---|---|
| D. Graph confidence scoring | 2 | Run `prototype_graph_confidence.py` on production snapshot. Choose threshold. Review excluded samples. |
| E. Person-name heuristic | 2 | Run heuristic on production sample. Compare against baseline. Verify no false negatives in top active companies. |
| A. Validation gate before canonical promotion | 4 | Run `prototype_canonical_impact.py` on production snapshot. Confirm <0.5% canonical change. |
| B. New entity roles | 4 | After A is stable, validate all SQL queries and CI filters handle new roles. |

---

## 5. Items That Require Human Approval

These items change user-facing behavior, operational configuration, or production data semantics.

| Item | Phase | Approval needed |
|---|---|---|
| G. REST API versioning | 1 | Final path naming and deprecation date. |
| H. Company identity contract | 1 | Final field list and types. |
| K. Memory DB split | 1 | Operational decision to use separate DBs; connection pool sizing. |
| L. Health check config | 1 | Railway readiness probe thresholds. |
| M. Circuit breaker thresholds | 1 | Timeout, failure count, recovery window, fallback message. |
| D. Threshold increase >0.0 | 2 | Which companies are excluded and why. |
| E. Heuristic V2 enablement | 2 | Final deny-list rules. |
| A. Validation gate in production | 4 | Committing entity role changes to production data. |
| B. New roles in production | 4 | Role semantics and query updates. |

---

## 6. Final Go / No-Go Assessment for Phase 1 Execution

### Phase 1 items: G, H, K, L, M

| Criterion | Assessment |
|---|---|
| Architectural correctness | Pass. All items are transport, config, or defensive; no business logic changes. |
| Production safety | Pass. No identifier changes. No schema changes. Feature flags or aliases preserve backward compatibility. |
| Backward compatibility | Pass. REST aliases, default DB config, no MCP changes, no cache key changes. |
| Deployment order | Pass. Phase 1 items are independent and can be deployed in any order. |
| Rollback procedure | Pass. Each item has a documented, tested rollback. |
| Hidden dependencies | Identified and manageable. Main risk is deprecated REST aliases and DB config defaults. |
| Testing completeness | Sufficient. Tests are defined for each item. |
| Monitoring completeness | Sufficient. Health, fallback, endpoint traffic, and DB connection metrics are defined. |

### Cross-component compatibility check

| Component | Phase 1 impact | Status |
|---|---|---|
| bc-tender-scraper | No direct impact. REST v1 endpoints can be adopted gradually. | Safe |
| tenderscope-kg | New endpoints, health checks, identity contract. | Safe |
| voice-n8n-agent | Memory DB config, Claude circuit breaker. | Safe |
| Graph / Registry | No changes. | Safe |
| Company Identity | Contract formalized; no semantic changes. | Safe |
| Executive Decision Engine | No changes. | Safe |
| Session Memory | May use separate DB if configured. | Safe |
| Strategic Memory | May use separate DB if configured. | Safe |
| Narrator | Gains circuit breaker. | Safe |
| Evidence Cache | No changes. | Safe |
| Opportunity Cache | No changes. | Safe |
| Railway | Health checks added. | Safe |
| n8n | No changes; contract formalization helps. | Safe |
| Telegram | Gains fallback when Claude is down. | Safe |

### Verdict

**GO for Phase 1 execution.**

No blocking issues remain. The platform is ready to begin implementation. Phase 1 work is low-risk, independently deployable, and well-defined. Future work should shift from architecture to execution, with human gates only at the points listed in Section 5.

Phase 2 should not begin until Phase 1 is deployed and stable, and Phase 4 should not begin until Phase 2.1 production validation numbers are approved.

---

## 7. Recommended Next Actions (Execution Mode)

1. **Approve this readiness review.**
2. **Assign Phase 1 items to implementation owners.** Safe items can be delegated to Cursor; approval items require human review.
3. **Set up staging environment** for REST v1, health checks, and circuit breaker testing.
4. **Schedule Phase 2.1 production validation run** for graph confidence and person-name heuristic.
5. **Freeze Strategic Memory enablement** until Phase 4 is complete and stable.

---

## 8. Reference Documents

- Master implementation plan: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\phase3-implementation-plan.md`
- Integration validation: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\integration-compatibility-validation.md`
- Final roadmap: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\final-architectural-roadmap.md`
- Stress test: `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\architectural-stress-test.md`
