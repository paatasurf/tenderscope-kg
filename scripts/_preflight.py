"""
Pre-flight audit for reimport_identity.py
Read-only. No writes. No modifications.
"""
import psycopg2, psycopg2.extras, json, time

DSN = "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway"

print("=== SECTION 1: PRECONDITIONS ===")

# Connectivity
t0 = time.perf_counter()
conn = psycopg2.connect(DSN)
elapsed_connect = time.perf_counter() - t0
print(f"DB reachable: YES ({elapsed_connect*1000:.0f}ms)")

cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

# Check required public.* tables
print("\nRequired source tables (public.*):")
for table in ("companies", "tenders", "permits", "contract_awards", "commercial_tenders", "arch_tenders"):
    try:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM public.{table}")
        n = cur.fetchone()["cnt"]
        print(f"  public.{table}: EXISTS ({n} rows)")
    except Exception as e:
        print(f"  public.{table}: MISSING — {e}")
        conn.rollback()

# Check graph schema
print("\nGraph schema tables:")
for table in ("graph.biz_entities", "graph.biz_relations", "graph.graph_uid_map", "graph.biz_entity_history"):
    try:
        cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
        n = cur.fetchone()["cnt"]
        print(f"  {table}: EXISTS ({n} rows)")
    except Exception as e:
        print(f"  {table}: MISSING — {e}")
        conn.rollback()

# Check indexes
print("\nIndexes:")
cur.execute("""
SELECT indexname, tablename
FROM pg_indexes
WHERE schemaname = 'graph'
ORDER BY tablename, indexname
""")
for r in cur.fetchall():
    print(f"  {r['tablename']}.{r['indexname']}")

# Check FK constraints
print("\nConstraints:")
cur.execute("""
SELECT conname, contype, conrelid::regclass AS table
FROM pg_constraint
WHERE connamespace = 'graph'::regnamespace
ORDER BY conrelid::regclass::text, conname
""")
for r in cur.fetchall():
    ctype = {"f": "FOREIGN KEY", "p": "PRIMARY KEY", "u": "UNIQUE"}.get(r["contype"], r["contype"])
    print(f"  {r['table']}.{r['conname']} ({ctype})")

# Check TRUNCATE permission
print("\nPermissions (TRUNCATE test via dry check):")
cur.execute("""
SELECT has_table_privilege(current_user, 'graph.biz_entities', 'TRUNCATE') AS can_truncate_entities,
       has_table_privilege(current_user, 'graph.biz_relations', 'TRUNCATE') AS can_truncate_relations,
       has_table_privilege(current_user, 'graph.graph_uid_map', 'TRUNCATE') AS can_truncate_uid_map,
       has_table_privilege(current_user, 'public.companies', 'SELECT') AS can_select_companies,
       current_user AS db_user
""")
r = cur.fetchone()
print(f"  db_user: {r['db_user']}")
print(f"  TRUNCATE graph.biz_entities: {r['can_truncate_entities']}")
print(f"  TRUNCATE graph.biz_relations: {r['can_truncate_relations']}")
print(f"  TRUNCATE graph.graph_uid_map: {r['can_truncate_uid_map']}")
print(f"  SELECT public.companies: {r['can_select_companies']}")

print("\n=== SECTION 2: DRY-RUN COUNTS ===")

# Source data breakdown
cur.execute("""
SELECT entity_role, COUNT(*) AS cnt
FROM public.companies
GROUP BY entity_role
ORDER BY cnt DESC
""")
roles = {str(r["entity_role"]): r["cnt"] for r in cur.fetchall()}
print("Scraper company roles:")
for role, cnt in roles.items():
    print(f"  {role}: {cnt}")

cur.execute("SELECT COUNT(*) AS cnt FROM public.companies")
total_companies = cur.fetchone()["cnt"]
print(f"Total companies in scraper: {total_companies}")

# Estimate what will be created
# Pass 1: everything that is NOT applicant_alias
non_alias = total_companies - roles.get("applicant_alias", 0)
alias_count = roles.get("applicant_alias", 0)
print(f"\nExpected after migration:")
print(f"  COMPANY nodes (Pass 1): {non_alias}")
print(f"  COMPANY_ALIAS nodes (Pass 2): {alias_count}")
print(f"  PERSON nodes: 0 (not created by importer)")
print(f"  ALIAS_OF edges (Pass 2): {alias_count} (max, minus any with missing canonical)")
print(f"  SAME_AS edges: 0 (not created by importer)")

