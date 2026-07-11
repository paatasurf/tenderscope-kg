"""
JavaScript / TypeScript parser using regex + lightweight AST heuristics.
Extracts: files, classes, functions, imports, exports, API routes, type aliases, interfaces.
"""

from __future__ import annotations

import re
from pathlib import Path

from ..db import make_entity_id, make_relation_id
from ..models import Entity, EntityKind, Relation, RelationKind
from .base import BaseParser, ParseResult

_JS_EXTS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts"}

# Patterns
_RE_CLASS = re.compile(
    r"^[ \t]*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)"
    r"(?:\s+extends\s+([\w.<>, ]+?))?"
    r"(?:\s+implements\s+([\w., ]+?))?[\s{]",
    re.MULTILINE,
)
_RE_FUNCTION = re.compile(
    r"^[ \t]*(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*"
    r"(<[^>]*>)?\s*\(([^)]*)\)(?:\s*:\s*([\w<>\[\]| ,?]+))?",
    re.MULTILINE,
)
_RE_ARROW = re.compile(
    r"^[ \t]*(?:export\s+)?(?:const|let|var)\s+(\w+)"
    r"\s*(?::\s*[\w<>\[\]| ,?]+)?\s*=\s*(?:async\s+)?\(",
    re.MULTILINE,
)
_RE_IMPORT = re.compile(
    r"""^[ \t]*import\s+(?:type\s+)?(?:[\w*, {}\n]+from\s+)?['"]([^'"]+)['"]""",
    re.MULTILINE,
)
_RE_REQUIRE = re.compile(
    r"""require\(['"]([^'"]+)['"]\)""",
    re.MULTILINE,
)
_RE_EXPORT_FROM = re.compile(
    r"""^[ \t]*export\s+(?:\*|[\w{}, ]+)\s+from\s+['"]([^'"]+)['"]""",
    re.MULTILINE,
)
# API route patterns — Express / Hono / Fastify style
_RE_ROUTE = re.compile(
    r"""(?:app|router|server|hono)\s*\.\s*(get|post|put|patch|delete|options|all)\s*\(\s*['"`]([^'"`]+)['"`]""",
    re.MULTILINE | re.IGNORECASE,
)
# TypeScript interface / type alias
_RE_INTERFACE = re.compile(
    r"^[ \t]*(?:export\s+)?interface\s+(\w+)(?:\s+extends\s+([\w., ]+))?[\s{]",
    re.MULTILINE,
)
_RE_TYPE_ALIAS = re.compile(
    r"^[ \t]*(?:export\s+)?type\s+(\w+)\s*(?:<[^>]*>)?\s*=",
    re.MULTILINE,
)
_RE_ENUM = re.compile(
    r"^[ \t]*(?:export\s+)?(?:const\s+)?enum\s+(\w+)\s*\{",
    re.MULTILINE,
)


def _line_of(source: str, pos: int) -> int:
    return source[:pos].count("\n") + 1


