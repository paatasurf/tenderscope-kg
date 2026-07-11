# Entity Validation Impact Analysis

**Branch:** `experiment/canonical-entity-impact`  
**Status:** Prototype / read-only. No production changes.  
**Date:** 2026-07-10

This document accompanies two prototype scripts that quantify the impact of the Phase 2 recommendations **before** any production rollout.

---

## 1. Objective

Validate the following candidate architecture changes using production-scale data in an isolated branch:

- **Recommendation A:** Validation gate before canonical promotion.
- **Recommendation B:** New entity roles (`generic_bucket`, `verified_company`).
- **Recommendation D:** Graph confidence scoring for CI/EDE consumption.

The goal is to produce hard numbers for:

1. How many `Company` rows would change `entity_role`.
2. How many canonical generic buckets exist today.
3. How many CI competitors would be removed under each proposed filter.
4. How graph confidence is distributed and how a confidence threshold affects CI/EDE.
5. Cross-system impact on search, GraphDB, Executive Decision Engine, narrator cache, and strategic memory.

---

## 2. Prototype Tools

### 2.1 bc-tender-scraper impact script

**File:** `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\scripts\prototype_canonical_impact.py`

What it does (read-only):

- Loads every `Company` row.
- Reports current `entity_role` distribution.
- Simulates Recommendation A + B: reclassifies rows to `generic_bucket`, `verified_company`, etc.
- Identifies canonical rows whose names are generic buckets.
- Identifies standalone rows that look like probable persons.
- For a fixed sample of canonical companies, fetches the same peer pool the CI cohort SQL would see and counts how many competitors would be removed by the new filters.
- Prints a JSON report to stdout.

How to run against a production snapshot:

```bash
cd bc-tender-scraper
export DATABASE_URL="postgresql://..."
python -m scripts.prototype_canonical_impact > canonical_impact_report.json
```

The script never commits.

### 2.2 tenderscope-kg graph confidence script

**File:** `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\scripts\prototype_graph_confidence.py`

What it does (read-only):

- Connects to a business graph database (SQLite or PostgreSQL via `DATABASE_URL`).
- Iterates every `COMPANY` node.
- Computes a synthetic confidence score from:
  - external identifiers (`scraper_id`, `id_bc_registry`, etc.)
  - `ALIAS_OF` edges carrying `IdentityEvidence`
  - `SAME_AS` merge candidates
  - business activity edges (`AWARDED_TO`, `SUBMITTED_BID`, etc.)
  - source diversity
- Reports score distribution and simulates threshold-based filtering at `0.25`, `0.5`, `0.7`, `0.85`.
- Lists sample companies that would be excluded at each threshold.

How to run:

```bash
cd tenderscope-kg
# local SQLite snapshot
python scripts/prototype_graph_confidence.py /path/to/graph.db

# or production PostgreSQL
export DATABASE_URL="postgresql://..."
python scripts/prototype_graph_confidence.py
```

The prototype confidence formula is tunable. Weights are exported in the JSON output.

---

## 3. Methodology Notes

### 3.1 Data source

For trustworthy numbers, run both scripts against a **production read replica** or a **sanitized nightly snapshot**. Running against a developer laptop will usually show zero or skewed data because local graph databases are not populated with the full business graph.

### 3.2 What is being counted

| Metric | Definition |
|---|---|
| `generic_bucket_canonical_count` | `entity_role='canonical'` and `is_generic_bucket_company_name(name)==True` |
| `probable_person_standalone_count` | `entity_role='standalone'` and `is_probable_person_name(name)==True` |
| `role_change_count` | Rows where simulated new role differs from current role |
| `ci_impact.*.removed_generic_bucket` | CI cohort members that are canonical generic buckets |
| `ci_impact.*.removed_probable_person` | CI cohort members that are standalone probable persons |
| `graph.companies_above` | Graph companies with confidence >= threshold |
| `graph.companies_below` | Graph companies with confidence < threshold |

