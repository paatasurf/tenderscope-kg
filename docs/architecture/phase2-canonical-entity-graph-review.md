# Phase 2 – Canonical Entity & Graph Architecture Review

**Status:** Architecture review only. No code changes.  
**Scope:** bc-tender-scraper canonical entity pipeline + tenderscope-kg graph / intelligence engines + voice-n8n-agent consumption layer.  
**Date:** 2026-07-10

---

## 1. Executive Summary

The platform has two logically separate entity layers:

1. **bc-tender-scraper** builds a relational canonical company registry from permit/tender applicants via regex parsing, normalized-key resolution, and deterministic merge.
2. **tenderscope-kg** builds a graph-native company registry (with immutable `company_uid`, `canonical_name` dedup, and `ALIAS_OF`/`SAME_AS` evidence) that is consumed by the voice agent through MCP/REST.

The recent production defect where generic bucket names (e.g. `Architect`) and person names with parenthetical nicknames leaked into Competitive Intelligence is **symptomatic**, not isolated. The root cause is that bc-tender-scraper's canonical merge treats generic-bucket detection as a *deprioritization* signal rather than a hard validation gate, and the same heuristic layer is reused at insert-time, resolution-time, and CI-time without confidence metadata.

This report finds that the architecture is fundamentally sound but has **three recurring weak spots**:

1. **Validation happens too late.** Generic/person classification is applied after rows already exist as `standalone` or even `canonical`, rather than at identity creation.
2. **Entity roles are too coarse.** Four roles (`canonical`, `applicant_alias`, `standalone`, `probable_person`) cannot express "verified real company", "generic bucket", "placeholder", or "low-confidence alias".
3. **Graph evidence is underutilized.** tenderscope-kg already stores `IdentityEvidence` on `ALIAS_OF`/`SAME_AS` edges and `confidence` on entities, but bc-tender-scraper's SQL-based CI and resolution do not consume it.

The most valuable, lowest-risk improvement is to **introduce an explicit validation gate before canonical promotion**, reuse the existing generic-bucket and person-name classifiers, and propagate a confidence/validation flag into both the SQL analytics layer and the graph. A separate, higher-effort improvement is to **migrate canonical authority gradually from bc-tender-scraper integer IDs to tenderscope-kg `company_uid`**, which would let the graph's evidence model become the single source of truth for identity quality.

No recommendation below requires rebuilding the graph or changing canonical IDs in the near term. Any recommendation that eventually does is explicitly flagged.

---

## 2. Scope & Methodology

### 2.1 In scope

- Raw ingestion → parsing → resolution → canonical merge → entity roles → canonical graph → Competitive Intelligence → Executive Decision Engine → Strategic/Session Memory → Narrator.
- All review questions in the Phase 2 brief.

### 2.2 Out of scope / constraints

- No code changes.
- No PR.
- No modification of GraphDB schema, cache logic, or production behaviour.
- No redesign of the overall system.

### 2.3 Methodology

- Code review of `bc-tender-scraper/pipeline/{company_canonical_merge, parsed_identity_canonical_merge, company_resolution, identity_parser, company_name_heuristics, competitive_intel/cohort}.py`.
- Code review of `tenderscope-kg/src/tenderscope_kg/{domain, repository/_base, executive_decision, server_engines, mcp_server}.py`.
- Code review of `voice-n8n-agent/app/intelligence/{memory, strategic_memory, narrator_cache, narrator, orchestrator, deps}.py`.
- Production observation: verified `Architect` (`id=548732`) is a canonical generic bucket; `Educator` is absent from production.

---

## 3. Current Architecture Overview