class JavaScriptParser(BaseParser):
    def __init__(self, file_path: str, source: str):
        super().__init__(file_path, source)
        self.language = "typescript" if self.ext in {".ts", ".tsx", ".mts"} else "javascript"

    def can_parse(self) -> bool:
        return self.ext in _JS_EXTS

    def parse(self) -> ParseResult:
        entities: list[Entity] = []
        relations: list[Relation] = []
        src = self.source
        fp = self.file_path
        mod = self._module_name()
        lines = src.splitlines()

        # File entity
        file_eid = make_entity_id(EntityKind.FILE, fp)
        entities.append(
            Entity(
                id=file_eid,
                kind=EntityKind.FILE,
                name=Path(fp).name,
                qualified_name=fp,
                file_path=fp,
                line_start=1,
                line_end=len(lines),
                language=self.language,
            )
        )

        # Module entity
        mod_eid = make_entity_id(EntityKind.MODULE, mod)
        entities.append(
            Entity(
                id=mod_eid,
                kind=EntityKind.MODULE,
                name=mod.split(".")[-1],
                qualified_name=mod,
                file_path=fp,
                line_start=1,
                line_end=len(lines),
                language=self.language,
            )
        )
        relations.append(
            Relation(
                id=make_relation_id(file_eid, RelationKind.CONTAINS, mod_eid),
                source_id=file_eid,
                target_id=mod_eid,
                kind=RelationKind.CONTAINS,
                file_path=fp,
            )
        )

        # Classes
        for m in _RE_CLASS.finditer(src):
            name = m.group(1)
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.CLASS, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.CLASS,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                    extra={"extends": m.group(2) or "", "implements": m.group(3) or ""},
                )
            )
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.DEFINES, eid),
                    source_id=mod_eid,
                    target_id=eid,
                    kind=RelationKind.DEFINES,
                    file_path=fp,
                    line=line,
                )
            )
            if m.group(2):
                for base in re.split(r",\s*", m.group(2).strip()):
                    base = base.strip().split("<")[0]
                    if base:
                        base_id = make_entity_id(EntityKind.CLASS, base)
                        relations.append(
                            Relation(
                                id=make_relation_id(eid, RelationKind.INHERITS, base_id),
                                source_id=eid,
                                target_id=base_id,
                                kind=RelationKind.INHERITS,
                                file_path=fp,
                                line=line,
                                extra={"unresolved_target": base},
                            )
                        )

        # Named functions
        for m in _RE_FUNCTION.finditer(src):
            name = m.group(1)
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.FUNCTION, qname)
            line = _line_of(src, m.start())
            sig = f"({m.group(3) or ''})"
            if m.group(4):
                sig += f": {m.group(4)}"
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.FUNCTION,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    signature=sig,
                    language=self.language,
                )
            )
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.DEFINES, eid),
                    source_id=mod_eid,
                    target_id=eid,
                    kind=RelationKind.DEFINES,
                    file_path=fp,
                    line=line,
                )
            )

        # Arrow functions assigned to const/let
        for m in _RE_ARROW.finditer(src):
            name = m.group(1)
            # Skip if already captured as named function
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.FUNCTION, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.FUNCTION,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                    extra={"arrow": True},
                )
            )
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.DEFINES, eid),
                    source_id=mod_eid,
                    target_id=eid,
                    kind=RelationKind.DEFINES,
                    file_path=fp,
                    line=line,
                )
            )

        # Interfaces
        for m in _RE_INTERFACE.finditer(src):
            name = m.group(1)
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.INTERFACE, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.INTERFACE,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                    extra={"extends": m.group(2) or ""},
                )
            )

        # Type aliases
        for m in _RE_TYPE_ALIAS.finditer(src):
            name = m.group(1)
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.TYPE_ALIAS, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.TYPE_ALIAS,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                )
            )

        # Enums
        for m in _RE_ENUM.finditer(src):
            name = m.group(1)
            qname = f"{mod}.{name}"
            eid = make_entity_id(EntityKind.ENUM, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.ENUM,
                    name=name,
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                )
            )

        # API routes
        for m in _RE_ROUTE.finditer(src):
            method = m.group(1).upper()
            path = m.group(2)
            qname = f"{mod}.{method}:{path}"
            eid = make_entity_id(EntityKind.API_ROUTE, qname)
            line = _line_of(src, m.start())
            entities.append(
                Entity(
                    id=eid,
                    kind=EntityKind.API_ROUTE,
                    name=f"{method} {path}",
                    qualified_name=qname,
                    file_path=fp,
                    line_start=line,
                    line_end=line,
                    language=self.language,
                    extra={"method": method, "path": path},
                )
            )
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.HANDLES_ROUTE, eid),
                    source_id=mod_eid,
                    target_id=eid,
                    kind=RelationKind.HANDLES_ROUTE,
                    file_path=fp,
                    line=line,
                )
            )

        # Imports
        for m in _RE_IMPORT.finditer(src):
            target = m.group(1)
            target_id = make_entity_id(EntityKind.MODULE, target)
            line = _line_of(src, m.start())
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.IMPORTS, target_id),
                    source_id=mod_eid,
                    target_id=target_id,
                    kind=RelationKind.IMPORTS,
                    file_path=fp,
                    line=line,
                    extra={"unresolved_target": target},
                )
            )

        for m in _RE_REQUIRE.finditer(src):
            target = m.group(1)
            target_id = make_entity_id(EntityKind.MODULE, target)
            line = _line_of(src, m.start())
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.IMPORTS, target_id),
                    source_id=mod_eid,
                    target_id=target_id,
                    kind=RelationKind.IMPORTS,
                    file_path=fp,
                    line=line,
                    extra={"unresolved_target": target, "style": "require"},
                )
            )

        for m in _RE_EXPORT_FROM.finditer(src):
            target = m.group(1)
            target_id = make_entity_id(EntityKind.MODULE, target)
            line = _line_of(src, m.start())
            relations.append(
                Relation(
                    id=make_relation_id(mod_eid, RelationKind.RE_EXPORTS, target_id),
                    source_id=mod_eid,
                    target_id=target_id,
                    kind=RelationKind.RE_EXPORTS,
                    file_path=fp,
                    line=line,
                    extra={"unresolved_target": target},
                )
            )

        return ParseResult(entities=entities, relations=relations)
