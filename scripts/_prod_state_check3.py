import psycopg2, psycopg2.extras, json

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"
conn = psycopg2.connect(DSN)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Check every entity_role value actually present in graph companies
cur.execute("""
SELECT attributes->>'entity_role' AS entity_role, COUNT(*) AS cnt
FROM graph.biz_entities
WHERE kind = 'company'
GROUP BY attributes->>'entity_role'
ORDER BY cnt DESC
""")
print("=== entity_role breakdown inside graph.biz_entities (kind=company) ===")
for r in cur.fetchall():
    print(f"  entity_role={r['entity_role']!r}: {r['cnt']}")

# Check how many companies have canonical_company_id attribute
cur.execute("""
SELECT COUNT(*) AS cnt
FROM graph.biz_entities
WHERE kind = 'company'
AND attributes ? 'canonical_company_id'
""")
print("\nCompanies with canonical_company_id attribute:", cur.fetchone()["cnt"])

# How many scraper 'canonical' role companies are in graph
cur.execute("""
SELECT COUNT(*) AS cnt
FROM graph.biz_entities
WHERE kind = 'company'
AND attributes->>'entity_role' = 'canonical'
""")
print("Graph companies with entity_role='canonical':", cur.fetchone()["cnt"])

# How many scraper 'standalone' role companies are in graph
cur.execute("""
SELECT COUNT(*) AS cnt
FROM graph.biz_entities
WHERE kind = 'company'
AND attributes->>'entity_role' = 'standalone'
""")
print("Graph companies with entity_role='standalone':", cur.fetchone()["cnt"])

# Cross-check: how many applicant_alias rows in scraper
cur.execute("SELECT COUNT(*) AS cnt FROM public.companies WHERE entity_role = 'applicant_alias'")
print("\nScraper applicant_alias rows:", cur.fetchone()["cnt"])

# Are any of those aliases present as COMPANY (not COMPANY_ALIAS) in graph?
cur.execute("""
SELECT COUNT(*) AS cnt
FROM graph.biz_entities g
JOIN public.companies p ON (g.attributes->>'scraper_id')::int = p.id
WHERE g.kind = 'company'
AND p.entity_role = 'applicant_alias'
""")
print("Scraper aliases imported as COMPANY (wrong kind):", cur.fetchone()["cnt"])

# What is the earliest created_at in graph entities from bc_scraper_pg?
cur.execute("""
SELECT MIN(created_at) AS first, MAX(created_at) AS last
FROM graph.biz_entities
WHERE source = 'bc_scraper_pg'
""")
r = cur.fetchone()
print(f"\nbc_scraper_pg import window: {r['first']} → {r['last']}")

cur.close()
conn.close()
print("DONE")
