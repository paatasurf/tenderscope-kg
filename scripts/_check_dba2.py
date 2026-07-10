import os, psycopg2, psycopg2.extras
DATABASE_URL = os.environ.get("DATABASE_URL","postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway")
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

print("=== Source row for Shane Droucker ===")
cur.execute("SELECT id, entity_role, display_name, name, canonical_company_id FROM public.companies WHERE name ILIKE '%Shane Droucker%' OR display_name ILIKE '%Shane Droucker%'")
for r in cur.fetchall(): print(dict(r))

print()
print("=== Graph entities with Shane ===")
cur.execute("SELECT uid, kind, name FROM graph.biz_entities WHERE name ILIKE '%Shane%'")
for r in cur.fetchall(): print(dict(r))

print()
print("=== How many COMPANY nodes have DBA in name (graph) ===")
cur.execute("SELECT COUNT(*) FROM graph.biz_entities WHERE kind='company' AND (name ILIKE '%DBA:%' OR name ILIKE '%DBA;%')")
print("count:", cur.fetchone()[0])

print()
print("=== Sample COMPANY nodes with DBA: in name ===")
cur.execute("SELECT uid, name FROM graph.biz_entities WHERE kind='company' AND name ILIKE '%DBA:%' LIMIT 5")
for r in cur.fetchall():
    print(f"  {r['uid']}  {r['name']!r}")
    # check if there is a display_name attribute
    cur2 = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur2.execute("SELECT attributes FROM graph.biz_entities WHERE uid=%s", (r['uid'],))
    attrs = cur2.fetchone()['attributes']
    print(f"    attrs keys: {list(attrs.keys()) if attrs else 'NULL'}")
    if attrs and 'display_name' in attrs:
        print(f"    display_name attr: {attrs['display_name']!r}")
    cur2.close()

conn.close()
