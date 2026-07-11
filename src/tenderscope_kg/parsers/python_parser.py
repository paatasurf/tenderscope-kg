"""
Python parser using stdlib ast.
Extracts: modules, classes, functions, methods, imports, calls, inheritance.
"""

from __future__ import annotations

import ast
from pathlib import Path

from ..db import make_entity_id, make_relation_id
from ..models import Entity, EntityKind, Relation, RelationKind
from .base import BaseParser, ParseResult

_PYTHON_EXTS = {".py", ".pyi"}


class PythonParser(BaseParser):
    def __init__(self, file_path: str, source: str):
        super().__init__(file_path, source)
        self.language = "python"

    def can_parse(self) -> bool:
        return self.ext in _PYTHON_EXTS

    def parse(self) -> ParseResult:
        try:
            tree = ast.parse(self.source, filename=self.file_path)
        except SyntaxError:
            return ParseResult(entities=[], relations=[])

        entities: list[Entity] = []
        relations: list[Relation] = []
        module_name = self._module_name()
        lines = self.source.splitlines()

        # File entity
        file_eid = make_entity_id(EntityKind.FILE, self.file_path)
        entities.append(
            Entity(
                id=file_eid,
                kind=EntityKind.FILE,
                name=Path(self.file_path).name,
                qualified_name=self.file_path,
                file_path=self.file_path,
                line_start=1,
                line_end=len(lines),
                language="python",
            )
        )

        # Module entity
        mod_eid = make_entity_id(EntityKind.MODULE, module_name)
        entities.append(
            Entity(
                id=mod_eid,
                kind=EntityKind.MODULE,
                name=module_name.split(".")[-1],
                qualified_name=module_name,
                file_path=self.file_path,
                line_start=1,
                line_end=len(lines),
                docstring=ast.get_docstring(tree),
                language="python",
            )
        )
        relations.append(
            Relation(
                id=make_relation_id(file_eid, RelationKind.CONTAINS, mod_eid),
                source_id=file_eid,
                target_id=mod_eid,
                kind=RelationKind.CONTAINS,
                file_path=self.file_path,
            )
        )

        visitor = _PythonVisitor(self.file_path, module_name)
        visitor.visit(tree)
        entities.extend(visitor.entities)
        relations.extend(visitor.relations)

        # module DEFINES top-level classes/functions
        for e in visitor.entities:
            if (
                e.kind in (EntityKind.CLASS, EntityKind.FUNCTION)
                and e.qualified_name.count(".") == module_name.count(".") + 1
            ):
                relations.append(
                    Relation(
                        id=make_relation_id(mod_eid, RelationKind.DEFINES, e.id),
                        source_id=mod_eid,
                        target_id=e.id,
                        kind=RelationKind.DEFINES,
                        file_path=self.file_path,
                    )
                )

        return ParseResult(entities=entities, relations=relations)


