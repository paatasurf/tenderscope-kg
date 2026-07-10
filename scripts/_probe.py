import psycopg2, sys

conn = psycopg2.connect(
    "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"
)
cur = conn.cursor()

print("=== entity_role distribution ===")
cur.execute("SELECT entity_role, COUNT(*) FROM public.companies GROUP BY entity_role ORDER BY COUNT(*) DESC")
for row in cur.fetchall():
    print(f"  {row[0]!r:25}  {row[1]}")

print("\n=== PCL rows ===")
cur.execute(
    "SELECT id, display_name, name, entity_role, canonical_company_id "
    "FROM public.companies WHERE name ILIKE '%pcl%' ORDER BY id"
)
for row in cur.fetchall():
    print(" ", row)

conn.close()
