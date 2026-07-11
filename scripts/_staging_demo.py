"""
Staging identity demo — non-destructive, uses local SQLite graph.
Reads from production Postgres (read-only SELECT) into in-memory SQLite.
Demonstrates before/after for PCL + aggregate alias counts.
"""
import os, sqlite3, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import psycopg2
from tenderscope_kg.repository import create_repository
from tenderscope_kg.biz_query_engine import BizQueryEngine
from tenderscope_kg.importers.bc_scraper_pg_importer import BCScraperPGImporter

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"

def banner(msg):
    print(f"\n{'─'*60}\n  {msg}\n{'─'*60}")

def show_search(engine, q, label):
    r = engine.find_companies(q, limit=20)
    print(f"\n  [{label}] search '{q}' → {r['count']} result(s)")
    for c in r["results"]:
        print(f"    {c['uid']}  kind={c['kind']:<16}  {c['name']}")

def show_identity(engine, uid, label):
    r = engine.company_identity(uid)
    if "error" in r:
        print(f"  [{label}] identity({uid}): {r['error']}")
        return
    print(f"\n  [{label}] identity {uid} — {r['display_name']}")
    print(f"    aliases ({len(r['aliases'])}):")
    for a in r["aliases"]:
        print(f"      {a['uid']}  conf={a['confidence']}  reason={a['reason']}  name={a['name']}")

# ── BEFORE: old-style flat import, all rows as COMPANY ────────────────────────
banner("BEFORE — old flat import (all rows as COMPANY, no alias resolution)")

conn_before = sqlite3.connect(":memory:", check_same_thread=False)
repo_before = create_repository("sqlite", conn=conn_before)
repo_before.setup_schema()

src = psycopg2.connect(DSN)
cur = src.cursor()
cur.execute(
    "SELECT id, COALESCE(NULLIF(display_name,''), name, '') AS dn, name, entity_role, canonical_company_id "
    "FROM public.companies WHERE name ILIKE '%pcl%' ORDER BY id"
)
pcl_rows = cur.fetchall()
cur.close()

from tenderscope_kg.domain import BizEntityKind
for (db_id, dn, raw_name, role, can_id) in pcl_rows:
    name = (dn or raw_name or "").strip()
    if not name:
        continue
    repo_before.put_entity(
        kind=BizEntityKind.COMPANY,
        name=name,
        attributes={"scraper_id": db_id, "entity_role": role or ""},
        source="bc_scraper_pg",
        write_history=False,
    )

engine_before = BizQueryEngine(repo_before)
show_search(engine_before, "pcl construction", "BEFORE")
show_search(engine_before, "pcl westcoast", "BEFORE")
stats_b = repo_before.get_stats()
print(f"\n  Graph before: entities={stats_b['entities']}  by_kind={stats_b.get('by_kind',{})}")

# ── AFTER: two-pass import with COMPANY_ALIAS + ALIAS_OF ─────────────────────
banner("AFTER — two-pass import (COMPANY + COMPANY_ALIAS + ALIAS_OF edges)")

conn_after = sqlite3.connect(":memory:", check_same_thread=False)
repo_after = create_repository("sqlite", conn=conn_after)
repo_after.setup_schema()

src2 = psycopg2.connect(DSN)
importer = BCScraperPGImporter(repo=repo_after, conn=src2, batch_size=500)

# Run only the companies step (no tenders/permits — keep it fast)
from tenderscope_kg.domain.results import ImportResult
with repo_after.transaction():
    result = importer._import_companies()

print(f"\n  Import result:")
print(f"    entities_created  : {result.entities_created}")
print(f"    entities_updated  : {result.entities_updated}")
print(f"    relations_created : {result.relations_created}")
print(f"    errors            : {len(result.errors)}")
if result.errors[:5]:
    for e in result.errors[:5]:
        print(f"    ERROR: {e}")
print(f"    warnings (first 3): {result.warnings[:3]}")

engine_after = BizQueryEngine(repo_after)
stats_a = repo_after.get_stats()
print(f"\n  Graph after:  entities={stats_a['entities']}  by_kind={stats_a.get('by_kind',{})}")

show_search(engine_after, "pcl construction", "AFTER")
show_search(engine_after, "pcl westcoast", "AFTER")

# Show canonical PCL identity — find its uid first
res = engine_after.find_companies("PCL Constructors Westcoast Inc.", limit=5)
canonical_uid = next((r["uid"] for r in res["results"] if r["kind"] == "company"), None)
if canonical_uid:
    show_identity(engine_after, canonical_uid, "AFTER")
else:
    print("\n  Could not find canonical PCL uid")

src2.close()
print("\n  Done. No production data was modified.")
