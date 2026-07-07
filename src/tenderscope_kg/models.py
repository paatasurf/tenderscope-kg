"""
Core data models for the Knowledge Graph.
All entities and relationships are strongly-typed dataclasses.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class EntityKind(str, Enum):
    FILE = "file"
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    METHOD = "method"
    VARIABLE = "variable"
    SQL_TABLE = "sql_table"
    SQL_COLUMN = "sql_column"
    API_ROUTE = "api_route"
    CONFIG_KEY = "config_key"
    CONFIG_FILE = "config_file"
    PIPELINE = "pipeline"
    PIPELINE_STAGE = "pipeline_stage"
    INTERFACE = "interface"
    TYPE_ALIAS = "type_alias"
    ENUM = "enum"
    CONSTANT = "constant"
    DECORATOR = "decorator"


class RelationKind(str, Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    INHERITS = "inherits"
    IMPLEMENTS = "implements"
    CONTAINS = "contains"
    DEFINES = "defines"
    USES_TABLE = "uses_table"
    READS_COLUMN = "reads_column"
    WRITES_COLUMN = "writes_column"
    HANDLES_ROUTE = "handles_route"
    DEPENDS_ON = "depends_on"
    DECORATED_BY = "decorated_by"
    OVERRIDES = "overrides"
    CONFIGURES = "configures"
    PIPELINE_STEP = "pipeline_step"
    REFERENCES = "references"
    EXPORTS = "exports"
    RE_EXPORTS = "re_exports"


@dataclass
class Entity:
    id: str                          # sha256[:16] of (kind + qualified_name)
    kind: EntityKind
    name: str
    qualified_name: str              # dotted path: module.Class.method
    file_path: str                   # repo-relative path
    line_start: int
    line_end: int
    signature: Optional[str] = None  # function/class signature
    docstring: Optional[str] = None
    language: Optional[str] = None
    extra: dict = field(default_factory=dict)  # language-specific metadata

    def token_repr(self) -> str:
        """Compact representation for token-budget context packs."""
        sig = f"({self.signature})" if self.signature else ""
        doc = f"  # {self.docstring[:80]}" if self.docstring else ""
        return f"{self.kind.value} {self.qualified_name}{sig}{doc}  [{self.file_path}:{self.line_start}]"


@dataclass
class Relation:
    id: str                          # sha256[:16] of (src_id + kind + tgt_id)
    source_id: str
    target_id: str
    kind: RelationKind
    file_path: Optional[str] = None  # file where the relation was found
    line: Optional[int] = None
    weight: float = 1.0              # call frequency, import depth, etc.
    extra: dict = field(default_factory=dict)


@dataclass
class IndexStats:
    repo_root: str
    files_indexed: int
    entities_total: int
    relations_total: int
    languages: dict[str, int]        # language -> file count
    index_time_s: float
    last_updated: str                # ISO 8601
    schema_version: str = "1"