### 3.3 Confidence formula

```text
confidence =
    0.30 * external_id_score +
    0.25 * alias_evidence_score +
    0.10 * same_as_evidence_score +
    0.20 * business_relationship_score +
    0.15 * source_diversity_score
```

Each component is clamped to `[0, 1]`. The weights are a starting point and should be calibrated against a labelled validation set.

---

## 4. Sample Output Shapes

### 4.1 bc-tender-scraper report (simulated)

```json
{
  "entity_role_distribution": {
    "canonical": 12345,
    "standalone": 67890,
    "applicant_alias": 23456,
    "probable_person": 789
  },
  "classification_changes": [
    {"current_role": "canonical", "proposed_role": "generic_bucket", "count": 42, "sample_ids": [548732, ...]}
  ],
  "generic_bucket_canonical": [
    {"id": 548732, "name": "Architect", "entity_role": "canonical", ...}
  ],
  "probable_person_standalone": [
    {"id": 999999, "name": "Yi Chieh (Ashanti) Lee", ...}
  ],
  "ci_impact": [
    {
      "subject_id": 549130,
      "subject_name": "EllisDon Corporation",
      "total_competitors": 47,
      "removed_generic_bucket": 1,
      "removed_probable_person": 0,
      "removed_low_confidence": 0,
      "removed_ids": [{"id": 548732, "name": "Architect", "reason": "generic_bucket_canonical"}]
    }
  ],
  "summary": {
    "total_companies": 104480,
    "total_canonical": 12345,
    "generic_bucket_canonical_count": 42,
    "probable_person_standalone_count": 156,
    "role_change_count": 198,
    "ci_sample_subjects": 8,
    "note": "All changes are simulated; no database writes occurred."
  }
}
```

Numbers above are illustrative until the script is run against production data.

### 4.2 tenderscope-kg report (simulated)

```json
{
  "graph_path": "/path/to/graph.db",
  "total_company_nodes": 12345,
  "score_distribution": {
    "0.8-1.0": 3456,
    "0.5-0.8": 5432,
    "0.25-0.5": 2345,
    "0.0-0.25": 712
  },
  "threshold_impacts": [
    {
      "threshold": 0.5,
      "companies_above": 8888,
      "companies_below": 3457,
      "pct_above": 71.98,
      "sample_excluded": [
        {"uid": "CMP-00012345", "name": "Some Thin Entity Inc.", "confidence": 0.32, "breakdown": {...}}
      ]
    }
  ],
  "formula": {...}
}
```

---

## 5. Cross-System Impact Assessment

### 5.1 Competitive Intelligence

| Change | Impact on CI | How to measure |
|---|---|---|
| Generic-bucket canonicals become `generic_bucket` | Removes false competitors | `removed_generic_bucket` in prototype report |
| Probable-person standalones stay excluded | No new false competitors | Count in `removed_probable_person` |
| Graph confidence threshold | Filters low-evidence competitors | `companies_below` at each threshold |
| New `verified_company` role (informational) | No immediate CI change if not used as filter | Track verified % over time |

**Key question to answer with production numbers:** What percentage of current CI competitor lists would be removed by each filter? A value below 1–2% is likely safe; above 5% needs careful review.

### 5.2 Search

- bc-tender-scraper's company search relies on `name`/`display_name`/`canonical_vendor_name` indexes. Changing `entity_role` does not affect these indexes, but search results may omit `generic_bucket` rows if the role is added to exclusion lists.
- tenderscope-kg `search_fts` searches all `COMPANY` nodes. If low-confidence nodes are filtered at query time, search recall drops for thinly evidenced companies. This is usually desirable.

### 5.3 GraphDB

- **No schema change required** for A, B, or D in the near term.
- The graph importer would simply classify some incoming companies differently (or skip them).
- For Recommendation D, graph nodes gain a computed `confidence` score; the node stays, but engines may ignore it below a threshold.
- Long-term, if `generic_bucket` rows are imported at all, they could become `COMPANY_ALIAS` to a placeholder or be skipped entirely. That is a schema/behaviour decision to make separately.

