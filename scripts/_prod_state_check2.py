import psycopg2, psycopg2.extras, json

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"
conn = psycopg2.connect(DSN)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Who actually is CMP-00000001..CMP-00000005 and what attributes do they have?
cur.execute("SELECT uid, name, canonical_name, source, attributes FROM graph.biz_entities WHERE kind='company' ORDER BY uid LIMIT 10")
rows = cur.fetchall()
print("=== First 10 COMPANY entities ===")
for r in rows:
    attrs = r["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    print(f"  uid={r['uid']} name={r['name']!r} source={r['source']!r}")
    print(f"    attributes: {json.dumps(attrs)}")

# What source values are in the graph?
print()
print("=== Source breakdown for all entities ===")
cur.execute("SELECT source, kind, COUNT(*) AS cnt FROM graph.biz_entities GROUP BY source, kind ORDER BY source, cnt DESC")
for r in cur.fetchall():
    print(f"  source={r['source']!r} kind={r['kind']} count={r['cnt']}")

# What source values for relations?
print()
print("=== Source breakdown for all relations ===")
cur.execute("SELECT source, kind, COUNT(*) AS cnt FROM graph.biz_relations GROUP BY source, kind ORDER BY source, cnt DESC")
for r in cur.fetchall():
    print(f"  source={r['source']!r} kind={r['kind']} count={r['cnt']}")

# Is bc_scraper_pg the source for any entity?
cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entities WHERE source = 'bc_scraper_pg'")
print()
print("Entities with source='bc_scraper_pg':", cur.fetchone()["cnt"])

# Check the entity history for who ran what
print()
print("=== Entity history (all 8 rows) ===")
cur.execute("SELECT uid, changed_by, changed_at FROM graph.biz_entity_history ORDER BY id")
for r in cur.fetchall():
    print(f"  uid={r['uid']} changed_by={r['changed_by']!r} changed_at={r['changed_at']}")

# Are there any scraper_id attributes at all in the graph?
cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entities WHERE attributes ? 'scraper_id'")
print()
print("Entities with scraper_id attribute:", cur.fetchone()["cnt"])

# Who is in graph companies actually? Sample 10 real company names
cur.execute("SELECT uid, name, source FROM graph.biz_entities WHERE kind='company' AND source != 'smoke_test' AND name NOT LIKE '%smoke%' ORDER BY uid LIMIT 10")
rows = cur.fetchall()
print()
print("=== 10 real COMPANY nodes ===")
for r in rows:
    print(f"  {r['uid']}  {r['name']!r}  source={r['source']!r}")

cur.close()
conn.close()
print("DONE")
