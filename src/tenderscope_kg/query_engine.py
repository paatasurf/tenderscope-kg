"""
Query engine: high-level graph queries that return token-budgeted context packs.
All public methods return plain dicts ready for JSON serialisation / MCP tools.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Optional

from .db import GraphDB, make_entity_id
from .models import Entity, EntityKind, RelationKind

try:
    import tiktoken
    _ENC = tiktoken.get_encoding("cl100k_base")
    def _count_tokens(text: str) -> int:
        return len(_ENC.encode(text))
except Exception:
    def _count_tokens(text: str) -> int:
        return len(text) // 4   # rough fallback


_DEFAULT_TOKEN_BUDGET = 4000


class QueryEngine:
    def __init__(self, db: GraphDB):
        self.db = db

    # ── Core lookup tools ────────────────────────────────────────────────

    def search(
        self,
        query: str,
        kinds: Optional[list[str]] = None,
        limit: int = 20,
    ) -> dict:
        """FTS + glob search over entities. Returns compact list."""
        fts_results = self.db.search_fts(query, limit=limit)
        glob_results = self.db.find_entities(
            name_glob=f"*{query}*",
            kind=EntityKind(kinds[0]) if kinds and len(kinds) == 1 else None,
            limit=limit,
        )
        # Merge, dedup by id
        seen: dict[str, Entity] = {}
        for e in fts_results + glob_results:
            seen[e.id] = e
        results = list(seen.values())[:limit]

        if kinds:
            kind_set = set(kinds)
            results = [e for e in results if e.kind.value in kind_set]

        return {
            "query": query,
            "count": len(results),
            "results": [_entity_summary(e) for e in results],
        }

    def get_entity_detail(self, qualified_name: str) -> dict:
        """Full detail for one entity including immediate neighbours."""
        e = self.db.get_entity_by_qname(qualified_name)
        if not e:
            # Try partial match
            results = self.db.find_entities(name_glob=f"*{qualified_name}*", limit=5)
            if not results:
                return {"error": f"Entity not found: {qualified_name}"}
            e = results[0]

        neighbors = self.db.get_neighbors(e.id, direction="both", limit=30)
        callers = self.db.get_callers(e.id, limit=10)
        callees = self.db.get_callees(e.id, limit=10)

        return {
            "entity": _entity_full(e),
            "callers": [_entity_summary(x) for x in callers],
            "callees": [_entity_summary(x) for x in callees],
            "neighbors": [
                {"relation": rel.kind.value, "entity": _entity_summary(ent)}
                for rel, ent in neighbors
            ],
        }

    def get_file_outline(self, file_path: str) -> dict:
        """All entities in a file, ordered by line, as a compact outline."""
        # Support partial path matching
        if not file_path.startswith("/"):
            entities = self.db.find_entities(
                file_path=f"*{file_path}*", limit=200
            )
        else:
            entities = self.db.get_file_entities(file_path)

        if not entities:
            return {"error": f"No entities found for file: {file_path}", "file": file_path}

        # Group by actual file_path
        by_file: dict[str, list[Entity]] = {}
        for e in entities:
            by_file.setdefault(e.file_path, []).append(e)

        outlines = []
        for fp, ents in by_file.items():
            ents.sort(key=lambda x: x.line_start)
            outlines.append({
                "file": fp,
                "entities": [_entity_summary(e) for e in ents],
            })

        return {"files": outlines, "total": len(entities)}

    def get_callers(self, qualified_name: str, depth: int = 1, limit: int = 20) -> dict:
        """Who calls this function? Optionally recursive."""
        e = self._resolve(qualified_name)
        if not e:
            return {"error": f"Not found: {qualified_name}"}

        result: dict[str, Any] = {
            "target": _entity_summary(e),
            "callers": [],
        }
        visited = {e.id}
        frontier = [e.id]

        for d in range(depth):
            next_frontier = []
            for eid in frontier:
                callers = self.db.get_callers(eid, limit=limit)
                for caller in callers:
                    if caller.id not in visited:
                        visited.add(caller.id)
                        next_frontier.append(caller.id)
                        result["callers"].append({
                            "depth": d + 1,
                            "entity": _entity_summary(caller),
                        })
            frontier = next_frontier

        return result

    def get_callees(self, qualified_name: str, depth: int = 1, limit: int = 20) -> dict:
        """What does this function call?"""
        e = self._resolve(qualified_name)
        if not e:
            return {"error": f"Not found: {qualified_name}"}

        result: dict[str, Any] = {
            "source": _entity_summary(e),
            "callees": [],
        }
        visited = {e.id}
        frontier = [e.id]

        for d in range(depth):
            next_frontier = []
            for eid in frontier:
                callees = self.db.get_callees(eid, limit=limit)
                for callee in callees:
                    if callee.id not in visited:
                        visited.add(callee.id)
                        next_frontier.append(callee.id)
                        result["callees"].append({
                            "depth": d + 1,
                            "entity": _entity_summary(callee),
                        })
            frontier = next_frontier

        return result

    def get_inheritance_chain(self, qualified_name: str) -> dict:
        """Full class hierarchy: ancestors and descendants."""
        e = self._resolve(qualified_name)
        if not e:
            return {"error": f"Not found: {qualified_name}"}

        ancestors = self._walk_relations(e.id, RelationKind.INHERITS, direction="out")
        descendants = self._walk_relations(e.id, RelationKind.INHERITS, direction="in")

        return {
            "class": _entity_summary(e),
            "ancestors": [_entity_summary(x) for x in ancestors],
            "descendants": [_entity_summary(x) for x in descendants],
        }

    def get_imports(self, file_path: str) -> dict:
        """What does a file import?"""
        entities = self.db.get_file_entities(file_path)
        if not entities:
            entities = self.db.find_entities(file_path=f"*{file_path}*", limit=5)
        if not entities:
            return {"error": f"File not found: {file_path}"}

        mod_entity = next((e for e in entities if e.kind == EntityKind.MODULE), entities[0])
        neighbors = self.db.get_neighbors(
            mod_entity.id,
            direction="out",
            kinds=[RelationKind.IMPORTS, RelationKind.RE_EXPORTS],
            limit=100,
        )
        imports = [{"relation": r.kind.value, "target": _entity_summary(e), "line": r.line}
                   for r, e in neighbors]
        imports.sort(key=lambda x: x.get("line") or 0)

        return {
            "file": file_path,
            "module": mod_entity.qualified_name,
            "imports": imports,
            "count": len(imports),
        }

    def list_api_routes(self) -> dict:
        """All HTTP API routes across the codebase."""
        routes = self.db.get_api_routes()
        return {
            "count": len(routes),
            "routes": [_entity_full(r) for r in routes],
        }

    def list_sql_tables(self) -> dict:
        """All SQL tables and their columns."""
        tables = self.db.get_sql_tables()
        result = []
        for t in tables:
            neighbors = self.db.get_neighbors(
                t.id, direction="out", kinds=[RelationKind.CONTAINS], limit=100
            )
            cols = [e for _, e in neighbors if e.kind == EntityKind.SQL_COLUMN]
            result.append({
                **_entity_full(t),
                "columns": [_entity_summary(c) for c in cols],
            })
        return {"count": len(tables), "tables": result}

    def get_table_usage(self, table_name: str) -> dict:
        """Which files/functions read or write to a SQL table?"""
        teid = make_entity_id(EntityKind.SQL_TABLE, table_name)
        t = self.db.get_entity(teid)
        if not t:
            results = self.db.find_entities(name_glob=f"*{table_name}*",
                                             kind=EntityKind.SQL_TABLE, limit=5)
            if not results:
                return {"error": f"Table not found: {table_name}"}
            t = results[0]

        neighbors = self.db.get_neighbors(
            t.id,
            direction="in",
            kinds=[RelationKind.USES_TABLE, RelationKind.WRITES_COLUMN,
                   RelationKind.READS_COLUMN],
            limit=100,
        )
        usages = [{"relation": r.kind.value, "entity": _entity_summary(e), "file": r.file_path, "line": r.line}
                  for r, e in neighbors]
        return {"table": _entity_full(t), "usages": usages, "count": len(usages)}

    def get_subgraph(self, qualified_name: str, depth: int = 2) -> dict:
        """Return entities+relations within N hops — the raw subgraph."""
        e = self._resolve(qualified_name)
        if not e:
            return {"error": f"Not found: {qualified_name}"}
        entities, relations = self.db.subgraph(e.id, depth=depth)
        return {
            "center": _entity_summary(e),
            "depth": depth,
            "entities": [_entity_summary(x) for x in entities],
            "relations": [
                {"from": r.source_id, "to": r.target_id, "kind": r.kind.value}
                for r in relations
            ],
        }

    def context_pack(
        self,
        task_description: str,
        token_budget: int = _DEFAULT_TOKEN_BUDGET,
        seed_names: Optional[list[str]] = None,
    ) -> dict:
        """
        Build a token-budgeted context pack for a task.
        Searches the graph for relevant entities, ranks them,
        and assembles a compact representation within the token budget.
        """
        # 1. Collect candidate entities via FTS + seed names
        candidates: dict[str, Entity] = {}

        fts_hits = self.db.search_fts(task_description, limit=30)
        for e in fts_hits:
            candidates[e.id] = e

        # Word-by-word FTS fallback when the full phrase hits nothing
        if not candidates:
            words = [w for w in re.sub(r"[^a-z0-9_]", " ", task_description.lower()).split()
                     if len(w) > 3]
            for word in words:
                for e in self.db.search_fts(word, limit=10):
                    candidates[e.id] = e
                for e in self.db.find_entities(name_glob=f"*{word}*", limit=10):
                    candidates[e.id] = e

        # Last-resort fallback: grab all callables so context is never empty
        if not candidates:
            for kind in (EntityKind.FUNCTION, EntityKind.METHOD, EntityKind.CLASS):
                for e in self.db.find_entities(kind=kind, limit=20):
                    candidates[e.id] = e

        if seed_names:
            for name in seed_names:
                e = self._resolve(name)
                if e:
                    candidates[e.id] = e
                    # Expand one hop
                    neighbors = self.db.get_neighbors(e.id, direction="both", limit=20)
                    for _, ne in neighbors:
                        candidates[ne.id] = ne

        # 2. Score candidates (higher = more relevant)
        scored: list[tuple[float, Entity]] = []
        query_tokens = set(re.sub(r"[^a-z0-9_]", " ", task_description.lower()).split())

        for e in candidates.values():
            score = 0.0
            name_tokens = set(re.sub(r"[^a-z0-9_]", " ", e.name.lower()).split())
            score += len(query_tokens & name_tokens) * 3.0
            if e.kind in (EntityKind.FUNCTION, EntityKind.METHOD, EntityKind.CLASS):
                score += 2.0
            if e.kind == EntityKind.API_ROUTE:
                score += 1.5
            if e.kind == EntityKind.SQL_TABLE:
                score += 1.5
            if e.docstring:
                doc_tokens = set(e.docstring.lower().split())
                score += len(query_tokens & doc_tokens) * 1.0
            scored.append((score, e))

        scored.sort(key=lambda x: -x[0])

        # 3. Fill budget
        sections: list[str] = []
        used_tokens = 0

        for _, e in scored:
            line = e.token_repr()
            t = _count_tokens(line)
            if used_tokens + t > token_budget:
                break
            sections.append(line)
            used_tokens += t

            # Add neighbors summary if budget allows
            if e.kind in (EntityKind.CLASS, EntityKind.FUNCTION, EntityKind.METHOD):
                callers = self.db.get_callers(e.id, limit=5)
                callees = self.db.get_callees(e.id, limit=5)
                extras = []
                if callers:
                    extras.append(f"  callers: {', '.join(x.name for x in callers)}")
                if callees:
                    extras.append(f"  calls: {', '.join(x.name for x in callees)}")
                for ex in extras:
                    t2 = _count_tokens(ex)
                    if used_tokens + t2 <= token_budget:
                        sections.append(ex)
                        used_tokens += t2

        context_text = "\n".join(sections)
        return {
            "task": task_description,
            "token_budget": token_budget,
            "tokens_used": used_tokens,
            "entity_count": len(sections),
            "context": context_text,
        }

    def get_stats(self) -> dict:
        return self.db.get_stats()

    # ── Helpers ───────────────────────────────────────────────────────────

    def _resolve(self, name: str) -> Optional[Entity]:
        e = self.db.get_entity_by_qname(name)
        if e:
            return e
        results = self.db.find_entities(name_glob=f"*{name}*", limit=1)
        return results[0] if results else None

    def _walk_relations(
        self,
        entity_id: str,
        kind: RelationKind,
        direction: str,
        max_depth: int = 10,
    ) -> list[Entity]:
        visited: set[str] = {entity_id}
        frontier = [entity_id]
        results: list[Entity] = []
        for _ in range(max_depth):
            if not frontier:
                break
            next_f = []
            for eid in frontier:
                neighbors = self.db.get_neighbors(
                    eid, direction=direction, kinds=[kind], limit=20
                )
                for _, ne in neighbors:
                    if ne.id not in visited:
                        visited.add(ne.id)
                        results.append(ne)
                        next_f.append(ne.id)
            frontier = next_f
        return results


# ── Formatting helpers ────────────────────────────────────────────────────────

def _entity_summary(e: Entity) -> dict:
    d: dict = {
        "id": e.id,
        "kind": e.kind.value,
        "name": e.name,
        "qualified_name": e.qualified_name,
        "file": e.file_path,
        "line": e.line_start,
    }
    if e.signature:
        d["signature"] = e.signature
    if e.docstring:
        d["docstring"] = e.docstring[:120]
    return d


def _entity_full(e: Entity) -> dict:
    return {
        "id": e.id,
        "kind": e.kind.value,
        "name": e.name,
        "qualified_name": e.qualified_name,
        "file": e.file_path,
        "line_start": e.line_start,
        "line_end": e.line_end,
        "signature": e.signature,
        "docstring": e.docstring,
        "language": e.language,
        "extra": e.extra,
    }
