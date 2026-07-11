import psycopg2, psycopg2.extras, json, sys

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"
conn = psycopg2.connect(DSN)
cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

cur.execute("SELECT kind, COUNT(*) AS cnt FROM graph.biz_entities GROUP BY kind ORDER BY cnt DESC")
by_kind = {r["kind"]: r["cnt"] for r in cur.fetchall()}
print("GRAPH_ENTITIES_BY_KIND:", json.dumps(by_kind))

cur.execute("SELECT kind, COUNT(*) AS cnt FROM graph.biz_relations GROUP BY kind ORDER BY cnt DESC")
by_rel = {r["kind"]: r["cnt"] for r in cur.fetchall()}
print("GRAPH_RELATIONS_BY_KIND:", json.dumps(by_rel))

cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entities")
print("TOTAL_ENTITIES:", cur.fetchone()["cnt"])

cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_relations")
print("TOTAL_RELATIONS:", cur.fetchone()["cnt"])

cur.execute("SELECT entity_role, COUNT(*) AS cnt FROM public.companies GROUP BY entity_role ORDER BY cnt DESC")
roles = {str(r["entity_role"]): r["cnt"] for r in cur.fetchall()}
print("SCRAPER_COMPANY_ROLES:", json.dumps(roles))

cur.execute("SELECT COUNT(*) AS cnt FROM public.companies")
print("SCRAPER_TOTAL_COMPANIES:", cur.fetchone()["cnt"])

cur.execute("SELECT COUNT(*) AS cnt FROM public.tenders")
print("SCRAPER_TOTAL_TENDERS:", cur.fetchone()["cnt"])

cur.execute("SELECT COUNT(*) AS cnt FROM public.permits")
print("SCRAPER_TOTAL_PERMITS:", cur.fetchone()["cnt"])

cur.execute("SELECT COUNT(*) AS cnt FROM public.contract_awards")
print("SCRAPER_TOTAL_CONTRACT_AWARDS:", cur.fetchone()["cnt"])

# Check for any company_alias or person nodes
for k in ("company_alias", "person", "address", "project", "organization"):
    v = by_kind.get(k, 0)
    print(f"NODE_{k.upper()}: {v}")

# Check IdentityEvidence in any ALIAS_OF relation attributes
cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_relations WHERE kind = 'alias_of'")
print("ALIAS_OF_RELATIONS:", cur.fetchone()["cnt"])

cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_relations WHERE kind = 'same_as'")
print("SAME_AS_RELATIONS:", cur.fetchone()["cnt"])

# Sample a few company entities to see if they have scraper_id
cur.execute("SELECT uid, name, attributes FROM graph.biz_entities WHERE kind='company' LIMIT 3")
rows = cur.fetchall()
for r in rows:
    attrs = r["attributes"]
    if isinstance(attrs, str):
        attrs = json.loads(attrs)
    print(f"SAMPLE_COMPANY: uid={r['uid']} name={r['name']!r} has_scraper_id={'scraper_id' in attrs}")

# Check if entity_history table has rows
cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entity_history")
print("ENTITY_HISTORY_ROWS:", cur.fetchone()["cnt"])

# Check uid counters
cur.execute("SELECT prefix, next_val FROM graph.graph_uid_map ORDER BY prefix")
for r in cur.fetchall():
    print(f"UID_COUNTER_{r['prefix']}: {r['next_val']}")

cur.close()
conn.close()
print("DONE")
