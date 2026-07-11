"""
Instruments put_entity(), _next_uid(), _commit(), and conn.commit() with
precise timestamps to find exactly where execution stalls.

Runs only the first 5 canonical companies from _import_companies so it
completes quickly and produces unambiguous evidence.
"""
from __future__ import annotations

import os
import sys
import time
import json

sys.path.insert(0, str(__import__("pathlib").Path(__file__).parent.parent / "src"))

import psycopg2

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway",
)

_t0 = time.perf_counter()
_orig_connect = psycopg2.connect  # save before any patching


def ts() -> str:
    return f"[+{int((time.perf_counter() - _t0)*1000):>6}ms]"


print(f"{ts()} probe starting")

# ── Patch BizRepositoryPG before import ──────────────────────────────────────
from tenderscope_kg.repository._postgres import BizRepositoryPG

_orig_put_entity = BizRepositoryPG.put_entity
_orig_next_uid   = BizRepositoryPG._next_uid
_orig_commit_m   = BizRepositoryPG._commit


def _patched_put_entity(self, kind, name, **kwargs):
    print(f"{ts()} put_entity() START  kind={kind.value}  name={name!r:.60}")
    t = time.perf_counter()

    # Check connection alive before call
    conn = self._get_conn()
    try:
        conn.cursor().execute("SELECT 1")
        print(f"{ts()} connection alive (SELECT 1 OK)")
    except Exception as exc:
        print(f"{ts()} connection DEAD before put_entity: {exc}")

    try:
        result = _orig_put_entity(self, kind, name, **kwargs)
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} put_entity() RETURNED in {dur}ms  uid={result[0].uid}  created={result[1]}")
        return result
    except Exception as exc:
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} put_entity() RAISED after {dur}ms: {exc}")
        raise


def _patched_next_uid(self, kind, conn=None):
    print(f"{ts()} _next_uid() START  kind={kind.value}")
    t = time.perf_counter()
    try:
        result = _orig_next_uid(self, kind, conn)
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} _next_uid() RETURNED in {dur}ms  uid={result}")
        return result
    except Exception as exc:
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} _next_uid() RAISED after {dur}ms: {exc}")
        raise


def _patched_commit(self, conn):
    print(f"{ts()} _commit() START")
    t = time.perf_counter()
    try:
        _orig_commit_m(self, conn)
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} _commit() DONE in {dur}ms")
    except Exception as exc:
        dur = int((time.perf_counter() - t) * 1000)
        print(f"{ts()} _commit() RAISED after {dur}ms: {exc}")
        raise


BizRepositoryPG.put_entity = _patched_put_entity
BizRepositoryPG._next_uid  = _patched_next_uid
BizRepositoryPG._commit    = _patched_commit

# Patch _get_conn to log each new connection created per put_entity call
_orig_get_conn = BizRepositoryPG._get_conn


def _patched_get_conn(self):
    print(f"{ts()} _get_conn() START")
    t = time.perf_counter()
    conn = _orig_get_conn(self)
    dur = int((time.perf_counter() - t) * 1000)
    print(f"{ts()} _get_conn() DONE in {dur}ms  conn_status={conn.status}")
    return conn


BizRepositoryPG._get_conn = _patched_get_conn

# Skip setup_schema DDL — schema already exists, saves 900ms and avoids noise
_orig_setup_schema = BizRepositoryPG.setup_schema


def _patched_setup_schema(self):
    print(f"{ts()} setup_schema() SKIPPED (schema already present)")


BizRepositoryPG.setup_schema = _patched_setup_schema

# ── Set up repo ───────────────────────────────────────────────────────────────
print(f"{ts()} opening repository")
from tenderscope_kg.repository import open_repository
repo = open_repository()
print(f"{ts()} repository ready")

# ── Run first 5 companies only ───────────────────────────────────────────────
from tenderscope_kg.domain import BizEntityKind

src_conn = _orig_connect(DATABASE_URL)
cur = src_conn.cursor()
cur.execute("""
    SELECT id, COALESCE(display_name, name) as name, entity_role
    FROM public.companies
    WHERE entity_role IS DISTINCT FROM 'applicant_alias'
      AND COALESCE(display_name, name) IS NOT NULL
      AND COALESCE(display_name, name) <> ''
    ORDER BY id
    LIMIT 5
""")
rows = cur.fetchall()
cur.close()
src_conn.close()

print(f"\n{ts()} --- begin put_entity loop ({len(rows)} rows) ---\n")

for db_id, name, role in rows:
    print(f"\n{ts()} === company id={db_id} name={name!r:.60} role={role} ===")
    try:
        entity, created = repo.put_entity(
            kind=BizEntityKind.COMPANY,
            name=name,
            attributes={"scraper_id": db_id},
            source="probe",
            write_history=False,
        )
        print(f"{ts()} LOOP OK  uid={entity.uid}  created={created}")
    except Exception as exc:
        print(f"{ts()} LOOP ERROR: {exc}")

print(f"\n{ts()} probe complete")
