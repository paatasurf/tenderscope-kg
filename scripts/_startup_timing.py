"""
Startup timing probe.

Measures every phase of the Railway startup sequence against the
production PostgreSQL endpoint and prints a timeline with millisecond
precision.  Run multiple times to simulate normal + warm + cold starts.

Usage:
    python scripts/_startup_timing.py
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://postgres:LujCUsENgKYCbfclnCCPhRCuJQEKfCPy@acela.proxy.rlwy.net:47306/railway",
)

RAILWAY_HEALTHCHECK_TIMEOUT_MS = 30_000  # from railway.toml

_t0 = time.perf_counter()


def ms(since: float) -> int:
    return int((time.perf_counter() - since) * 1000)


def mark(label: str, since: float) -> float:
    now = time.perf_counter()
    elapsed = int((now - since) * 1000)
    total = int((now - _t0) * 1000)
    print(f"  [{total:>6} ms total | {elapsed:>6} ms] {label}")
    return now


# ── Phase 0: process start ────────────────────────────────────────────────────
print()
print("=" * 70)
print("  TenderScope startup timing probe")
print(f"  Railway healthcheck timeout: {RAILWAY_HEALTHCHECK_TIMEOUT_MS} ms")
print("=" * 70)

t = mark("process start / imports begin", _t0)

# ── Phase 1: module imports ───────────────────────────────────────────────────
import psycopg2  # noqa: E402
import sqlite3  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from tenderscope_kg.repository import open_repository  # noqa: E402
from tenderscope_kg.repository._postgres import BizRepositoryPG  # noqa: E402
from tenderscope_kg.db import GraphDB  # noqa: E402

t = mark("module imports complete", t)

# ── Phase 2: psycopg2.connect() raw ──────────────────────────────────────────
print()
print("  -- Phase 2: raw psycopg2.connect() (what setup_schema calls) --")
t2 = time.perf_counter()
try:
    raw_conn = psycopg2.connect(DATABASE_URL)
    t = mark("psycopg2.connect() returned", t2)
    raw_conn.close()
    t = mark("psycopg2.connect().close() returned", t)
except Exception as exc:
    t = mark(f"psycopg2.connect() FAILED: {exc}", t2)

# ── Phase 3: open_repository() → setup_schema() ──────────────────────────────
print()
print("  -- Phase 3: open_repository() including setup_schema() --")
t3 = time.perf_counter()
try:
    # Monkey-patch BizRepositoryPG.setup_schema to time it precisely
    _orig_setup = BizRepositoryPG.setup_schema

    def _timed_setup(self):
        ts = time.perf_counter()
        _orig_setup(self)
        dur = int((time.perf_counter() - ts) * 1000)
        total = int((time.perf_counter() - _t0) * 1000)
        print(f"  [{total:>6} ms total | {dur:>6} ms] setup_schema() DDL execution")

    BizRepositoryPG.setup_schema = _timed_setup

    repo = open_repository()          # uses DATABASE_URL from env / hardcoded above
    t = mark("open_repository() returned (repo ready)", t3)
except Exception as exc:
    t = mark(f"open_repository() FAILED: {exc}", t3)
    repo = None

# ── Phase 4: GraphDB.connect() (SQLite, always fast) ─────────────────────────
print()
print("  -- Phase 4: GraphDB.connect() (SQLite) --")
t4 = time.perf_counter()
db_path = Path("/tmp/_startup_timing_probe.db")
gdb = GraphDB(db_path)
gdb.connect()
t = mark("GraphDB.connect() returned", t4)

# ── Phase 5: build_engines() ─────────────────────────────────────────────────
if repo is not None:
    print()
    print("  -- Phase 5: build_engines() --")
    from tenderscope_kg.server_engines import build_engines  # noqa: E402
    t5 = time.perf_counter()
    engines = build_engines(repo)
    t = mark("build_engines() returned", t5)

# ── Phase 6: simulated HTTP health response ───────────────────────────────────
print()
print("  -- Phase 6: simulate health endpoint call --")
if repo is not None:
    t6 = time.perf_counter()
    try:
        stats = repo.get_stats()
        t = mark("repo.get_stats() returned (health endpoint body)", t6)
    except Exception as exc:
        t = mark(f"repo.get_stats() FAILED: {exc}", t6)

# ── Summary ───────────────────────────────────────────────────────────────────
total_ms = ms(_t0)
print()
print("=" * 70)
print(f"  TOTAL from process start to server-ready: {total_ms} ms")
budget_remaining = RAILWAY_HEALTHCHECK_TIMEOUT_MS - total_ms
if budget_remaining > 0:
    print(f"  Remaining budget before Railway timeout:  {budget_remaining} ms  ✓")
else:
    print(f"  OVER Railway timeout by:                  {-budget_remaining} ms  ✗ FAIL")
print("=" * 70)
print()