class _PythonVisitor(ast.NodeVisitor):
    def __init__(self, file_path: str, module_name: str):
        self.file_path = file_path
        self.module_name = module_name
        self.entities: list[Entity] = []
        self.relations: list[Relation] = []
        self._scope: list[str] = []  # stack of names for nested scopes

    def _qname(self, name: str) -> str:
        return ".".join([self.module_name] + self._scope + [name])

    def _sig(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> str:
        parts: list[str] = []
        defs = node.args.defaults
        n_args = len(node.args.args)
        offset = n_args - len(defs)
        for i, arg in enumerate(node.args.args):
            di = i - offset
            if di >= 0:
                try:
                    parts.append(f"{arg.arg}={ast.unparse(defs[di])}")
                except Exception:
                    parts.append(f"{arg.arg}=...")
            else:
                parts.append(arg.arg)
        if node.args.vararg:
            parts.append(f"*{node.args.vararg.arg}")
        if node.args.kwarg:
            parts.append(f"**{node.args.kwarg.arg}")
        ret = ""
        if node.returns:
            try:
                ret = f" -> {ast.unparse(node.returns)}"
            except Exception:
                pass
        return f"({', '.join(parts)}){ret}"

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qname = self._qname(node.name)
        eid = make_entity_id(EntityKind.CLASS, qname)
        bases = []
        for b in node.bases:
            try:
                bases.append(ast.unparse(b))
            except Exception:
                pass
        self.entities.append(
            Entity(
                id=eid,
                kind=EntityKind.CLASS,
                name=node.name,
                qualified_name=qname,
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                docstring=ast.get_docstring(node),
                language="python",
                extra={
                    "bases": bases,
                    "decorators": self._deco_names(node.decorator_list),
                },
            )
        )
        for base in bases:
            base_id = make_entity_id(EntityKind.CLASS, base)
            self.relations.append(
                Relation(
                    id=make_relation_id(eid, RelationKind.INHERITS, base_id),
                    source_id=eid,
                    target_id=base_id,
                    kind=RelationKind.INHERITS,
                    file_path=self.file_path,
                    line=node.lineno,
                    extra={"unresolved_target": base},
                )
            )
        self._scope.append(node.name)
        self.generic_visit(node)
        self._scope.pop()

    def _visit_func(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        in_class = bool(self._scope) and any(
            make_entity_id(EntityKind.CLASS, ".".join([self.module_name] + self._scope[: i + 1]))
            for i in range(len(self._scope))
        )
        kind = EntityKind.METHOD if in_class else EntityKind.FUNCTION
        qname = self._qname(node.name)
        eid = make_entity_id(kind, qname)
        self.entities.append(
            Entity(
                id=eid,
                kind=kind,
                name=node.name,
                qualified_name=qname,
                file_path=self.file_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                signature=self._sig(node),
                docstring=ast.get_docstring(node),
                language="python",
                extra={
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "decorators": self._deco_names(node.decorator_list),
                },
            )
        )
        if self._scope:
            parent_qname = ".".join([self.module_name] + self._scope)
            parent_kind = EntityKind.CLASS
            parent_id = make_entity_id(parent_kind, parent_qname)
            self.relations.append(
                Relation(
                    id=make_relation_id(parent_id, RelationKind.CONTAINS, eid),
                    source_id=parent_id,
                    target_id=eid,
                    kind=RelationKind.CONTAINS,
                    file_path=self.file_path,
                )
            )
        # FastAPI / Flask / APIRouter decorator routes: @app.get("/path") etc.
        _HTTP_METHODS = {"get", "post", "put", "patch", "delete", "options", "head", "all"}
        for deco in node.decorator_list:
            if not isinstance(deco, ast.Call):
                continue
            func = deco.func
            # attr style: @app.get("/path") or @router.post("/path")
            if isinstance(func, ast.Attribute) and func.attr.lower() in _HTTP_METHODS:
                http_method = func.attr.upper()
                route_path = ""
                if deco.args:
                    try:
                        route_path = ast.literal_eval(deco.args[0])
                    except Exception:
                        try:
                            route_path = ast.unparse(deco.args[0])
                        except Exception:
                            route_path = "?"
                if route_path:
                    route_qname = f"{self.module_name}.{http_method}:{route_path}"
                    route_eid = make_entity_id(EntityKind.API_ROUTE, route_qname)
                    self.entities.append(
                        Entity(
                            id=route_eid,
                            kind=EntityKind.API_ROUTE,
                            name=f"{http_method} {route_path}",
                            qualified_name=route_qname,
                            file_path=self.file_path,
                            line_start=node.lineno,
                            line_end=node.lineno,
                            language="python",
                            extra={"method": http_method, "path": route_path},
                        )
                    )
                    self.relations.append(
                        Relation(
                            id=make_relation_id(eid, RelationKind.HANDLES_ROUTE, route_eid),
                            source_id=eid,
                            target_id=route_eid,
                            kind=RelationKind.HANDLES_ROUTE,
                            file_path=self.file_path,
                            line=node.lineno,
                        )
                    )
        # Collect calls inside function body
        cv = _CallVisitor(self.file_path, eid, node.lineno)
        for child in ast.walk(node):
            if isinstance(child, ast.Call):
                cv.record_call(child)
        self.relations.extend(cv.relations)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_func(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_func(node)

    def visit_Import(self, node: ast.Import) -> None:
        mod_id = make_entity_id(EntityKind.MODULE, self.module_name)
        for alias in node.names:
            target_id = make_entity_id(EntityKind.MODULE, alias.name)
            self.relations.append(
                Relation(
                    id=make_relation_id(mod_id, RelationKind.IMPORTS, target_id),
                    source_id=mod_id,
                    target_id=target_id,
                    kind=RelationKind.IMPORTS,
                    file_path=self.file_path,
                    line=node.lineno,
                    extra={"unresolved_target": alias.name, "alias": alias.asname},
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if not node.module:
            return
        mod_id = make_entity_id(EntityKind.MODULE, self.module_name)
        target_id = make_entity_id(EntityKind.MODULE, node.module)
        self.relations.append(
            Relation(
                id=make_relation_id(mod_id, RelationKind.IMPORTS, target_id),
                source_id=mod_id,
                target_id=target_id,
                kind=RelationKind.IMPORTS,
                file_path=self.file_path,
                line=node.lineno,
                extra={
                    "unresolved_target": node.module,
                    "names": [a.name for a in node.names],
                    "level": node.level,
                },
            )
        )

    @staticmethod
    def _deco_names(decorators: list[ast.expr]) -> list[str]:
        names = []
        for d in decorators:
            try:
                names.append(ast.unparse(d))
            except Exception:
                names.append("?")
        return names


class _CallVisitor:
    def __init__(self, file_path: str, caller_id: str, base_line: int):
        self.file_path = file_path
        self.caller_id = caller_id
        self.base_line = base_line
        self.relations: list[Relation] = []

    def record_call(self, node: ast.Call) -> None:
        try:
            callee_name = ast.unparse(node.func)
        except Exception:
            return
        # Heuristic: use last dotted segment to form a candidate entity id
        short = callee_name.split(".")[-1]
        # We store an unresolved reference; the resolver will link them post-index
        target_id = make_entity_id(EntityKind.FUNCTION, callee_name)
        line = getattr(node, "lineno", self.base_line)
        rel_id = make_relation_id(self.caller_id, RelationKind.CALLS, target_id)
        self.relations.append(
            Relation(
                id=rel_id,
                source_id=self.caller_id,
                target_id=target_id,
                kind=RelationKind.CALLS,
                file_path=self.file_path,
                line=line,
                extra={"unresolved_target": callee_name, "short": short},
            )
        )
