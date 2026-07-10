"""
Probe: what exactly is stored in display_name vs name for standalone DBA rows,
and what does the importer use as the graph node name?
"""
import psycopg2

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"
conn = psycopg2.connect(DSN)
cur = conn.cursor()

print("=== Sample: canonical rows ===")
cur.execute("""
    SELECT id, display_name, name
    FROM public.companies
    WHERE entity_role = 'canonical'
    ORDER BY id
    LIMIT 10
""")
for row in cur.fetchall():
    print(f"  id={row[0]}  display_name={row[1]!r}  name={row[2]!r}")

print("\n=== Sample: applicant_alias rows ===")
cur.execute("""
    SELECT id, display_name, name, canonical_company_id
    FROM public.companies
    WHERE entity_role = 'applicant_alias'
    ORDER BY id
    LIMIT 10
""")
for row in cur.fetchall():
    print(f"  id={row[0]}  display_name={row[1]!r}  name={row[2]!r}  canon_id={row[3]}")

print("\n=== Sample: standalone DBA rows ===")
cur.execute("""
    SELECT id, display_name, name
    FROM public.companies
    WHERE entity_role = 'standalone'
      AND name ILIKE '%DBA:%'
    ORDER BY id
    LIMIT 15
""")
for row in cur.fetchall():
    print(f"  id={row[0]}  display_name={row[1]!r}  name={row[2]!r}")

print("\n=== display_name vs name comparison for DBA standalones ===")
cur.execute("""
    SELECT
        COUNT(*) FILTER (WHERE display_name = name)               AS display_equals_name,
        COUNT(*) FILTER (WHERE display_name <> name)              AS display_differs,
        COUNT(*) FILTER (WHERE display_name IS NULL OR display_name = '') AS display_empty,
        COUNT(*) FILTER (WHERE display_name ILIKE '%DBA:%')       AS display_also_has_dba,
        COUNT(*) FILTER (WHERE display_name NOT ILIKE '%DBA:%'
                           AND (display_name IS NOT NULL AND display_name <> ''))
                                                                  AS display_is_clean_name
    FROM public.companies
    WHERE entity_role = 'standalone'
      AND name ILIKE '%DBA:%'
""")
row = cur.fetchone()
print(f"  display_name == name          : {row[0]}")
print(f"  display_name != name          : {row[1]}")
print(f"  display_name empty/null       : {row[2]}")
print(f"  display_name also has DBA:    : {row[3]}")
print(f"  display_name is clean company : {row[4]}")

print("\n=== Sample: standalone DBA rows where display_name is clean ===")
cur.execute("""
    SELECT id, display_name, name
    FROM public.companies
    WHERE entity_role = 'standalone'
      AND name ILIKE '%DBA:%'
      AND display_name NOT ILIKE '%DBA:%'
      AND display_name IS NOT NULL
      AND display_name <> ''
    LIMIT 10
""")
for row in cur.fetchall():
    print(f"  id={row[0]}  display_name={row[1]!r}")
    print(f"           name={row[2]!r}")

print("\n=== PCL standalones specifically ===")
cur.execute("""
    SELECT id, display_name, name, entity_role, canonical_company_id
    FROM public.companies WHERE name ILIKE '%pcl%' ORDER BY id
""")
for row in cur.fetchall():
    print(f"  id={row[0]}  display_name={row[1]!r}  name={row[2]!r}  role={row[3]}  canon={row[4]}")

cur.close()
conn.close()
