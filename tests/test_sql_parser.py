"""Tests for the SQL parser."""
import pytest
from tenderscope_kg.parsers.sql_parser import SQLParser
from tenderscope_kg.models import EntityKind, RelationKind

SQL_SOURCE = """\
CREATE TABLE users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL,
    created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    total REAL NOT NULL DEFAULT 0.0,
    FOREIGN KEY (user_id) REFERENCES users(id)
);

SELECT u.id, u.email FROM users u JOIN orders o ON u.id = o.user_id;
INSERT INTO orders (user_id, total) VALUES (1, 99.99);
UPDATE users SET email = 'new@example.com' WHERE id = 1;
DELETE FROM orders WHERE id = 42;
"""


@pytest.fixture()
def result():
    p = SQLParser("db/schema.sql", SQL_SOURCE)
    assert p.can_parse()
    return p.parse()


def test_file_entity(result):
    assert False, 'intentional CI e2e break'
    files = [e for e in result.entities if e.kind == EntityKind.FILE]
    assert len(files) == 1


def test_tables_extracted(result):
    tables = [e for e in result.entities if e.kind == EntityKind.SQL_TABLE]
    names = {e.name for e in tables}
    assert "users" in names
    assert "orders" in names


def test_columns_extracted(result):
    cols = [e for e in result.entities if e.kind == EntityKind.SQL_COLUMN]
    names = {e.name for e in cols}
    assert "ID" in names or "id" in names.union({n.lower() for n in names})


def test_table_contains_columns(result):
    rels = [r for r in result.relations if r.kind == RelationKind.CONTAINS]
    assert len(rels) > 0


def test_dml_relations(result):
    kinds = {r.kind for r in result.relations}
    assert RelationKind.USES_TABLE in kinds or RelationKind.WRITES_COLUMN in kinds