### 5.4 Executive Decision Engine

- EDE reads from sub-engines. If CI removes bad competitors, `market_position`, `risk_register`, and `strategic_priorities` become more accurate.
- The biggest win is reducing false positives in the `competition_detected` risk and `direct_competitors` list.
- No EDE code changes are required if the filtering happens upstream in the engines.

### 5.5 Narrator / NarratorCache

- The cache key includes `evidence_hash`. When upstream CI results change, the hash changes and the cache miss triggers a fresh narration. This is self-healing.
- Strategic memory is currently disabled. **Do not enable it until validation is complete**, or bad entities will be stored as historical artifacts.

### 5.6 Session Memory / Telegram / n8n

- No impact. These layers store only session identity and final agent output.

---

## 6. Validation Plan

### 6.1 Phase 0: Baseline

1. Run `prototype_canonical_impact.py` against a production snapshot. Save output as `baseline.json`.
2. Run `prototype_graph_confidence.py` against the same snapshot. Save output as `baseline_graph.json`.
3. Record current CI competitor lists for the sample subjects and a random sample of 100 additional canonical companies.

### 6.2 Phase 1: Candidate filter simulation

1. Re-run prototypes with proposed rules enabled.
2. Diff baseline vs candidate outputs.
3. Manual review of every removed competitor in the sample sets.
4. Check for unintended loss of real companies.

### 6.3 Phase 2: Shadow mode (optional)

1. Deploy A/B/D in a shadow branch that computes the new roles/confidence but does not persist them.
2. Log divergence from production for 1–2 pipeline runs.
3. Review logs for anomalies.

### 6.4 Phase 3: Limited rollout

1. Apply changes to a small cohort (e.g. construction sector only).
2. Monitor CI competitor counts, narrator cache hit rates, and user feedback.
3. Roll back if competitor loss exceeds acceptable threshold.

---

## 7. Decision Criteria

| Question | Go / No-go threshold |
|---|---|
| Generic-bucket canonicals removed | >90% of known generic buckets are caught |
| Real company false-positive removals | <0.5% of current CI competitors are real companies removed |
| Role change count | <1% of all companies change role |
| Graph confidence coverage | >80% of real companies score >= 0.5 |
| Low-confidence false negatives | <5% of excluded companies are real, active bidders |

If any threshold is missed, tune the heuristic or confidence formula and re-run.

---

## 8. Current Environment Findings

- **Local tenderscope-kg graph DB** (`@c:\Users\DAVIDSURF\Projects\tenderscope-kg\.tkg\graph.db`) contains zero `COMPANY` nodes, confirming it is a development-only code graph. Production-scale validation requires a business graph snapshot.
- **Local bc-tender-scraper PostgreSQL** is not running. The script correctly failed to connect to `localhost:5432`. Production validation requires `DATABASE_URL` pointing to a read replica or snapshot.
- Previous production API observations confirmed that `Architect` (`id=548732`) is a canonical generic bucket, while `Educator` does not exist in the production dataset.

---

## 9. Recommended Next Steps

1. **Provision a production read replica / sanitized snapshot** for both the scraper PostgreSQL database and the tenderscope-kg graph.
2. **Run the prototypes** against that snapshot and populate this report with real numbers.
3. **Tune the confidence formula** using the snapshot data and a small labelled validation set.
4. **Decide on go/no-go** for Phase 1 based on the thresholds in Section 7.
5. If Phase 1 is approved, implement A + B behind a feature flag and run a shadow pass before writing to canonical tables.

---

## 10. Files in this Branch

- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\scripts\prototype_canonical_impact.py`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\scripts\prototype_graph_confidence.py`
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\docs\architecture\entity-validation-impact-analysis.md`
