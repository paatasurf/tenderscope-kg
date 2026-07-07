"""
Repository contract test fixtures.

The parametrized 'repo' fixture runs every contract test against:
  sqlite   — BizRepositorySQLite(:memory:)   reference implementation
  fake     — FakeBizRepository               in-memory test double
  postgres — BizRepositoryPG                 production implementation
             only when PG_TEST_URL env var is set; skipped otherwise.

Run postgres contract tests:
    PG_TEST_URL=postgresql://user:pass@localhost/testdb pytest \\
        tests/repository_contract/ -m postgres
"""
from __future__ import annotations

import os
import sqlite3

import pytest

from tenderscope_kg.repository._sqlite import BizRepositorySQLite
from tests.fakes.repository import FakeBizRepository


def _make_sqlite_repo() -> BizRepositorySQLite:
    conn = sqlite3.connect(":memory:")
    repo = BizRepositorySQLite(conn)
    repo.setup_schema()
    return repo


def _make_fake_repo() -> FakeBizRepository:
    return FakeBizRepository()


def _make_pg_repo():
    try:
        import psycopg2
    except ImportError:
        pytest.skip("psycopg2 not installed (pip install tenderscope-kg[postgres])")
    dsn = os.environ.get("PG_TEST_URL")
    if not dsn:
        pytest.skip("PG_TEST_URL not set — skipping postgres contract tests")
    from tenderscope_kg.repository._postgres import BizRepositoryPG
    conn = psycopg2.connect(dsn)
    repo = BizRepositoryPG(conn=conn)
    repo.setup_schema()
    # Wipe all graph data so each test starts clean
    with conn.cursor() as cur:
        cur.execute("DELETE FROM graph.biz_entity_history")
        cur.execute("DELETE FROM graph.biz_relations")
        cur.execute("DELETE FROM graph.biz_entities")
        cur.execute("DELETE FROM graph.graph_uid_map")
    conn.commit()
    return repo


@pytest.fixture(params=["sqlite", "fake", "postgres"])
def repo(request):
    """
    Parametrized fixture: yields a fresh repository for each backend.

    sqlite   — BizRepositorySQLite(:memory:) — reference implementation
    fake     — FakeBizRepository             — in-memory test double
    postgres — BizRepositoryPG               — production (requires PG_TEST_URL)
    """
    if request.param == "sqlite":
        return _make_sqlite_repo()
    if request.param == "postgres":
        r = _make_pg_repo()
        request.addfinalizer(lambda: r._fixed_conn.close())
        return r
    return _make_fake_repo()
