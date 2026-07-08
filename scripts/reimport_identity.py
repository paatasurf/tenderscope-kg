"""
Phase 1 — Company Identity Re-Import
======================================
Clears graph.biz_entities / graph.biz_relations and re-imports the full
production dataset using the new COMPANY / COMPANY_ALIAS identity model.

Usage
-----
    DATABASE_URL=postgres://... python scripts/reimport_identity.py [--dry-run]

Flags
-----
    --dry-run   Print stats from the source tables and current graph, then exit
                without touching any data.

Safety checks
-------------
- Confirms DATABASE_URL is set before touching anything.
- Prints before/after stats.
- All graph writes are idempotent; the script is safe to re-run.

Exit codes
----------
    0  success
    1  error (check stderr)
"""
from __future__ import annotations

import os
import sys
import time
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2

from tenderscope_kg.repository import open_repository
from tenderscope_kg.importers import BCScraperPGImporter


# ── helpers ───────────────────────────────────────────────────────────────────

def banner(msg: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


def fail(msg: str) -> int:
    print(f"\nERROR: {msg}", file=sys.stderr)
    return 1


# ── main ──────────────────────────────────────────────────────────────────────

def main() -> int:
    dry_run = "--dry-run" in sys.argv

    database_url = os.environ.get("DATABASE_URL", "").strip()
    if not database_url:
        return fail("DATABASE_URL is not set.\n"
                    "  export DATABASE_URL=postgres://user:pass@host/db")

    redacted = database_url.split("@")[-1] if "@" in database_url else database_url
    banner(f"Target: ...@{redacted}")
    if dry_run:
        print("  [DRY-RUN] No data will be modified.")

    # ── open graph repo ───────────────────────────────────────────────────────
    try:
        repo = open_repository()
        stats_before = repo.get_stats()
    except Exception as exc:
        return fail(f"Cannot open graph repository: {exc}")

    print(f"\n  Graph before:")
    print(f"    entities  : {stats_before['entities']}")
    print(f"    relations : {stats_before['relations']}")
    by_kind = stats_before.get("by_kind", {})
    for kind in ("company", "company_alias", "tender", "permit", "contract",
                 "organization"):
        print(f"    {kind:<18}: {by_kind.get(kind, 0)}")

    # ── open source connection ────────────────────────────────────────────────
    # Plain connection — BCScraperPGImporter unpacks rows positionally.
    try:
        src_conn = psycopg2.connect(database_url)
    except Exception as exc:
        return fail(f"Cannot connect to source database: {exc}")

    # Verify source table access
    try:
        importer = BCScraperPGImporter(repo=repo, conn=src_conn)
        access = importer.verify_access()
    except Exception as exc:
        return fail(f"Source access check failed: {exc}")

    print(f"\n  Source tables:")
    for table, count in access.items():
        print(f"    {table:<24}: {count}")

    if dry_run:
        src_conn.close()
        print("\n  [DRY-RUN] Exiting without changes.")
        return 0

    # ── clear existing graph data ─────────────────────────────────────────────
    # We TRUNCATE CASCADE so that re-import starts from a clean slate.
    # biz_entity_history is preserved so audit logs survive the migration.
    banner("Step 1 — Clearing existing graph data")
    try:
        clear_conn = psycopg2.connect(database_url)
        with clear_conn.cursor() as cur:
            print("  Truncating graph.biz_relations …")
            cur.execute("TRUNCATE graph.biz_relations CASCADE")
            print("  Truncating graph.biz_entities …")
            cur.execute("TRUNCATE graph.biz_entities CASCADE")
            print("  Resetting UID counters …")
            cur.execute("TRUNCATE graph.graph_uid_map")
        clear_conn.commit()
        clear_conn.close()
        print("  Done.")
    except Exception as exc:
        traceback.print_exc()
        return fail(f"Clear failed: {exc}")

    # ── re-import ─────────────────────────────────────────────────────────────
    banner("Step 2 — Re-importing with new identity model")
    t0 = time.perf_counter()
    try:
        result = importer.run()
    except Exception as exc:
        traceback.print_exc()
        return fail(f"Import failed: {exc}")
    elapsed = time.perf_counter() - t0

    print(f"\n  Import result:")
    print(f"    entities_created  : {result.entities_created}")
    print(f"    entities_updated  : {result.entities_updated}")
    print(f"    relations_created : {result.relations_created}")
    print(f"    relations_updated : {result.relations_updated}")
    print(f"    warnings          : {len(result.warnings)}")
    print(f"    errors            : {len(result.errors)}")
    print(f"    elapsed           : {elapsed:.1f}s")

    if result.errors:
        print(f"\n  First 20 errors:")
        for e in result.errors[:20]:
            print(f"    * {e}")

    if result.warnings:
        print(f"\n  First 10 warnings:")
        for w in result.warnings[:10]:
            print(f"    ~ {w}")

    # ── stats after ───────────────────────────────────────────────────────────
    banner("Step 3 — Graph stats after re-import")
    try:
        stats_after = repo.get_stats()
    except Exception as exc:
        return fail(f"Cannot read post-import stats: {exc}")

    print(f"\n  Graph after:")
    print(f"    entities  : {stats_after['entities']}")
    print(f"    relations : {stats_after['relations']}")
    by_kind_after = stats_after.get("by_kind", {})
    for kind in ("company", "company_alias", "tender", "permit", "contract",
                 "organization"):
        print(f"    {kind:<18}: {by_kind_after.get(kind, 0)}")

    src_conn.close()

    n_errors = len(result.errors)
    if n_errors:
        print(f"\n  COMPLETED WITH {n_errors} ERRORS — see above.")
        return 1

    print(f"\n  SUCCESS — re-import complete.")
    print(f"  Run scripts/verify_identity.py next to generate the verification report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
