"""
SQL parser (DDL + DML patterns via regex).
Extracts: tables, columns, CREATE/ALTER, SELECT/INSERT/UPDATE/DELETE references.
Also detects SQLAlchemy model classes in Python source via inline heuristics.
"""
from __future__ import annotations

import re
from pathlib import Path

from ..db import make_entity_id, make_relation_id
from ..models import Entity, EntityKind, Relation, RelationKind
from .base import BaseParser, ParseResult

_SQL_EXTS = {".sql"}

_RE_CREATE_TABLE = re.compile(
    r"CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`'\"]?(\w+)[`'\"]?\s*\(",
    re.IGNORECASE | re.MULTILINE,
)
_RE_COLUMN_DEF = re.compile(
    r"^\s+[`'\"]?(\w+)[`'\"]?\s+([\w()]+(?:\s+\w+)?)",
    re.MULTILINE,
)
_RE_ALTER_TABLE = re.compile(
    r"ALTER\s+TABLE\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)
_RE_SELECT_FROM = re.compile(
    r"\bFROM\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)
_RE_INSERT_INTO = re.compile(
    r"\bINSERT\s+(?:OR\s+\w+\s+)?INTO\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)
_RE_UPDATE = re.compile(
    r"\bUPDATE\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)
_RE_DELETE_FROM = re.compile(
    r"\bDELETE\s+FROM\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)
_RE_JOIN = re.compile(
    r"\bJOIN\s+[`'\"]?(\w+)[`'\"]?",
    re.IGNORECASE | re.MULTILINE,
)


def _line_of(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


class SQLParser(BaseParser):
    def __init__(self, file_path: str, source: str):
        super().__init__(file_path, source)
        self.language = "sql"

    def can_parse(self) -> bool:
        return self.ext in _SQL_EXTS

    def parse(self) -> ParseResult:
        entities: list[Entity] = []
        relations: list[Relation] = []
        src = self.source
        fp = self.file_path
        lines = src.splitlines()

        file_eid = make_entity_id(EntityKind.FILE, fp)
        entities.append(Entity(
            id=file_eid,
            kind=EntityKind.FILE,
            name=Path(fp).name,
            qualified_name=fp,
            file_path=fp,
            line_start=1,
            line_end=len(lines),
            language="sql",
        ))

        # CREATE TABLE → SQL_TABLE + SQL_COLUMNs
        for m in _RE_CREATE_TABLE.finditer(src):
            table_name = m.group(1)
            teid = make_entity_id(EntityKind.SQL_TABLE, table_name)
            line = _line_of(src, m.start())
            entities.append(Entity(
                id=teid,
                kind=EntityKind.SQL_TABLE,
                name=table_name,
                qualified_name=table_name,
                file_path=fp,
                line_start=line,
                line_end=line,
                language="sql",
            ))
            relations.append(Relation(
                id=make_relation_id(file_eid, RelationKind.DEFINES, teid),
                source_id=file_eid,
                target_id=teid,
                kind=RelationKind.DEFINES,
                file_path=fp,
                line=line,
            ))
            # Extract the column block between the outer parens
            body_start = src.find("(", m.end() - 1)
            if body_start != -1:
                depth = 0
                body_end = body_start
                for i, ch in enumerate(src[body_start:], body_start):
                    if ch == "(":
                        depth += 1
                    elif ch == ")":
                        depth -= 1
                        if depth == 0:
                            body_end = i
                            break
                body = src[body_start + 1:body_end]
                for cm in _RE_COLUMN_DEF.finditer(body):
                    col_name = cm.group(1).upper()
                    if col_name in ("PRIMARY", "UNIQUE", "CONSTRAINT", "INDEX",
                                    "FOREIGN", "KEY", "CHECK"):
                        continue
                    col_qname = f"{table_name}.{col_name}"
                    ceid = make_entity_id(EntityKind.SQL_COLUMN, col_qname)
                    col_line = _line_of(src, body_start + cm.start())
                    entities.append(Entity(
                        id=ceid,
                        kind=EntityKind.SQL_COLUMN,
                        name=col_name,
                        qualified_name=col_qname,
                        file_path=fp,
                        line_start=col_line,
                        line_end=col_line,
                        language="sql",
                        extra={"type": cm.group(2)},
                    ))
                    relations.append(Relation(
                        id=make_relation_id(teid, RelationKind.CONTAINS, ceid),
                        source_id=teid,
                        target_id=ceid,
                        kind=RelationKind.CONTAINS,
                        file_path=fp,
                        line=col_line,
                    ))

        # DML references — record which tables are READ / WRITTEN
        for pattern, rel_kind in [
            (_RE_SELECT_FROM, RelationKind.USES_TABLE),
            (_RE_JOIN, RelationKind.USES_TABLE),
            (_RE_INSERT_INTO, RelationKind.WRITES_COLUMN),
            (_RE_UPDATE, RelationKind.WRITES_COLUMN),
            (_RE_DELETE_FROM, RelationKind.WRITES_COLUMN),
        ]:
            for m in pattern.finditer(src):
                table_name = m.group(1)
                teid = make_entity_id(EntityKind.SQL_TABLE, table_name)
                line = _line_of(src, m.start())
                relations.append(Relation(
                    id=make_relation_id(file_eid, rel_kind, teid),
                    source_id=file_eid,
                    target_id=teid,
                    kind=rel_kind,
                    file_path=fp,
                    line=line,
                    extra={"unresolved_target": table_name},
                ))

        return ParseResult(entities=entities, relations=relations)
