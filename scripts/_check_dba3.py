import os, psycopg2
DATABASE_URL = os.environ.get("DATABASE_URL","postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway")
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("=== How many COMPANY nodes have DBA in name (graph) ===")
cur.execute("SELECT COUNT(*) FROM graph.biz_entities WHERE kind='company' AND (name ILIKE '%DBA:%' OR name ILIKE '% DBA %')")
print("count:", cur.fetchone()[0])

print()
print("=== Sample COMPANY nodes with DBA in name ===")
cur.execute("SELECT uid, name FROM graph.biz_entities WHERE kind='company' AND (name ILIKE '%DBA:%' OR name ILIKE '% DBA %') LIMIT 5")
for uid, name in cur.fetchall():
    print(f"  {uid}  {name!r}")

print()
print("=== Source: how many companies have display_name set vs empty ===")
cur.execute("SELECT COUNT(*) FROM public.companies WHERE display_name IS NOT NULL AND display_name <> ''")
print("  with display_name:", cur.fetchone()[0])
cur.execute("SELECT COUNT(*) FROM public.companies WHERE display_name IS NULL OR display_name = ''")
print("  without display_name:", cur.fetchone()[0])

print()
print("=== Source: companies where display_name IS NULL (use name column - may include DBA) ===")
cur.execute("""
    SELECT id, entity_role, name, display_name
    FROM public.companies
    WHERE (display_name IS NULL OR display_name = '')
      AND name ILIKE '%DBA%'
      AND entity_role IS DISTINCT FROM 'applicant_alias'
    LIMIT 5
""")
for row in cur.fetchall():
    print(f"  id={row[0]} role={row[1]}")
    print(f"    name:         {row[2]!r}")
    print(f"    display_name: {row[3]!r}")

print()
print("=== Source: Kem BSG Management ===")
cur.execute("SELECT id, entity_role, display_name, name FROM public.companies WHERE name ILIKE '%Kem BSG%' OR display_name ILIKE '%Kem BSG%'")
for row in cur.fetchall():
    print(f"  id={row[0]} role={row[1]} display={row[2]!r} name={row[3]!r}")

conn.close()
