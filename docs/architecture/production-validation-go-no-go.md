# Phase 2.1 – Production Validation & Go/No-Go Assessment

**Status:** Measurement-only phase. No production changes, no schema changes, no migrations, no Graph rebuild.  
**Branch:** `experiment/canonical-entity-impact`  
**Date:** 2026-07-10

---

## 1. Objective

Use the read-only prototype scripts to measure the real production impact of the proposed architecture improvements and decide whether each recommendation is safe enough to implement.

---

## 2. How the numbers were produced

Two read-only scripts were run against a production snapshot / read replica:

- `bc-tender-scraper/scripts/prototype_canonical_impact.py` → `canonical_impact.json`
- `tenderscope-kg/scripts/prototype_graph_confidence.py` → `graph_confidence.json`

Both scripts issue only `SELECT` statements and never commit.

> **TODO:** Populate this section with the actual snapshot path, run date, and runner.

---

## 3. Entity Statistics

| Metric | Value |
|---|---|
| Total COMPANY entities | `_` |
| Total canonical entities | `_` |
| Total standalone entities | `_` |
| Total probable_person entities | `_` |
| Total applicant_alias entities | `_` |

Role distribution (from `canonical_impact.json`):

```json
{
  "canonical": _,
  "standalone": _,
  "applicant_alias": _,
  "probable_person": _,
  "unknown": _
}
```

---

## 4. Recommendation A Impact

### Validation gate before canonical promotion

If generic-bucket and probable-person canonicals were demoted:

| Metric | Value |
|---|---|
| Canonical entities changing | `_` |
| % of all canonical entities | `_` |
| % of all entities | `_` |

Affected role transitions:

| Current role | Proposed role | Count |
|---|---|---|
| canonical | generic_bucket | `_` |
| canonical | probable_person | `_` |
| canonical | verified_company | `_` |

Top 50 examples (from `canonical_impact.json`):

> Populate from `recommendation_a.top_50_examples`.

### Go/No-Go decision for Recommendation A

**GO only if ALL are true:**

- [ ] Canonical entities changing < 0.5%
- [ ] No verified production companies incorrectly downgraded
- [ ] No Company IDs changed
- [ ] No Graph rebuild required

**Decision:** `GO / NO-GO`  
**Rationale:** `_`

---

## 5. Recommendation B Impact

### New entity role projections

Estimated counts if new roles were applied:

| Proposed role | Count | Notes |
|---|---|---|
| `verified_company` | `_` | Requires external-ID marker (`bc_registry_number`) in current prototype. |
| `probable_company` | `_` | Standalone rows with business activity. |
| `probable_person` | `_` | Standalone rows matching person-name heuristic. |
| `generic_bucket` | `_` | Canonical or standalone generic-bucket names. |
| `placeholder` | `_` | Requires explicit placeholder signal; not derivable from current data. |
| `unresolved` | `_` | Requires conflict-review queue data. |

### Go/No-Go decision for Recommendation B

**GO only if:**

- [ ] Entity role migration is deterministic
- [ ] No production queries break
- [ ] No API compatibility issues
- [ ] No Company IDs change

**Decision:** `GO / NO-GO`  
**Rationale:** `_`

---

## 6. Competitive Intelligence Impact

### CI competitor-list changes

| Metric | Value |
|---|---|
| Companies with changed competitor list | `_` |
| % of sampled companies affected | `_` |
| Average competitors removed | `_` |
| Maximum competitors removed | `_` |
| Total competitors removed (sample) | `_` |

Top 20 largest changes (from `canonical_impact.json`):

> Populate from `ci_impact.top_20_largest_changes`.

### Interpretation

A small, clean removal of generic-bucket / probable-person competitors is a strong signal to proceed. If many real companies are removed, the heuristic or confidence formula needs tuning.

---

## 7. Graph Confidence Analysis

### Distribution

| Bucket | Count |
|---|---|
| 0.8–1.0 | `_` |
| 0.5–0.8 | `_` |
| 0.25–0.5 | `_` |
| 0.0–0.25 | `_` |

Histogram (10 buckets):

```json
{
  "0.0-0.1": _,
  "0.1-0.2": _,
  ...
  "0.9-1.0": _
}
```

Percentiles:

| Percentile | Value |
|---|---|
| p50 | `_` |
| p90 | `_` |
| p95 | `_` |
| p99 | `_` |

### Recommended initial threshold

**Recommended threshold:** `_`  
**Rationale:** `_`

The recommended threshold in the prototype is chosen near p80 while staying above p50, with the goal of excluding only the lowest-confidence tail. It should be adjusted after manual review of excluded samples.

### Threshold simulations

| Threshold | Companies above | Companies below | % above |
|---|---|---|---|
| 0.25 | `_` | `_` | `_` |
| 0.50 | `_` | `_` | `_` |
| 0.70 | `_` | `_` | `_` |
| 0.85 | `_` | `_` | `_` |

### Go/No-Go decision for Recommendation D

**Can graph confidence be consumed by CI and EDE without changing GraphDB?**

- [ ] Yes — read `BizEntity.confidence` attribute at query time.
- [ ] No — requires schema or indexing changes.

**Decision:** `GO / NO-GO / DEFER`  
**Estimated implementation effort:** `_`  
**Rationale:** `_`

---

## 8. Downstream Impact Summary

| Component | Impact Level | Explanation |
|---|---|---|
| Company Search | Low / Medium / High / No impact | `_` |
| Competitive Intelligence | Low / Medium / High / No impact | `_` |
| Executive Decision Engine | Low / Medium / High / No impact | `_` |
| Narrator | Low / Medium / High / No impact | `_` |
| Strategic Memory | Low / Medium / High / No impact | `_` |
| Session Memory | Low / Medium / High / No impact | `_` |
| Voice Agent | Low / Medium / High / No impact | `_` |

---

## 9. Final Recommendation Summary

| Recommendation | Benefit | Risk | Production Impact | GO / NO-GO |
|---|---|---|---|---|
| A. Validation gate before canonical promotion | Removes false canonicals; improves CI trust. | May demote real single-member companies if heuristic is wrong. | Low if <0.5% change. | `GO / NO-GO` |
| B. New entity roles (`verified_company`, `generic_bucket`, etc.) | Richer trust model; explicit quarantine. | Requires query/consumer updates; role churn. | Low if introduced gradually. | `GO / NO-GO` |
| D. Graph confidence for CI/EDE | Improves decision quality with minimal disruption. | Calibration risk; may exclude low-evidence real companies. | Low; no schema change. | `GO / NO-GO` |

---

## 10. Required Actions Before Implementation

1. Run `prototype_canonical_impact.py` against a production snapshot and paste `canonical_impact.json` into this report.
2. Run `prototype_graph_confidence.py` against the same snapshot and paste `graph_confidence.json` into this report.
3. Manually review the top 50 examples and top 20 CI changes.
4. Verify no production queries break under new role projections.
5. Decide final confidence threshold after reviewing excluded graph nodes.
6. Do **not** enable Strategic Memory until all three recommendations are validated.
