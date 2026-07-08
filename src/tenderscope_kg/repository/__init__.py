"""
TenderScope Knowledge Graph — Repository layer.

Public surface:
    BizRepository          — the abstract interface (the contract)
    create_repository()    — low-level factory; constructs a named backend
    open_repository()      — production factory; selects backend from environment

Backend implementations are private (_sqlite.py, _postgres.py) and must never
be imported directly by engines, query engines, or importers.
"""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Optional

from ._base import BizRepository


def create_repository(backend: str, **kwargs) -> BizRepository:
    """
    Construct the named repository backend.

    Supported backends
    ------------------
    "sqlite"
        BizRepositorySQLite(conn=<sqlite3.Connection>)
        REFERENCE implementation only.  Never in production.
        Caller must call repo.setup_schema() after construction.

    "postgres"
        BizRepositoryPG(conn_factory=<callable>)   — production / pooled use
        BizRepositoryPG(conn=<psycopg2 connection>) — testing / single-thread
        Requires: pip install tenderscope-kg[postgres]
        Caller must call repo.setup_schema() after first construction.

    All kwargs are forwarded to the backend constructor.
    """
    if backend == "sqlite":
        from ._sqlite import BizRepositorySQLite
        return BizRepositorySQLite(**kwargs)
    if backend == "postgres":
        from ._postgres import BizRepositoryPG  # type: ignore[import]
        return BizRepositoryPG(**kwargs)
    raise ValueError(
        f"Unknown repository backend: {backend!r}. "
        f"Valid options: 'sqlite', 'postgres'."
    )


def open_repository(sqlite_db_path: Optional[Path] = None) -> BizRepository:
    """Production-path factory: select the backend from the environment.

    Decision logic (evaluated at call time):

    1. If the ``DATABASE_URL`` environment variable is set and non-empty,
       connect to PostgreSQL using that URL.  This is the **production path**.
    2. Otherwise fall back to SQLite, creating the database file at
       *sqlite_db_path* (or ``":memory:"`` if *None*).

    ``setup_schema()`` is called before returning so callers need not do it.
    """
    database_url = os.environ.get("DATABASE_URL", "").strip()
    if database_url:
        import psycopg2  # noqa: PLC0415
        repo = create_repository(
            "postgres",
            conn_factory=lambda: psycopg2.connect(database_url),
        )
    else:
        if sqlite_db_path is not None:
            sqlite_db_path = Path(sqlite_db_path)
            sqlite_db_path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(sqlite_db_path), check_same_thread=False)
        else:
            conn = sqlite3.connect(":memory:", check_same_thread=False)
        repo = create_repository("sqlite", conn=conn)
    repo.setup_schema()
    return repo


__all__ = ["BizRepository", "create_repository", "open_repository"]
