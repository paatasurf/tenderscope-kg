import os, psycopg2
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway")
conn = psycopg2.connect(DATABASE_URL)
cur = conn.cursor()
cur.execute("SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name='companies' ORDER BY ordinal_position")
print([r[0] for r in cur.fetchall()])
conn.close()
