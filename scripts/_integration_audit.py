"""
End-to-end integration audit.
For each target company: scraper DB rows, current graph nodes, alias links.
Read-only. No writes.
"""
import psycopg2, os, sys, json
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"

TARGETS = [
    "pcl", "ledcor", "ellisdon", "graham", "bird construction",
    "kindred", "omicron", "ventana", "brock solutions", "azure",
    "flynn", "clark builders", "western pacific", "dovetail",
    "colas", "lafarge", "aecom", "stantec", "bam construction", "fluor"
]

conn = psycopg2.connect(DSN)
cur = conn.cursor()

results = {}

for target in TARGETS:
    cur.execute("""
        SELECT id, display_name, name, entity_role, canonical_company_id
        FROM public.companies
        WHERE display_name ILIKE %s OR name ILIKE %s
        ORDER BY
            CASE entity_role
                WHEN 'canonical' THEN 0
                WHEN 'applicant_alias' THEN 1
                WHEN 'standalone' THEN 2
                ELSE 3
            END,
            id
        LIMIT 20
    """, (f"%{target}%", f"%{target}%"))
    rows = cur.fetchall()
    results[target] = [
        {"id": r[0], "display_name": r[1], "name": r[2],
         "entity_role": r[3], "canonical_company_id": r[4]}
        for r in rows
    ]

cur.close()
conn.close()

# Print structured report
for target, rows in results.items():
    if not rows:
        print(f"\n{'='*60}\n{target.upper()}: NO ROWS FOUND")
        continue
    print(f"\n{'='*60}")
    print(f"TARGET: {target.upper()}  ({len(rows)} rows)")
    canonicals = [r for r in rows if r["entity_role"] == "canonical"]
    aliases    = [r for r in rows if r["entity_role"] == "applicant_alias"]
    standalones= [r for r in rows if r["entity_role"] == "standalone"]
    persons    = [r for r in rows if r["entity_role"] == "probable_person"]

    for r in canonicals:
        print(f"  CANONICAL  id={r['id']:>8}  display={r['display_name']!r}")
    for r in aliases:
        print(f"  ALIAS      id={r['id']:>8}  display={r['display_name']!r}  -> canon_id={r['canonical_company_id']}")
    for r in standalones:
        print(f"  STANDALONE id={r['id']:>8}  display={r['display_name']!r}")
    if persons:
        print(f"  PERSON     ({len(persons)} rows, skipped)")
