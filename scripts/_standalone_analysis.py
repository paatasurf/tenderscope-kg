"""
Analyse the standalone duplicate problem in production public.companies.
No writes. Read-only SELECT from production Postgres.
"""
import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import psycopg2
from collections import defaultdict

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"

conn = psycopg2.connect(DSN)
cur = conn.cursor()

# ── 1. Overall distribution ────────────────────────────────────────────────────
print("=== entity_role distribution ===")
cur.execute("SELECT entity_role, COUNT(*) FROM public.companies GROUP BY entity_role ORDER BY COUNT(*) DESC")
for role, count in cur.fetchall():
    print(f"  {role!r:25}  {count:>6}")

# ── 2. How many standalones share a display_name with a canonical? ─────────────
print("\n=== standalones that share display_name with a canonical ===")
cur.execute("""
    SELECT COUNT(*)
    FROM public.companies s
    JOIN public.companies c
      ON LOWER(TRIM(COALESCE(s.display_name,''))) = LOWER(TRIM(COALESCE(c.display_name,'')))
     AND c.entity_role = 'canonical'
     AND s.entity_role = 'standalone'
     AND s.id <> c.id
""")
print(f"  standalones matching a canonical by display_name: {cur.fetchone()[0]}")

# ── 3. DBA pattern — standalones that are clearly DBA variants ────────────────
print("\n=== standalones with DBA pattern in raw name ===")
cur.execute("""
    SELECT COUNT(*) FROM public.companies
    WHERE entity_role = 'standalone'
      AND name ILIKE '%DBA:%'
""")
print(f"  standalones with 'DBA:' in raw name: {cur.fetchone()[0]}")

# ── 4. Exact display_name duplicates among standalones ────────────────────────
print("\n=== exact display_name duplicates among standalones only ===")
cur.execute("""
    SELECT LOWER(TRIM(COALESCE(display_name,''))), COUNT(*)
    FROM public.companies
    WHERE entity_role = 'standalone'
    GROUP BY 1
    HAVING COUNT(*) > 1
    ORDER BY 2 DESC
    LIMIT 20
""")
rows = cur.fetchall()
print(f"  distinct names with 2+ standalone rows: {len(rows)}")
for name, cnt in rows[:10]:
    print(f"    {cnt}x  {name!r}")

# ── 5. Sample: standalones that have a canonical with same display_name ────────
print("\n=== sample: 10 standalones that match a canonical display_name ===")
cur.execute("""
    SELECT s.id, s.display_name, s.name, c.id AS canonical_id, c.display_name AS canonical_display
    FROM public.companies s
    JOIN public.companies c
      ON LOWER(TRIM(COALESCE(s.display_name,''))) = LOWER(TRIM(COALESCE(c.display_name,'')))
     AND c.entity_role = 'canonical'
     AND s.entity_role = 'standalone'
     AND s.id <> c.id
    LIMIT 10
""")
for row in cur.fetchall():
    print(f"  standalone id={row[0]}  display={row[1]!r}")
    print(f"    raw name: {row[2]!r}")
    print(f"    matches canonical id={row[3]}  display={row[4]!r}")

# ── 6. Total standalone rows that are clearly DBA of a known canonical ─────────
print("\n=== standalones with 'DBA:' whose DBA company matches a canonical display_name ===")
cur.execute("""
    SELECT COUNT(*)
    FROM public.companies s
    JOIN public.companies c
      ON LOWER(TRIM(COALESCE(
             CASE WHEN s.name ILIKE '%DBA:%'
                  THEN TRIM(SPLIT_PART(s.name, 'DBA:', 2))
                  ELSE s.display_name END
         ,''))) = LOWER(TRIM(COALESCE(c.display_name,'')))
     AND c.entity_role = 'canonical'
     AND s.entity_role = 'standalone'
""")
print(f"  DBA-pattern standalones resolvable to a canonical: {cur.fetchone()[0]}")

cur.close()
conn.close()