### 3.1 Logical data flow

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Raw ingestion (scrapers, CSV imports, legacy DB)                            │
│  • Permit records with applicant, contractor, description, builder fields   │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Company Discovery (pipeline/company_discovery.py)                           │
│  • Priority-ranked candidates from structured fields, description regex,    │
│    and parsed applicant identity                                            │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Identity Parser (pipeline/identity_parser.py)                               │
│  • Regex extraction of DBA / O/A / c/o / JV / partnership / trade-name      │
│  • Returns person_name, business_name, relationship_type, confidence        │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Company Resolution (pipeline/company_resolution.py)                         │
│  • Normalized-key lookup in in-memory index                                 │
│  • Person-name heuristic skip                                               │
│  • Conflict review when >1 candidate matches                                │
│  • Creates new `Company` row as `standalone` if no match                       │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Canonical Merge – two pipelines                                             │
│  Scenario A: company_canonical_merge.py                                     │
│    • Groups by normalized key + forced canonical IDs                        │
│    • Safe tier requires explicit DBA or high-confidence signals             │
│    • Excluded probable-person groups marked as `probable_person`            │
│  Scenario B: parsed_identity_canonical_merge.py                             │
│    • Groups by parsed identity key                                          │
│    • `_choose_primary_member` / `_choose_primary_root` can fall back to      │
│      generic-bucket members when all members are generic                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Entity Roles (db/company_canonical_constants.py)                            │
│  • canonical, applicant_alias, standalone, probable_person                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ SQL Analytics + CI (bc-tender-scraper)                                        │
│  • company_analytics_entity_filter excludes alias & probable_person         │
│  • CI cohort SQL + post-filter (`is_probable_person_name`)                  │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Graph Import / Sync (tenderscope-kg importers)                              │
│  • bc_scraper_pg_importer: two-pass company import                           │
│  • `repo.resolve_company_uid(name, source, attributes)` is single entry point │
│  • ALIAS_OF edges carry IdentityEvidence                                    │
│  • scraper_id attached as attribute for ID compatibility                    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ tenderscope-kg Intelligence Engines                                         │
│  • CIE, RIE, CeI, BIE, OIE → EDE orchestration                              │
│  • MCP + REST transports share one EngineSet                                │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ voice-n8n-agent                                                              │
│  • IntelligenceOrchestrator → tool executor → tenderscope-kg MCP/REST      │
│  • SessionMemory, StrategicMemory, NarratorCache, ClaudeNarrator            │
│  • Strategic memory currently feature-flagged off                           │
└─────────────────────────────────────────────────────────────────────────────┘
```

### 3.2 Key abstraction boundaries

| Layer | Authority | Identity key | Validation model |
|---|---|---|---|
| bc-tender-scraper SQL registry | Relational canonical merge | `Company.id` (integer) | Regex + heuristic filters |
| tenderscope-kg graph | Graph-native canonical registry | `company_uid` (CMP-*) | `canonical_name` dedup + `IdentityEvidence` on edges |
| voice-n8n-agent | Consumer / narrator | Either scraper id or graph uid | Claim validation + confidence blending |

The bc-tender-scraper layer is currently the **authoritative producer** of company rows; tenderscope-kg is the **authoritative consumer + enricher** of those rows for the agent.

---

## 4. Detailed Area Reviews

### 4.1 Canonical Merge

#### Current behaviour

There are two merge implementations:

- **Scenario A** (`pipeline/company_canonical_merge.py`): groups by normalized key, partitions into `SAFE_DBA`, `EXCLUDED_PROBABLE_PERSON`, and `EXCLUDED_REVIEW`. Only `SAFE_DBA` groups are applied automatically. The person-name heuristic is used at group classification time (`classify_merge_group`).
- **Scenario B** (`pipeline/parsed_identity_canonical_merge.py`): groups by parsed-identity key, selects a primary member with `_choose_primary_member` and a primary root with `_choose_primary_root`. Both helpers deprioritize generic-bucket names but **fall back** to them if every member is generic:

```python
# pipeline/parsed_identity_canonical_merge.py:255-272
def _choose_primary_member(members):
    eligible = [m for m in members if not is_generic_bucket_company_name(m.name)]
    pool = eligible or members          # <-- fallback
    return sorted(pool, key=_member_score, reverse=True)[0]

def _choose_primary_root(members, company_rows):
    scores = Counter()
    for member in members:
        if is_generic_bucket_company_name(member.name):
            continue
        ...
    if not scores:                      # <-- fallback
        for member in members:
            ...
