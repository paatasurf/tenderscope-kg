"""
Check how DBA company names are stored in the source database and graph.
"""
import os, psycopg2, psycopg2.extras

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway",
)
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== Source: companies with 'DBA' in display_name or name (first 10) ===")
cur.execute("""
    SELECT id, entity_role,
           COALESCE(NULLIF(display_name,''), name) AS effective_name,
           display_name, name, canonical_company_id
    FROM public.companies
    WHERE (display_name ILIKE '%DBA%' OR name ILIKE '%DBA%')
      AND entity_role IS DISTINCT FROM 'applicant_alias'
    ORDER BY id
    LIMIT 10
""")
for r in cur.fetchall():
    print(f"  id={r['id']} role={r['entity_role']}")
    print(f"    display_name : {r['display_name']!r}")
    print(f"    name         : {r['name']!r}")
    print(f"    effective    : {r['effective_name']!r}")
    print(f"    canonical_id : {r['canonical_company_id']}")
    print()

print("=== Graph: COMPANY entities with 'DBA' in name (first 10) ===")
cur.execute("""
    SELECT uid, kind, name, canonical_name
    FROM graph.biz_entities
    WHERE kind = 'company' AND name ILIKE '%DBA%'
    ORDER BY uid
    LIMIT 10
""")
for r in cur.fetchall():
    print(f"  {r['uid']}  {r['name']!r}")

print()
print("=== Graph: COMPANY_ALIAS entities with 'DBA' in name (first 5) ===")
cur.execute("""
    SELECT uid, kind, name, canonical_name
    FROM graph.biz_entities
    WHERE kind = 'company_alias' AND name ILIKE '%DBA%'
    ORDER BY uid
    LIMIT 5
""")
for r in cur.fetchall():
    print(f"  {r['uid']}  {r['name']!r}")

print()
print("=== Specific example: Shane Droucker ===")
cur.execute("""
    SELECT uid, kind, name, canonical_name, attributes
    FROM graph.biz_entities
    WHERE name ILIKE '%Shane Droucker%' OR name ILIKE '%BC Event Management%'
    ORDER BY kind, uid
""")
for r in cur.fetchall():
    print(f"  [{r['kind']}] {r['uid']}  {r['name']!r}")
    print(f"    canonical_name: {r['canonical_name']!r}")

conn.close()
