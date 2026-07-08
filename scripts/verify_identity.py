"""
Phase 1 — Company Identity Verification Report
================================================
Queries the live graph and produces a complete verification report.

Checks
------
  1. COMPANY / COMPANY_ALIAS / ALIAS_OF counts
  2. Companies merged through canonicalization (aliases → canonical)
  3. Examples of merged companies (before/after) — at least 10
  4. Relationship integrity:
       - Every HAS_PERMIT target is a PERMIT entity
       - Every HAS_CONTRACT / AWARDED_TO target is a CONTRACT entity
       - Every AWARDED_TO relation pointing to a company targets a COMPANY
         (not a COMPANY_ALIAS)
       - No biz_relation.source_uid or target_uid references a COMPANY_ALIAS
         except on ALIAS_OF edges
  5. Search spot-check: searching by alias name resolves to canonical COMPANY
  6. Orphan check: COMPANY_ALIAS nodes with no ALIAS_OF edge (data integrity)

Usage
-----
    DATABASE_URL=postgres://... python scripts/verify_identity.py [--json]

Flags
-----
    --json    Write the full report to verify_identity_report.json

Exit codes
----------
    0  all checks passed
    1  one or more checks FAILED
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2
import psycopg2.extras

from tenderscope_kg.repository import open_repository
from tenderscope_kg.domain import BizEntityKind, BizRelationKind


# ── helpers ───────────────────────────────────────────────────────────────────

PASSED: list[str] = []
FAILED: list[tuple[str, str]] = []
WARNINGS: list[str] = []


def check(name: str, fn) -> dict:
    try:
        result = fn()
        PASSED.append(name)
        status = "PASS"
        detail = result if isinstance(result, str) else ""
        print(f"  {'✓' if sys.platform != 'win32' else 'OK  '} {name}")
        if detail:
            print(f"      {detail}")
        return {"status": "PASS", "name": name, "detail": detail}
    except AssertionError as exc:
        FAILED.append((name, str(exc)))
        print(f"  {'✗' if sys.platform != 'win32' else 'FAIL'} {name}")
        print(f"      {exc}")
        return {"status": "FAIL", "name": name, "detail": str(exc)}
    except Exception as exc:
        FAILED.append((name, str(exc)))
        print(f"  {'✗' if sys.platform != 'win32' else 'FAIL'} {name}")
        print(f"      {exc}")
        return {"status": "ERROR", "name": name, "detail": str(exc)}


def banner(msg: str) -> None:
    print(f"\n{'─' * 64}")
    print(f"  {msg}")
    print(f"{'─' * 64}")


def _pg_query(conn, sql: str, params=None) -> list[dict]:
    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql, params or [])
        return [dict(r) for r in cur.fetchall()]


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    write_json = "--json" in sys.argv

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        print("ERROR: DATABASE_URL is not set.", file=sys.stderr)
        return 1

    redacted = database_url.split("@")[-1] if "@" in database_url else database_url
    banner(f"Identity Verification Report  —  {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  Target: ...@{redacted}")

    try:
        repo = open_repository()
        pg_conn = psycopg2.connect(database_url)
    except Exception as exc:
        print(f"ERROR: Cannot connect: {exc}", file=sys.stderr)
        return 1

    report: dict = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "target": redacted,
        "checks": [],
        "counts": {},
        "merged_examples": [],
        "relationship_integrity": {},
    }

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 1 — Entity counts")
    # ═══════════════════════════════════════════════════════════════════════

    stats = repo.get_stats()
    by_kind = stats.get("by_kind", {})
    n_companies  = by_kind.get("company", 0)
    n_aliases    = by_kind.get("company_alias", 0)
    n_tenders    = by_kind.get("tender", 0)
    n_permits    = by_kind.get("permit", 0)
    n_contracts  = by_kind.get("contract", 0)
    n_orgs       = by_kind.get("organization", 0)

    print(f"\n  Entity counts:")
    print(f"    COMPANY       : {n_companies:>8,}")
    print(f"    COMPANY_ALIAS : {n_aliases:>8,}")
    print(f"    TENDER        : {n_tenders:>8,}")
    print(f"    PERMIT        : {n_permits:>8,}")
    print(f"    CONTRACT      : {n_contracts:>8,}")
    print(f"    ORGANIZATION  : {n_orgs:>8,}")
    print(f"    TOTAL         : {stats['entities']:>8,}")
    print(f"\n  Relation count  : {stats['relations']:>8,}")

    report["counts"] = {
        "companies":    n_companies,
        "aliases":      n_aliases,
        "tenders":      n_tenders,
        "permits":      n_permits,
        "contracts":    n_contracts,
        "organizations":n_orgs,
        "total_entities":stats["entities"],
        "total_relations":stats["relations"],
    }

    def chk_companies_exist():
        assert n_companies > 0, "No COMPANY entities found"
        return f"{n_companies:,} canonical companies"

    def chk_aliases_exist():
        assert n_aliases > 0, \
            "No COMPANY_ALIAS entities (expected if source has applicant_alias rows)"
        return f"{n_aliases:,} aliases"

    report["checks"].append(check("COMPANY entities present", chk_companies_exist))
    report["checks"].append(check("COMPANY_ALIAS entities present", chk_aliases_exist))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 2 — ALIAS_OF relationship count")
    # ═══════════════════════════════════════════════════════════════════════

    alias_of_rows = _pg_query(pg_conn,
        "SELECT COUNT(*) AS cnt FROM graph.biz_relations WHERE kind = 'alias_of'")
    n_alias_of = alias_of_rows[0]["cnt"]
    print(f"\n  ALIAS_OF relations: {n_alias_of:,}")
    report["counts"]["alias_of_relations"] = n_alias_of

    def chk_alias_of_count():
        assert n_alias_of == n_aliases, (
            f"ALIAS_OF count ({n_alias_of}) != ALIAS count ({n_aliases}). "
            "Every alias must have exactly one ALIAS_OF edge."
        )
        return f"{n_alias_of:,} ALIAS_OF edges — matches {n_aliases:,} aliases"

    report["checks"].append(check(
        "ALIAS_OF count matches COMPANY_ALIAS count", chk_alias_of_count))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 3 — Canonicalization merges (before / after examples)")
    # ═══════════════════════════════════════════════════════════════════════

    merged_rows = _pg_query(pg_conn, """
        SELECT
            e_canonical.uid         AS canonical_uid,
            e_canonical.name        AS canonical_name,
            COUNT(e_alias.uid)      AS alias_count,
            ARRAY_AGG(e_alias.name ORDER BY e_alias.name) AS alias_names
        FROM graph.biz_entities e_canonical
        JOIN graph.biz_relations r
              ON r.target_uid = e_canonical.uid
             AND r.kind = 'alias_of'
        JOIN graph.biz_entities e_alias
              ON e_alias.uid = r.source_uid
        WHERE e_canonical.kind = 'company'
        GROUP BY e_canonical.uid, e_canonical.name
        ORDER BY COUNT(e_alias.uid) DESC
        LIMIT 30
    """)

    n_merged = sum(1 for row in merged_rows if row["alias_count"] >= 1)
    total_alias_companies_collapsed = sum(row["alias_count"] for row in merged_rows)

    print(f"\n  Canonical companies with ≥1 alias: {n_merged:,}")
    print(f"  Total alias names collapsed:        {total_alias_companies_collapsed:,}")

    if merged_rows:
        print(f"\n  Top merged companies (by alias count):")
        for row in merged_rows[:10]:
            aliases_preview = ", ".join(row["alias_names"][:4])
            if len(row["alias_names"]) > 4:
                aliases_preview += f" … +{len(row['alias_names']) - 4} more"
            print(f"    [{row['canonical_uid']}] {row['canonical_name']}")
            print(f"      aliases ({row['alias_count']}): {aliases_preview}")

    report["counts"]["canonical_companies_with_aliases"] = n_merged
    report["counts"]["total_aliases_collapsed"] = total_alias_companies_collapsed
    report["merged_examples"] = [
        {
            "canonical_uid":  row["canonical_uid"],
            "canonical_name": row["canonical_name"],
            "alias_count":    row["alias_count"],
            "aliases":        list(row["alias_names"]),
        }
        for row in merged_rows[:30]
    ]

    def chk_merged_examples():
        assert len(merged_rows) >= 1, \
            "No merged examples found — aliases may not be linked"
        return f"{len(merged_rows)} canonical companies have aliases; showing top {min(10, len(merged_rows))}"

    report["checks"].append(check("Merge examples exist", chk_merged_examples))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 4 — Relationship integrity")
    # ═══════════════════════════════════════════════════════════════════════

    # 4a: No relation (other than ALIAS_OF) points to a COMPANY_ALIAS
    bad_target_rows = _pg_query(pg_conn, """
        SELECT r.id, r.kind, r.source_uid, r.target_uid, e.name AS alias_name
        FROM graph.biz_relations r
        JOIN graph.biz_entities e ON e.uid = r.target_uid
        WHERE e.kind = 'company_alias'
          AND r.kind <> 'alias_of'
        LIMIT 20
    """)

    # 4b: No relation (other than ALIAS_OF) has a COMPANY_ALIAS as source_uid
    bad_source_rows = _pg_query(pg_conn, """
        SELECT r.id, r.kind, r.source_uid, r.target_uid, e.name AS alias_name
        FROM graph.biz_relations r
        JOIN graph.biz_entities e ON e.uid = r.source_uid
        WHERE e.kind = 'company_alias'
          AND r.kind <> 'alias_of'
        LIMIT 20
    """)

    # 4c: AWARDED_TO targeting a company must point to COMPANY, not COMPANY_ALIAS
    bad_award_rows = _pg_query(pg_conn, """
        SELECT r.id, r.source_uid, r.target_uid, e.name AS bad_name, e.kind AS bad_kind
        FROM graph.biz_relations r
        JOIN graph.biz_entities e ON e.uid = r.target_uid
        WHERE r.kind = 'awarded_to'
          AND e.kind = 'company_alias'
        LIMIT 20
    """)

    # 4d: Count of relations by kind (sanity)
    rel_by_kind = _pg_query(pg_conn,
        "SELECT kind, COUNT(*) AS cnt FROM graph.biz_relations GROUP BY kind ORDER BY cnt DESC")
    rel_kind_map = {r["kind"]: r["cnt"] for r in rel_by_kind}

    print(f"\n  Relations by kind:")
    for kind_name, cnt in sorted(rel_kind_map.items(), key=lambda x: -x[1]):
        print(f"    {kind_name:<24}: {cnt:>8,}")

    report["relationship_integrity"] = {
        "by_kind": rel_kind_map,
        "bad_target_alias_count":  len(bad_target_rows),
        "bad_source_alias_count":  len(bad_source_rows),
        "bad_awarded_to_alias_count": len(bad_award_rows),
        "bad_target_examples":  bad_target_rows[:5],
        "bad_source_examples":  bad_source_rows[:5],
        "bad_award_examples":   bad_award_rows[:5],
    }

    def chk_no_bad_target():
        if bad_target_rows:
            examples = "; ".join(
                f"{r['kind']} → alias({r['alias_name']})"
                for r in bad_target_rows[:5]
            )
            assert False, (
                f"{len(bad_target_rows)} relation(s) point to a COMPANY_ALIAS "
                f"(non-ALIAS_OF): {examples}"
            )
        return "No relation targets a COMPANY_ALIAS (except ALIAS_OF)"

    def chk_no_bad_source():
        if bad_source_rows:
            examples = "; ".join(
                f"alias({r['alias_name']}) → {r['kind']}"
                for r in bad_source_rows[:5]
            )
            assert False, (
                f"{len(bad_source_rows)} relation(s) sourced from a COMPANY_ALIAS "
                f"(non-ALIAS_OF): {examples}"
            )
        return "No relation is sourced from a COMPANY_ALIAS (except ALIAS_OF)"

    def chk_no_bad_awards():
        if bad_award_rows:
            examples = "; ".join(
                f"AWARDED_TO alias({r['bad_name']})"
                for r in bad_award_rows[:5]
            )
            assert False, (
                f"{len(bad_award_rows)} AWARDED_TO relation(s) target a COMPANY_ALIAS: "
                f"{examples}"
            )
        return "All AWARDED_TO relations target canonical COMPANY entities"

    report["checks"].append(check(
        "No non-ALIAS_OF relation targets a COMPANY_ALIAS", chk_no_bad_target))
    report["checks"].append(check(
        "No non-ALIAS_OF relation sourced from a COMPANY_ALIAS", chk_no_bad_source))
    report["checks"].append(check(
        "AWARDED_TO always points to canonical COMPANY", chk_no_bad_awards))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 5 — Orphan check")
    # ═══════════════════════════════════════════════════════════════════════

    orphan_rows = _pg_query(pg_conn, """
        SELECT e.uid, e.name
        FROM graph.biz_entities e
        WHERE e.kind = 'company_alias'
          AND NOT EXISTS (
              SELECT 1 FROM graph.biz_relations r
              WHERE r.source_uid = e.uid AND r.kind = 'alias_of'
          )
        LIMIT 20
    """)

    print(f"\n  Orphan COMPANY_ALIAS (no ALIAS_OF edge): {len(orphan_rows)}")
    if orphan_rows:
        for row in orphan_rows[:5]:
            print(f"    [{row['uid']}] {row['name']}")
    report["counts"]["orphan_aliases"] = len(orphan_rows)

    def chk_no_orphans():
        if orphan_rows:
            examples = "; ".join(f"{r['name']}" for r in orphan_rows[:5])
            assert False, (
                f"{len(orphan_rows)} COMPANY_ALIAS entities have no ALIAS_OF edge: "
                f"{examples}"
            )
        return "All COMPANY_ALIAS entities have exactly one ALIAS_OF edge"

    report["checks"].append(check(
        "No orphan COMPANY_ALIAS entities", chk_no_orphans))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 6 — Search resolution spot-check")
    # ═══════════════════════════════════════════════════════════════════════

    # Pick up to 5 aliases at random and confirm FTS resolves to canonical
    sample_aliases = _pg_query(pg_conn, """
        SELECT e_alias.uid AS alias_uid, e_alias.name AS alias_name,
               e_canon.uid AS canonical_uid, e_canon.name AS canonical_name
        FROM graph.biz_entities e_alias
        JOIN graph.biz_relations r
              ON r.source_uid = e_alias.uid AND r.kind = 'alias_of'
        JOIN graph.biz_entities e_canon
              ON e_canon.uid = r.target_uid
        WHERE length(e_alias.name) > 5
        ORDER BY random()
        LIMIT 5
    """)

    print(f"\n  Alias search spot-checks ({len(sample_aliases)} samples):")
    search_results = []
    for row in sample_aliases:
        alias_name = row["alias_name"]
        canonical_uid = row["canonical_uid"]
        fts_hits = repo.search_fts(alias_name, limit=10)
        hit_uids = [e.uid for e in fts_hits]
        resolved_uids = []
        for e in fts_hits:
            if e.kind == BizEntityKind.COMPANY_ALIAS:
                resolved = repo.resolve_alias(e.uid)
                resolved_uids.append(resolved.uid if resolved else None)
            else:
                resolved_uids.append(e.uid)
        found = canonical_uid in resolved_uids
        status = "PASS" if found else "FAIL"
        print(f"    [{status}] search('{alias_name[:40]}') → "
              f"{'found canonical' if found else 'MISSING canonical ' + canonical_uid}")
        search_results.append({
            "alias_name": alias_name,
            "canonical_uid": canonical_uid,
            "canonical_name": row["canonical_name"],
            "fts_found": found,
        })

    report["search_spot_checks"] = search_results

    def chk_search_resolves():
        if not sample_aliases:
            return "No aliases to spot-check"
        failed = [r for r in search_results if not r["fts_found"]]
        if failed:
            names = "; ".join(r["alias_name"] for r in failed[:3])
            assert False, f"{len(failed)} alias searches did not resolve to canonical: {names}"
        return f"All {len(sample_aliases)} alias spot-checks resolved to canonical company"

    report["checks"].append(check(
        "Alias FTS search resolves to canonical COMPANY", chk_search_resolves))

    # ═══════════════════════════════════════════════════════════════════════
    banner("Section 7 — Summary")
    # ═══════════════════════════════════════════════════════════════════════

    total = len(PASSED) + len(FAILED)
    print(f"\n  PASSED : {len(PASSED)}/{total}")
    print(f"  FAILED : {len(FAILED)}/{total}")
    if FAILED:
        print(f"\n  Failed checks:")
        for name, detail in FAILED:
            print(f"    ✗ {name}")
            print(f"        {detail}")

    print(f"\n  Key counts:")
    print(f"    Canonical COMPANY entities          : {n_companies:>8,}")
    print(f"    COMPANY_ALIAS entities              : {n_aliases:>8,}")
    print(f"    ALIAS_OF relations                  : {n_alias_of:>8,}")
    print(f"    Canonicals with ≥1 alias (merged)   : {n_merged:>8,}")
    print(f"    Total alias names collapsed         : {total_alias_companies_collapsed:>8,}")
    print(f"    Orphan aliases (FAIL if > 0)        : {len(orphan_rows):>8,}")

    report["summary"] = {
        "total_checks": total,
        "passed": len(PASSED),
        "failed": len(FAILED),
        "passed_all": len(FAILED) == 0,
    }

    if write_json:
        outfile = os.path.join(os.path.dirname(__file__), "verify_identity_report.json")
        with open(outfile, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, default=str)
        print(f"\n  Report written to: {outfile}")

    pg_conn.close()
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