```

This means a single-member group whose only member is named `Architect` will select `Architect` as the canonical primary and promote it to `entity_role='canonical'`.

#### Why generic buckets become canonical

1. **No hard gate.** `is_generic_bucket_company_name` is consulted, but if it returns `True` for *all* candidates, the algorithm still picks the "best" generic bucket.
2. **Single-member groups.** A permit applicant named exactly `Architect` creates one `Company` row. When parsed-identity merge runs, that row forms a single-member group. Because there are no non-generic alternatives, the generic bucket wins by fallback.
3. **No re-classification after promotion.** Once a row is `canonical`, later heuristic improvements do not retroactively demote it unless a new merge run explicitly reassigns it.

#### Why person-like entities survive canonical merge

- `classify_merge_group` in Scenario A only flags a whole group as `EXCLUDED_PROBABLE_PERSON` if the *group display name* looks like a person. If the group contains a mix of person and business names, or if the display name is a DBA-derived business name, the person-name members can be swept into an alias relationship.
- Scenario B does not appear to re-check `is_probable_person_name` on the selected primary name; it relies on the upstream resolution step to have skipped persons, but standalone rows created before the heuristic was improved remain eligible.

#### Other edge cases

- **City/trade suffixes treated as person names.** `Winmar Vancouver` is a real company but fits the two-token letter-only heuristic.
- **DBA family prefix matching too greedily.** `_collect_candidate_ids` uses `startswith` on normalized keys for DBA families; a short trade key could over-match unrelated companies.
- **Forced canonical IDs override evidence.** `FORCED_CANONICAL_IDS_BY_KEY` can stabilize known families, but maintaining it is manual and brittle.

### 4.2 Entity Roles

#### Current model

```python
# db/company_canonical_constants.py:27-47
ENTITY_ROLE_CANONICAL = "canonical"
ENTITY_ROLE_APPLICANT_ALIAS = "applicant_alias"
ENTITY_ROLE_STANDALONE = "standalone"
ENTITY_ROLE_PROBABLE_PERSON = "probable_person"
```

`COMPANY_ANALYTICS_EXCLUDED_ENTITY_ROLES` excludes only `applicant_alias` and `probable_person` from default analytics.

#### Evaluation of richer states

| Proposed state | Value | Cost / risk |
|---|---|---|
| `verified_company` | Distinguishes companies confirmed by external identifier (BC Registry, BN) from heuristic-only companies. High value for CI trust. | Requires new data source integration or backfill; queries must be updated. |
| `probable_company` | Marks companies that look real but have low evidence. Allows CI to gate consumption by confidence. | Useful, but overlaps with `confidence` field; adds role churn. |
| `generic_bucket` | Explicitly labels names like `Architect`/`Educator`. Prevents CI consumption without downstream filters. | Requires canonical-merge change and reprocessing; high value. |
| `placeholder` | For rows created as spatial/temporal placeholders during import. | Minor value today; could help future importers. |
| `unresolved` | For conflicts requiring human review. | Already implicitly handled by `RESOLUTION_STATUS_REVIEW`; formalizing it would add clarity. |

**Verdict:** Adding `verified_company` and `generic_bucket` would meaningfully improve the architecture. They should be derived from existing signals (external IDs, generic bucket list) and should not be manually assigned.

### 4.3 Graph Intelligence

#### Current evidence model

`tenderscope-kg` already has the right primitives:

- `BizEntity.confidence` (per-entity data quality score).
- `IdentityEvidence` on `ALIAS_OF` and `SAME_AS` edges with `confidence`, `reason`, `explanation`, `evidence` list.
- `CompanyIdentity` read model that assembles canonical entity + aliases + external IDs + merge candidates.

#### How GraphDB could assist validation

1. **Relationship confidence propagation.** A canonical company with many high-confidence `ALIAS_OF` edges from distinct data sources is more trustworthy than one with no aliases.
2. **External identifier convergence.** `attach_identifier` is the intended path; if a bc-tender-scraper company has a BC Registry number, that should be reflected in graph attributes and boost entity confidence.
3. **Graph-based disambiguation.** `SAME_AS` merge candidates could be surfaced to bc-tender-scraper before CI is computed, reducing duplicate competitor entries.

#### Current gap

bc-tender-scraper's CI and resolution do not read from the graph. They operate entirely on SQL heuristics. The graph is therefore a **read-only mirror** for the agent, not an active validator.

**Verdict:** The graph model is capable of replacing some regex heuristics, but only after bc-tender-scraper starts consuming graph evidence. That is a medium-effort integration, not a quick fix.

### 4.4 Company Resolution

#### Current flow

1. `resolve_company_name(raw)` extracts display name, canonical key, DBA flag.
2. `CompanyResolver` loads all companies into memory and indexes by normalized `name`/`display_name`/`canonical_vendor_name`.
3. If `is_probable_person_name(display_name)` and no explicit DBA, return `PERSON_SKIP`.
4. Compute BC-incorporation confidence from name markers.
5. If one candidate → resolve; if multiple → review/conflict.

#### Strengths

- Deterministic, reproducible, fully in-memory for a batch.
- Rollback snapshots on merge apply.
- Conflict review log for ambiguous matches.

#### Remaining edge cases

- **Person-name heuristic brittleness.** Two-token real companies (`RodRozen Designs`, `Winmar Vancouver`) are misclassified.
- **Ambiguity when DBA family prefix is short.** `MIN_DBA_FAMILY_PREFIX_LEN=4` can still match broad prefixes.
- **No external ID disambiguation.** BC Registry numbers are not used to break ties.
- **No feedback loop.** Misclassifications found in CI are not fed back to resolution.

### 4.5 Competitive Intelligence

#### bc-tender-scraper CI

- SQL cohort based on sector/trade/city + quality gate (`total_projects >= 2` or `award_count >= 1`).
- `company_analytics_entity_filter()` excludes `applicant_alias` and `probable_person` at SQL level.
- Post-filter `_exclude_misclassified_person_standalone` catches person-name standalone rows leaking through.

Current weakness: CI consumes any `canonical` or `standalone` row that passes quality gates, regardless of whether the name is a generic bucket.

#### tenderscope-kg CI (CeI)

- Operates on the graph: `direct_competitors`, `emerging_competitors`, `competitive_pressure`, etc.
- Reads `COMPANY` nodes and `AWARDED_TO` / `SUBMITTED_BID` / `PARTICIPATED_IN` relationships.
- If a generic bucket node exists in the graph, CeI will treat it as a real competitor.

#### Should CI consume only fully validated canonical entities?

Yes, ideally. The cleanest long-term model is:

- bc-tender-scraper marks generic buckets and low-confidence rows explicitly.
- tenderscope-kg importer skips or tags these rows (or imports them as `COMPANY_ALIAS`/`placeholder` rather than `COMPANY`).
- CI engines read only `COMPANY` nodes with `confidence >= threshold`.

This avoids adding more downstream filters.

### 4.6 Downstream Consumers

| Consumer | How it uses upstream data | Sensitivity to bad entities |
|---|---|---|
| **Voice Agent / ClaudeAgent** | Calls tenderscope-kg tools; receives CI/EDE payloads. | Narrator can mention bogus competitors; trust degrades. |
| **Narrator** | Generates natural language from evidence; uses `evidence_hash` + `strategic_context_hash` for cache key. | Bad entities produce bad narratives; cached bad narratives persist 7 days. |
| **NarratorCache** | Keys on `(company_id, evidence_hash, engine_version, prompt_version, strategic_context_hash, response_focus)`. | If upstream entity set changes, evidence_hash changes → cache miss → fresh (correct) narration. Cache is self-healing. |
| **Executive Decision Engine** | Blends all five engines; produces priorities, risks, narrative. | A single bogus competitor can inflate risk register or misclassify market position. |
| **Strategic Memory** | Feature-flagged off in production. When on, writes timeline/semantic/strategic artifacts per tool call. | Would persist bad evidence as historical facts; highest risk if enabled prematurely. |
| **Session Memory** | Stores `company_id` + `company_name` per session. | Low risk; only the active company identity is stored. |
| **Cache (reasoning, live)** | Caches tool results and reasoning steps. | Bad entity in cache poisons subsequent similar requests until TTL. |
| **Telegram / n8n** | Receive agent output. | Only exposed to final narration; indirect risk. |

**Compatibility conclusion:** Any upstream improvement that removes or reclassifies bad entities is **safe** for all consumers because it improves the input data. However, changes that *rename* roles, *remove* canonical IDs, or *alter* the graph schema would require coordinated updates in voice-n8n-agent and cache invalidation.

---

## 5. Root Cause Analysis

The generic-bucket and person-name leaks share a common root cause pattern:

> **Validation is downstream of persistence.**

In bc-tender-scraper, a row is inserted as `standalone` (or promoted to `canonical`) based on normalized-key matching and simple heuristics. Only later, when the row reaches CI or analytics, is it re-evaluated. By then, it already has permit links, award links, and a graph UID.

Specific root causes:

1. **Canonical merge fallback to generic buckets.** `_choose_primary_member` and `_choose_primary_root` in `parsed_identity_canonical_merge.py` do not forbid generic-bucket primaries.
2. **No confidence/validation flag on canonical rows.** A `canonical` row is treated as equally trustworthy whether it has one permit link and no external ID or a hundred links and a BC Registry number.
3. **CI consumes rows by role, not by confidence.** The SQL filter excludes `applicant_alias` and `probable_person`, but not `generic_bucket` or `low_confidence` states.
4. **Graph importer mirrors bc-tender-scraper decisions.** tenderscope-kg faithfully imports whatever canonical company the scraper produces, so bad entities propagate to the agent layer.

---

## 6. Strengths

1. **Deterministic, auditable merge.** Every merge run produces rollback snapshots; operations are reversible.
2. **Separation of concerns.** bc-tender-scraper owns the relational canonical pipeline; tenderscope-kg owns graph intelligence; voice agent owns narration. Boundaries are clean.
3. **Rich graph primitives.** `IdentityEvidence`, `SAME_AS`, `ALIAS_OF`, and external ID keys provide the foundation for evidence-based validation.
4. **Feature-flagged memory.** Strategic memory is disabled in production, preventing premature persistence of low-quality artifacts.
5. **Self-healing narrator cache.** Cache keys include evidence hash, so upstream corrections automatically invalidate stale narrations.

---

## 7. Weaknesses & Technical Debt

| Weakness | Impact | Location |
|---|---|---|
| Generic bucket fallback in merge | Generic names become canonical | `parsed_identity_canonical_merge.py:255-272` |
| Coarse 4-state role model | Cannot express confidence/quality of canonical rows | `db/company_canonical_constants.py:27-47` |
| Regex-only person-name heuristic | False positives for real two-token companies | `pipeline/company_name_heuristics.py` |
| No external-ID tie-breaking in resolution | Missed merge opportunities, ambiguous conflicts | `pipeline/company_resolution.py:160-205` |
| CI reads rows by role, not confidence | Generic buckets eligible for competitor lists | `pipeline/competitive_intel/cohort.py` |
| Graph evidence unused by scraper | Two parallel quality models | `tenderscope-kg/repository/_base.py`, `bc-tender-scraper/pipeline/*` |
| Manual forced-canonical map | Operational toil, risk of stale overrides | `db/company_canonical_constants.py` (`FORCED_CANONICAL_IDS_BY_KEY`) |
| Single-pass confidence in EDE | Executive narrative cannot down-weight low-confidence entities | `tenderscope-kg/executive_decision.py` |

---

## 8. High-Risk Areas

1. **Canonical merge promotions.** Any change here affects permit/award foreign keys, graph UIDs, and CI output. Must be accompanied by rollback and reprocessing capability.
2. **Strategic Memory enablement.** Once turned on, bad entities become historical facts. Do not enable until the validation gate is in place.
3. **Graph schema / ID migration.** Moving authority from scraper integer IDs to `company_uid` is valuable but touches importers, REST endpoints, cache keys, and the agent.
4. **External identifier backfill.** Attaching BC Registry numbers at scale requires data source integration and validation of identifier uniqueness.

---

## 9. Architectural Improvement Opportunities

### 9.1 Recommendation A – Add a validation gate before canonical promotion

**What:** Before any row is promoted to `entity_role='canonical'`, require that it is **not** a generic bucket and **not** a probable person. Single-member generic-bucket groups should remain `standalone` (or move to a new `generic_bucket` role), not become canonical.

**Where:** `pipeline/parsed_identity_canonical_merge.py:255-272` and `pipeline/company_canonical_merge.py:684-731`.

**Expected benefit:** Eliminates the root cause of generic buckets and person names leaking into canonical status; removes need for downstream CI filters.

**Regression risk:** Medium. Affects canonical merge output and downstream CI/graph. Requires careful dry-run and rollback testing.

**Implementation complexity:** Low–Medium. Reuses existing `is_generic_bucket_company_name` and `is_probable_person_name` functions.

**Migration complexity:** Medium. Existing canonical generic buckets (e.g. `Architect`) need a one-time demotion or reprocessing to `standalone`/`generic_bucket`. Does **not** require rebuilding the graph or changing canonical IDs for correctly classified rows.

**Production impact:** Changes entity_role for some rows; shifts CI competitor lists where generic buckets were previously included.

**Requires reprocessing historical data:** Yes, for affected canonical rows.

**Requires changing canonical IDs:** No.

**Requires rebuilding graph:** No.

---

### 9.2 Recommendation B – Introduce explicit `generic_bucket` and `verified_company` roles

**What:** Extend the entity role enum with at least two new states:

- `generic_bucket` — for names that are clearly not real companies (e.g. `Architect`, `Educator`). Excluded from analytics and CI by default.
- `verified_company` — for canonical companies with at least one strong external identifier (BC Registry, Business Number, confirmed domain) or multiple independent high-confidence aliases.

**Where:** `db/company_canonical_constants.py:27-47`; SQL filters in `db/company_analytics.py`; CI cohort queries; graph importer.

**Expected benefit:** Richer trust model; CI and EDE can consume only verified/high-confidence entities; generic buckets are explicitly quarantined.

**Regression risk:** Medium. All queries that filter by role must be updated. Risk of over-excluding real companies if `verified_company` criteria are too strict.

**Implementation complexity:** Medium. Requires schema/logic changes in scraper and graph importer.

**Migration complexity:** Low–Medium. Can be backfilled from existing external IDs and generic-bucket list. `verified_company` can start empty and be populated by a background job.

**Production impact:** CI/analytics output changes for unverified companies if gating is too aggressive. Recommended to keep `verified_company` informational only in Phase 1; use it for filtering in Phase 2 after validation.

**Requires reprocessing historical data:** Yes, for role backfill.

**Requires changing canonical IDs:** No.

**Requires rebuilding graph:** No.

---

### 9.3 Recommendation C – Attach external identifiers during import and use them for disambiguation

**What:** When bc-tender-scraper obtains external identifiers (BC Registry, Business Number, GST), attach them to the corresponding graph company via `repo.attach_identifier`. Use these identifiers in `CompanyResolver._collect_candidate_ids` to break ties and avoid conflicts.

**Where:** Importers in both projects; `pipeline/company_resolution.py`; `tenderscope-kg/repository/_base.py:340-383`.

**Expected benefit:** Reduces ambiguous matches; increases confidence in canonical identity; aligns with the existing graph identity standard.

**Regression risk:** Low–Medium. Requires validating identifier uniqueness and source reliability.

**Implementation complexity:** Medium. Depends on data source availability and identifier parsing.

**Migration complexity:** Medium. Existing companies can be backfilled; duplicates must be reviewed.

**Production impact:** Improves resolution accuracy; may merge some currently separate companies that share an external ID.

**Requires reprocessing historical data:** Yes, for backfill.

**Requires changing canonical IDs:** No.

**Requires rebuilding graph:** No (only attributes/edges added).

---

### 9.4 Recommendation D – Propagate graph confidence into CI and EDE consumption

**What:** Ensure the graph importer sets `BizEntity.confidence` based on evidence quality (number of aliases, external IDs, source diversity). Update CI/EDE engines to skip or down-weight `COMPANY` nodes below a confidence threshold.

**Where:** `tenderscope-kg/importers/bc_scraper_pg_importer.py`; `tenderscope-kg/competitive_intelligence.py`; `tenderscope-kg/executive_decision.py`.

**Expected benefit:** Uses the existing graph evidence model as a runtime quality gate; no new heuristic code needed in CI.

**Regression risk:** Low if threshold is conservative. Risk of hiding real but low-evidence companies.

**Implementation complexity:** Medium. Requires confidence scoring formula and threshold configuration.

**Migration complexity:** Low. Can be rolled out with threshold=0 initially, then raised gradually.

**Production impact:** CI/EDE output changes for low-confidence graph nodes.

**Requires reprocessing historical data:** No (confidence can be computed on read or backfilled lazily).

**Requires changing canonical IDs:** No.

**Requires rebuilding graph:** No.

---

### 9.5 Recommendation E – Improve the person-name heuristic with graph/location signals

**What:** Keep the regex heuristic as a fast filter, but augment it with:

- A deny-list of known cities/provinces (so `Winmar Vancouver` is not a person).
- A deny-list of trade suffixes (`Designs`, `Homes`, `Builders`, `Construction`).
- Optional cross-check against the graph: if a name has `ALIAS_OF` edges to real companies or many `AWARDED_TO` relationships, do not treat it as a person.

**Where:** `pipeline/company_name_heuristics.py`; optionally `tenderscope-kg` relationship checks.

**Expected benefit:** Reduces false positives like `RodRozen Designs` and `Winmar Vancouver` without broadening the heuristic for genuine person names.

**Regression risk:** Low–Medium. Each new rule must be tested against the regression set. Risk of new false negatives.

**Implementation complexity:** Low.

**Migration complexity:** Low. No data migration; affects future classifications. Existing misclassified rows would need a reprocessing pass to demote.

**Production impact:** Reduces person-name false positives in CI and resolution.

**Requires reprocessing historical data:** Optional, for existing misclassified rows.

**Requires changing canonical IDs:** No.

**Requires rebuilding graph:** No.

---

### 9.6 Recommendation F – Long-term: migrate canonical authority to tenderscope-kg `company_uid`

**What:** Make tenderscope-kg the system of record for canonical identity. bc-tender-scraper continues to scrape and normalize, but all canonical decisions (merge, alias, external ID) happen through `repo.resolve_company_uid`. The scraper stores `company_uid` alongside its integer `Company.id`.

**Where:** Both projects; importer architecture; REST/MCP transport; voice agent.

**Expected benefit:** Single identity model; immutable UIDs; graph evidence becomes authoritative; eliminates divergence between SQL and graph registries.

**Regression risk:** High. Touches every system.

**Implementation complexity:** High.

**Migration complexity:** High. Requires:

- Backfill of `company_uid` for all existing companies.
- Migration of foreign keys in permits, awards, analytics.
- Updates to CI endpoints and agent cache keys.
- Careful handling of both scraper integer IDs and graph UIDs during transition.

**Production impact:** Large. Must be phased over multiple deployments.

**Requires reprocessing historical data:** Yes.

**Requires changing canonical IDs:** No new UIDs are created, but the *authority* for canonical decisions moves. Existing `Company.id` values can remain as internal references.

**Requires rebuilding graph:** No, but graph must be the source of truth going forward.

**Verdict:** This is the architecturally correct end state, but it is a **multi-quarter initiative**, not a bug fix. The earlier recommendations (A–E) should be implemented first to reduce risk before attempting F.

---

## 10. Production Risk Assessment Matrix

| Recommendation | Production behaviour change | Recall impact | Migration effort | Graph rebuild | Canonical IDs change | Recommended timing |
|---|---|---|---|---|---|---|
| A – Validation gate before canonical promotion | Yes (removes generic/person canonicals) | Removes false positives only | Medium | No | No | Phase 1 (next) |
| B – New entity roles | Yes (new role states) | Depends on gating | Medium | No | No | Phase 1 (with A) |
| C – External identifier disambiguation | Yes (better merging) | Likely improves precision | Medium | No | No | Phase 2 |
| D – Graph confidence in CI/EDE | Yes (low-confidence nodes filtered) | Low if threshold conservative | Low | No | No | Phase 2 |
| E – Improved person-name heuristic | Yes (fewer false positives) | Low if tested | Low | No | No | Phase 1 or 2 |
| F – Migrate canonical authority to KG | Yes (fundamental) | High if rushed | High | No | Authority shift | Phase 3+ |

---

## 11. Recommended Implementation Order

### Phase 1 (low risk, high value)

1. **A + B together:** Add a validation gate before canonical promotion and introduce `generic_bucket` / `verified_company` roles.
   - Keep `verified_company` informational only at first (no CI filtering).
   - Use `generic_bucket` role to quarantine generic buckets from analytics and CI.
   - Reprocess existing canonical generic buckets to `generic_bucket`.
2. **E:** Improve person-name heuristic with city/trade deny-lists.
3. Add regression tests for all three changes.

### Phase 2 (medium risk, high value)

4. **C:** Integrate external identifiers (BC Registry, BN, GST) into resolution and graph import.
5. **D:** Compute and use graph confidence in CI/EDE, starting with a conservative threshold.

### Phase 3 (strategic, high effort)

6. **F:** Design the migration of canonical authority to tenderscope-kg. Execute only after Phase 1 and Phase 2 are stable in production.

---

## 12. Compatibility Notes for Downstream Systems

- **Voice Agent / Narrator:** Phase 1 changes improve input data; no agent changes required. Narrator cache will self-invalidate via evidence-hash changes.
- **Strategic Memory:** Do **not** enable strategic memory writes until Phase 1 is complete, or bad entities will be persisted as historical artifacts.
- **Session Memory:** Unchanged; only stores `(company_id, company_name)`.
- **Telegram / n8n:** Unchanged; receive improved agent output.
- **Cache:** Reasoning/live caches may need manual invalidation if entity classifications change significantly during rollout.

---

## 13. Appendix: Key File References

### bc-tender-scraper

- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_name_heuristics.py` – person-name heuristic.
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\parsed_identity_canonical_merge.py:255-272` – generic bucket fallback.
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_resolution.py:93-294` – resolution logic.
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\company_canonical_merge.py:56-62` – merge group classification.
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\db\company_canonical_constants.py:27-47` – entity roles.
- `@c:\Users\DAVIDSURF\Projects\bc-tender-scraper\pipeline\competitive_intel\cohort.py` – CI cohort construction.

### tenderscope-kg

- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\entities.py` – `BizEntity`, `IdentityEvidence`, `CompanyIdentity`.
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\domain\kinds.py` – `BizEntityKind`, `BizRelationKind`, `EXTERNAL_ID_KEYS`.
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\repository\_base.py:340-470` – `attach_identifier`, `company_identity`, `resolve_company_uid`.
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\executive_decision.py` – EDE orchestration.
- `@c:\Users\DAVIDSURF\Projects\tenderscope-kg\src\tenderscope_kg\server_engines.py` – shared engine factory.

### voice-n8n-agent

- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\memory.py` – session memory.
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\strategic_memory.py` – strategic memory.
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\narrator_cache.py` – narrator response cache.
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\intelligence\orchestrator.py` – orchestration wiring.
- `@c:\Users\DAVIDSURF\Projects\voice-n8n-agent\app\deps.py` – dependency graph.
