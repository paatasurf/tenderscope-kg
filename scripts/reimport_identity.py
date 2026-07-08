"""
Phase 1 — Company Identity Re-Import (with UID Preservation)
=============================================================
Clears graph.biz_entities / graph.biz_relations and re-imports the full
production dataset using the new COMPANY / COMPANY_ALIAS identity model.

UID Preservation
----------------
Before truncating, this script snapshots every existing
(kind, canonical_name) -> uid mapping from graph.biz_entities.

During re-import, BCScraperPGImporter receives this snapshot and passes
the original uid= to put_entity() for every entity that already existed.
This guarantees:

  - Every existing CMP-XXXXXXXX, TEN-XXXXXXXX, PRM-XXXXXXXX etc. survives
    the migration unchanged.
  - No existing REST, dashboard, or AI-agent reference breaks.
  - graph_uid_map counters are set to the current maximums so newly-created
    entities never collide with preserved ones.
  - The migration is fully idempotent: running it a second or third time
    produces no UID changes.

Usage
-----
    DATABASE_URL=postgres://... python scripts/reimport_identity.py [--dry-run]

Flags
-----
    --dry-run   Snapshot UIDs and print the full preservation plan, then exit
                without touching any data.

Safety checks
-------------
- Confirms DATABASE_URL is set before touching anything.
- Snapshots all existing UIDs before any truncation.
- Prints preserved / new / collision counts.
- All graph writes are idempotent; safe to re-run.
- Post-migration verification confirms every preserved UID still exists.

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
import psycopg2.extras

from tenderscope_kg.repository import open_repository
from tenderscope_kg.importers import BCScraperPGImporter
from tenderscope_kg.domain.kinds import canonicalize
from tenderscope_kg.domain import BizEntityKind


# ── helpers ───────────────────────────────────────────────────────────────────

def banner(msg: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {msg}")
    print(f"{'─' * 60}")


def fail(msg: str) -> int:
    print(f"\nERROR: {msg}", file=sys.stderr)
    return 1


# ── UID snapshot ──────────────────────────────────────────────────────────────

def snapshot_uids(database_url: str) -> dict[tuple[str, str], str]:
    """
    Read every (kind, canonical_name) -> uid mapping from graph.biz_entities.

    Returns a dict keyed by (kind_str, canonical_name_str) -> uid_str.

    Taken BEFORE any truncation.  Passed to BCScraperPGImporter so that
    put_entity() receives the original uid= for every entity that already
    existed, guaranteeing stability across the migration.
    """
    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT kind, canonical_name, uid FROM graph.biz_entities")
    snapshot: dict[tuple[str, str], str] = {}
    for row in cur.fetchall():
        snapshot[(row["kind"], row["canonical_name"])] = row["uid"]
    cur.close()
    conn.close()
    return snapshot


def advance_uid_counters(
    database_url: str,
    snapshot: dict[tuple[str, str], str],
) -> None:
    """
    After truncating graph_uid_map, pre-populate it with the max UID number
    per prefix from the snapshot.

    _next_uid() increments then returns, so setting next_val=max_val means
    the next NEW allocation produces max_val+1 — safely above all preserved
    UIDs.  Preserved entities are inserted with explicit uid= and never call
    _next_uid() at all, so there is zero collision risk.
    """
    max_per_prefix: dict[str, int] = {}
    for uid in snapshot.values():
        if "-" not in uid:
            continue
        prefix, _, num_str = uid.partition("-")
        try:
            num = int(num_str)
        except ValueError:
            continue
        if prefix not in max_per_prefix or num > max_per_prefix[prefix]:
            max_per_prefix[prefix] = num

    if not max_per_prefix:
        return

    conn = psycopg2.connect(database_url)
    with conn.cursor() as cur:
        for prefix, max_val in max_per_prefix.items():
            cur.execute(
                """
                INSERT INTO graph.graph_uid_map (prefix, next_val)
                VALUES (%s, %s)
                ON CONFLICT (prefix) DO UPDATE
                    SET next_val = GREATEST(
                        graph.graph_uid_map.next_val,
                        EXCLUDED.next_val
                    )
                """,
                (prefix, max_val),
            )
    conn.commit()
    conn.close()


# ── dry-run report ────────────────────────────────────────────────────────────

def _dry_run_uid_report(
    database_url: str,
    uid_snapshot: dict[tuple[str, str], str],
) -> None:
    """
    Without touching the DB, show what the migration would do to UIDs.

    Checks every company, tender, and permit source row against the snapshot
    and reports: preserved / new / collisions / duplicates.
    """
    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    preserved: list[tuple[str, str, str]] = []   # (kind, name, uid)
    new_entities: list[tuple[str, str]] = []      # (kind, name)

    # ── companies ─────────────────────────────────────────────────────────────
    cur.execute("""
        SELECT id,
               COALESCE(NULLIF(display_name, ''), name, '') AS dname,
               name,
               entity_role
        FROM public.companies
        ORDER BY id
    """)
    for row in cur.fetchall():
        role = str(row["entity_role"] or "")
        name = (row["dname"] or row["name"] or "").strip()
        if not name:
            continue
        kind = (BizEntityKind.COMPANY_ALIAS.value
                if role == "applicant_alias"
                else BizEntityKind.COMPANY.value)
        key = (kind, canonicalize(name))
        if key in uid_snapshot:
            preserved.append((kind, name, uid_snapshot[key]))
        else:
            new_entities.append((kind, name))

    # ── tenders ───────────────────────────────────────────────────────────────
    for table in ("tenders", "commercial_tenders", "arch_tenders"):
        try:
            cur.execute(f"SELECT title FROM public.{table}")  # noqa: S608
            for row in cur.fetchall():
                name = (row["title"] or "").strip()
                if not name:
                    continue
                key = (BizEntityKind.TENDER.value, canonicalize(name))
                if key in uid_snapshot:
                    preserved.append((BizEntityKind.TENDER.value, name, uid_snapshot[key]))
                else:
                    new_entities.append((BizEntityKind.TENDER.value, name))
        except Exception:
            pass

    # ── permits: count only (111k rows — don't enumerate in dry-run) ──────────
    cur.execute("SELECT COUNT(*) AS cnt FROM public.permits")
    n_permits = cur.fetchone()["cnt"]
    cur.execute(
        "SELECT COUNT(*) AS cnt FROM graph.biz_entities WHERE kind = 'permit'"
    )
    permits_preserved = cur.fetchone()["cnt"]
    permits_new = n_permits - permits_preserved

    # ── collision check on snapshot (uid uniqueness) ──────────────────────────
    all_uids = list(uid_snapshot.values())
    n_collisions = len(all_uids) - len(set(all_uids))

    # ── duplicate (kind, canonical_name) in current graph ────────────────────
    cur.execute("""
        SELECT kind, canonical_name, COUNT(*) AS cnt
        FROM graph.biz_entities
        GROUP BY kind, canonical_name
        HAVING COUNT(*) > 1
    """)
    duplicates = cur.fetchall()

    cur.close()
    conn.close()

    company_preserved = [(k, n, u) for k, n, u in preserved if k == "company"]
    alias_new = [(k, n) for k, n in new_entities if k == "company_alias"]

    print(f"\n  UID preservation report (dry-run):")
    print(f"    Companies/tenders PRESERVED (uid stays):   {len(preserved):>8,}")
    print(f"    Companies/tenders NEW (new uid assigned):  {len(new_entities):>8,}")
    print(f"    Permits preserved (approx):                {permits_preserved:>8,}")
    print(f"    Permits new (approx):                      {permits_new:>8,}")
    print(f"    Collisions in snapshot:                    {n_collisions:>8,}  "
          f"{'OK' if n_collisions == 0 else 'FAIL'}")
    print(f"    Duplicate (kind,canonical_name) in graph:  {len(duplicates):>8,}  "
          f"{'OK' if not duplicates else 'FAIL'}")
    print(f"\n    NOTE: {len(alias_new)} COMPANY_ALIAS entities are new")
    print(f"    (they did not exist in the old graph — they will get new UIDs).")

    if n_collisions > 0:
        print(f"\n  BLOCKING: {n_collisions} collision(s) in snapshot — UIDs not unique!")
    if duplicates:
        print(f"\n  BLOCKING: duplicate (kind,canonical_name) pairs:")
        for d in duplicates[:10]:
            print(f"    kind={d['kind']}  canonical_name={d['canonical_name']!r}  count={d['cnt']}")

    print(f"\n  Sample PRESERVED company UIDs (first 10):")
    for kind, name, uid in company_preserved[:10]:
        print(f"    {uid}  {name!r}")

    print(f"\n  Sample NEW entities (first 10):")
    for kind, name in new_entities[:10]:
        print(f"    [{kind}]  {name!r}")

    if n_collisions > 0 or duplicates:
        sys.exit(1)


# ── post-migration verification ───────────────────────────────────────────────

def _verify_uid_preservation(
    database_url: str,
    uid_snapshot: dict[tuple[str, str], str],
) -> None:
    """
    After re-import, confirm every UID in the pre-migration snapshot still
    exists in the graph with the same uid value.

    Raises RuntimeError on any failure so the migration exits with code 1.
    """
    conn = psycopg2.connect(database_url)
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT kind, canonical_name, uid FROM graph.biz_entities")
    after: dict[tuple[str, str], str] = {
        (r["kind"], r["canonical_name"]): r["uid"]
        for r in cur.fetchall()
    }

    missing: list[tuple[str, str, str]] = []
    changed: list[tuple[str, str, str, str]] = []

    for (kind, canon), old_uid in uid_snapshot.items():
        if (kind, canon) not in after:
            missing.append((kind, canon, old_uid))
        elif after[(kind, canon)] != old_uid:
            changed.append((kind, canon, old_uid, after[(kind, canon)]))

    new_uids = [
        (kind, canon, uid)
        for (kind, canon), uid in after.items()
        if (kind, canon) not in uid_snapshot
    ]

    all_after_uids = list(after.values())
    n_collisions = len(all_after_uids) - len(set(all_after_uids))

    print(f"\n  Preserved UIDs confirmed: {len(uid_snapshot) - len(missing) - len(changed):>8,}")
    print(f"  Missing from graph after: {len(missing):>8,}  "
          f"{'OK' if not missing else 'FAIL'}")
    print(f"  UIDs that changed:        {len(changed):>8,}  "
          f"{'OK' if not changed else 'FAIL'}")
    print(f"  New UIDs created:         {len(new_uids):>8,}")
    print(f"  Collisions in final graph:{n_collisions:>8,}  "
          f"{'OK' if n_collisions == 0 else 'FAIL'}")

    if missing:
        print(f"\n  First 10 missing UIDs:")
        for kind, canon, uid in missing[:10]:
            print(f"    [{kind}] {canon!r}  was {uid}")
    if changed:
        print(f"\n  First 10 changed UIDs:")
        for kind, canon, old, new in changed[:10]:
            print(f"    [{kind}] {canon!r}  {old} -> {new}")

    cur.close()
    conn.close()

    if missing or changed or n_collisions > 0:
        raise RuntimeError(
            f"UID preservation FAILED: "
            f"{len(missing)} missing, {len(changed)} changed, "
            f"{n_collisions} collisions"
        )
    print(f"\n  UID preservation: PASS")


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

    # ── snapshot existing UIDs BEFORE any truncation ──────────────────────────
    banner("Step 0 — Snapshotting existing UIDs")
    try:
        uid_snapshot: dict[tuple[str, str], str] = snapshot_uids(database_url)
    except Exception as exc:
        return fail(f"UID snapshot failed: {exc}")

    n_snapshot = len(uid_snapshot)
    snapshot_by_kind: dict[str, int] = {}
    for (kind, _cn) in uid_snapshot:
        snapshot_by_kind[kind] = snapshot_by_kind.get(kind, 0) + 1

    print(f"\n  Captured {n_snapshot:,} UIDs from current graph.")
    print(f"  Breakdown by kind:")
    for k, n in sorted(snapshot_by_kind.items(), key=lambda x: -x[1]):
        print(f"    {k:<20}: {n:>8,}")

    # ── open source connection ────────────────────────────────────────────────
    try:
        src_conn = psycopg2.connect(database_url)
    except Exception as exc:
        return fail(f"Cannot connect to source database: {exc}")

    # Verify source table access and build importer with snapshot
    try:
        importer = BCScraperPGImporter(
            repo=repo,
            conn=src_conn,
            uid_snapshot=uid_snapshot,
        )
        access = importer.verify_access()
    except Exception as exc:
        return fail(f"Source access check failed: {exc}")

    print(f"\n  Source tables:")
    for table, count in access.items():
        print(f"    {table:<24}: {count}")

    if dry_run:
        _dry_run_uid_report(database_url, uid_snapshot)
        src_conn.close()
        print("\n  [DRY-RUN] Exiting without changes.")
        return 0

    # ── clear existing graph data ─────────────────────────────────────────────
    banner("Step 1 — Clearing existing graph data")
    try:
        clear_conn = psycopg2.connect(database_url)
        with clear_conn.cursor() as cur:
            print("  Truncating graph.biz_relations …")
            cur.execute("TRUNCATE graph.biz_relations CASCADE")
            print("  Truncating graph.biz_entities …")
            cur.execute("TRUNCATE graph.biz_entities CASCADE")
            print("  Truncating graph.graph_uid_map …")
            cur.execute("TRUNCATE graph.graph_uid_map")
        clear_conn.commit()
        clear_conn.close()
        print("  Done.")
    except Exception as exc:
        traceback.print_exc()
        return fail(f"Clear failed: {exc}")

    # Pre-populate UID counters so new allocations start above all preserved UIDs
    try:
        advance_uid_counters(database_url, uid_snapshot)
        print(f"  UID counters pre-populated from snapshot "
              f"({len(uid_snapshot):,} UIDs protected).")
    except Exception as exc:
        traceback.print_exc()
        return fail(f"advance_uid_counters failed: {exc}")

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

    # ── verify UID preservation ───────────────────────────────────────────────
    banner("Step 3 — Verifying UID preservation")
    try:
        _verify_uid_preservation(database_url, uid_snapshot)
    except Exception as exc:
        traceback.print_exc()
        return fail(f"UID verification failed: {exc}")

    # ── stats after ───────────────────────────────────────────────────────────
    banner("Step 4 — Graph stats after re-import")
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

    print(f"\n  SUCCESS — re-import complete with full UID preservation.")
    print(f"  Run scripts/verify_identity.py next to generate the verification report.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
