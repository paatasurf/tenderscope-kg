import os, psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway",
)
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()

print("=== Row counts ===")
for table in ["graph.biz_entities", "graph.biz_relations", "graph.graph_uid_map"]:
    cur.execute(f"SELECT COUNT(*) FROM {table}")
    print(f"  {table}: {cur.fetchone()[0]:,}")

print()
print("=== By kind ===")
cur.execute("SELECT kind, COUNT(*) FROM graph.biz_entities GROUP BY kind ORDER BY kind")
for row in cur.fetchall():
    print(f"  {row[0]}: {row[1]:,}")

print()
print("=== Active PG connections from this DB ===")
cur.execute("""
    SELECT pid, state, wait_event_type, wait_event, left(query, 100) as q
    FROM pg_stat_activity
    WHERE datname = current_database()
      AND pid <> pg_backend_pid()
    ORDER BY state
""")
rows = cur.fetchall()
if rows:
    for row in rows:
        print(f"  pid={row[0]} state={row[1]} wait={row[2]}/{row[3]}")
        print(f"    query: {row[4]}")
else:
    print("  (none)")

print()
print("=== Last 5 rows inserted (biz_entities) ===")
cur.execute("""
    SELECT kind, canonical_name, created_at
    FROM graph.biz_entities
    ORDER BY created_at DESC
    LIMIT 5
""")
for row in cur.fetchall():
    print(f"  [{row[2]}] {row[0]}: {row[1][:80]}")

conn.close()