# Check aliases with missing canonical (will be skipped in Pass 2)
cur.execute("""
SELECT COUNT(*) AS cnt
FROM public.companies alias_row
WHERE alias_row.entity_role = 'applicant_alias'
AND alias_row.canonical_company_id IS NOT NULL
AND EXISTS (
    SELECT 1 FROM public.companies canon
    WHERE canon.id = alias_row.canonical_company_id
    AND (canon.entity_role != 'applicant_alias' OR canon.entity_role IS NULL)
)
""")
resolvable_aliases = cur.fetchone()["cnt"]
print(f"\nAliases with resolvable canonical_company_id: {resolvable_aliases}")

cur.execute("""
SELECT COUNT(*) AS cnt
FROM public.companies alias_row
WHERE alias_row.entity_role = 'applicant_alias'
AND (
    alias_row.canonical_company_id IS NULL
    OR NOT EXISTS (
        SELECT 1 FROM public.companies canon
        WHERE canon.id = alias_row.canonical_company_id
        AND canon.entity_role != 'applicant_alias'
    )
)
""")
unresolvable_aliases = cur.fetchone()["cnt"]
print(f"Aliases that CANNOT be resolved (will be skipped, warnings): {unresolvable_aliases}")

# Other entity counts expected
cur.execute("SELECT COUNT(*) AS cnt FROM public.tenders")
n_tenders = cur.fetchone()["cnt"]
cur.execute("SELECT COUNT(*) AS cnt FROM public.commercial_tenders")
n_comm = cur.fetchone()["cnt"]
cur.execute("SELECT COUNT(*) AS cnt FROM public.arch_tenders")
n_arch = cur.fetchone()["cnt"]
cur.execute("SELECT COUNT(*) AS cnt FROM public.permits")
n_permits = cur.fetchone()["cnt"]
cur.execute("SELECT COUNT(*) AS cnt FROM public.contract_awards")
n_contracts = cur.fetchone()["cnt"]

print(f"\nOther entities to be imported:")
print(f"  TENDER (tenders + commercial + arch): {n_tenders} + {n_comm} + {n_arch} = {n_tenders+n_comm+n_arch}")
print(f"  PERMIT: {n_permits}")
print(f"  CONTRACT (from contract_awards): {n_contracts}")

print("\n=== SECTION 3: TRUNCATE CASCADE impact ===")
# What tables CASCADE will affect
cur.execute("""
SELECT
    tc.table_schema || '.' || tc.table_name AS child_table,
    kcu.column_name,
    ccu.table_schema || '.' || ccu.table_name AS referenced_table,
    rc.delete_rule
FROM information_schema.table_constraints AS tc
JOIN information_schema.key_column_usage AS kcu
    ON tc.constraint_name = kcu.constraint_name
    AND tc.table_schema = kcu.table_schema
JOIN information_schema.constraint_column_usage AS ccu
    ON ccu.constraint_name = tc.constraint_name
JOIN information_schema.referential_constraints AS rc
    ON tc.constraint_name = rc.constraint_name
WHERE tc.constraint_type = 'FOREIGN KEY'
AND (ccu.table_schema = 'graph' OR tc.table_schema = 'graph')
ORDER BY child_table
""")
print("Foreign key dependencies (CASCADE impact):")
for r in cur.fetchall():
    print(f"  {r['child_table']}.{r['column_name']} → {r['referenced_table']} (ON DELETE {r['delete_rule']})")

# Current row counts that will be deleted
print("\nRows that will be DELETED by TRUNCATE CASCADE:")
for table in ("graph.biz_entities", "graph.biz_relations", "graph.graph_uid_map"):
    cur.execute(f"SELECT COUNT(*) AS cnt FROM {table}")
    print(f"  {table}: {cur.fetchone()['cnt']} rows deleted")

cur.execute("SELECT COUNT(*) AS cnt FROM graph.biz_entity_history")
print(f"  graph.biz_entity_history: PRESERVED (not truncated, {cur.fetchone()['cnt']} rows)")

print("\n=== SECTION 4: TIMING ESTIMATE ===")
# Time a batch read of 500 companies to estimate import speed
t0 = time.perf_counter()
cur.execute("SELECT * FROM public.companies LIMIT 500")
cur.fetchall()
batch_time = time.perf_counter() - t0
print(f"500-row batch read from public.companies: {batch_time*1000:.1f}ms")

t0 = time.perf_counter()
cur.execute("SELECT * FROM public.permits LIMIT 500")
cur.fetchall()
permit_batch = time.perf_counter() - t0
print(f"500-row batch read from public.permits: {permit_batch*1000:.1f}ms")

print("\n=== SECTION 6: API ENDPOINT VERIFICATION ===")
# Check what the health/stats endpoint would return after migration
# (simulate by reading current state)
cur.execute("SELECT kind, COUNT(*) AS cnt FROM graph.biz_entities GROUP BY kind")
current_kinds = {r["kind"]: r["cnt"] for r in cur.fetchall()}
print("Current graph state:")
print(json.dumps(current_kinds, indent=2))

cur.close()
conn.close()
print("\nDONE")
