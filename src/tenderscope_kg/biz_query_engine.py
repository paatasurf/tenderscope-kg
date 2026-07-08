"""
TenderScope Intelligence Engine — Business Query Engine.

High-level query interface over the business knowledge graph.
All public methods return plain dicts (JSON-serialisable) for easy use
from MCP tools, CLI commands, and REST APIs.

Graph algorithm notes
---------------------
find_path / shortest_path use BFS in Python.  For a graph with millions of
nodes and tens-of-millions of edges, a pure-Python BFS over SQLite is
adequate for typical 3-6 hop paths because:
  - Each neighbour lookup is O(log N) with the (source_uid, kind) index.
  - BFS terminates early on first path found.
  - The visited set is bounded by the graph diameter, not graph size.

If sub-second performance is needed at very large scale, the path-finding
methods can be swapped for a compiled extension (e.g., sqlite-graph) or an
external engine (Neo4j) without changing the calling interface.
"""
from __future__ import annotations

import re
from collections import deque
from typing import Any, Optional

from .domain import BizEntity, BizEntityKind, BizRelationKind
from .repository._base import BizRepository


class BizQueryEngine:
    """Business graph query API."""

    def __init__(self, repo: BizRepository) -> None:
        self.repo = repo

    # ── Single-entity lookups ─────────────────────────────────────────────

    def entity(self, uid: str) -> dict:
        """Full detail for one entity by UID."""
        e = self.repo.get(uid)
        if not e:
            return {"error": f"Entity not found: {uid}"}
        neighbors = self.repo.get_neighbors(uid, direction="both", limit=50)
        return {
            "entity": e.to_full(),
            "neighbors": [
                {"relation": rel.kind.value, "entity": nb.to_summary()}
                for rel, nb in neighbors
            ],
        }

    def company(self, uid: str) -> dict:
        """Convenience wrapper: returns company + all directly connected entities."""
        e = self.repo.get(uid)
        if not e:
            return {"error": f"Company not found: {uid}"}
        if e.kind != BizEntityKind.COMPANY:
            return {"error": f"{uid} is a {e.kind.value}, not a company"}
        return self._rich_profile(e)

    def company_by_id(self, company_id: str) -> dict:
        """
        Look up a company by either a graph UID or a legacy scraper integer ID.

        Graph UID  (e.g. ``CMP-00000001``) — direct repository lookup.
        Scraper ID (e.g. ``"1247"`` or ``1247``) — resolved via the
        ``scraper_id`` attribute stored on every imported COMPANY entity.

        Returns the same rich profile dict as ``company()``.
        This is the single resolution point for both ID formats so that
        transport layers need no branching logic of their own.
        """
        if isinstance(company_id, str) and company_id.upper().startswith("CMP-"):
            return self.company(company_id)
        try:
            scraper_id = int(company_id)
        except (ValueError, TypeError):
            return {"error": f"Invalid company identifier: {company_id!r}"}
        hits = self.repo.find_by_attribute("scraper_id", scraper_id, limit=1)
        if not hits:
            return {"error": f"Company with scraper_id={scraper_id} not found"}
        e = hits[0]
        resolved = self.repo.resolve_alias(e.uid) or e
        return self._rich_profile(resolved)

    def company_identity(self, uid: str) -> dict:
        """
        Full identity record for a canonical company.

        Returns company_uid, display_name, canonical_name, all aliases with
        confidence + evidence, all external identifiers (BC Registry, BN,
        DUNS, LEI, LinkedIn, etc.), and any SAME_AS merge candidates.
        """
        identity = self.repo.company_identity(uid)
        if identity is None:
            return {"error": f"Canonical company not found: {uid}"}
        return identity.to_dict()

    def attach_identifier(
        self,
        company_uid: str,
        id_key: str,
        id_value: str,
        source: Optional[str] = None,
    ) -> dict:
        """
        Attach an external identifier to a canonical COMPANY entity.

        id_key should be a value from EXTERNAL_ID_KEYS, e.g.:
          'id_bc_registry', 'id_business_number', 'id_duns', 'id_lei'

        Returns the updated entity summary.
        """
        try:
            updated = self.repo.attach_identifier(
                company_uid=company_uid,
                id_key=id_key,
                id_value=id_value,
                source=source,
            )
            return {"ok": True, "entity": updated.to_summary()}
        except (KeyError, ValueError) as exc:
            return {"error": str(exc)}

    def tender(self, uid: str) -> dict:
        """Full tender record + bidders, awarding company, related project."""
        e = self.repo.get(uid)
        if not e:
            return {"error": f"Tender not found: {uid}"}
        if e.kind != BizEntityKind.TENDER:
            return {"error": f"{uid} is a {e.kind.value}, not a tender"}
        return self._rich_profile(e)

    def _rich_profile(self, e: BizEntity) -> dict:
        """Full entity with neighbors grouped by relation kind."""
        neighbors = self.repo.get_neighbors(e.uid, direction="both", limit=100)
        grouped: dict[str, list[dict]] = {}
        for rel, nb in neighbors:
            grouped.setdefault(rel.kind.value, []).append({
                **nb.to_summary(),
                "relation_confidence": rel.confidence,
                "relation_source": rel.source,
            })
        return {
            "entity": e.to_full(),
            "connections": grouped,
            "total_connections": len(neighbors),
        }

    # ── Multi-entity search ───────────────────────────────────────────────

    def search(
        self,
        query: str,
        kinds: Optional[list[str]] = None,
        limit: int = 20,
    ) -> dict:
        """FTS + name-like search over business entities.

        COMPANY_ALIAS hits are automatically resolved to their canonical
        COMPANY entity so callers always receive primary nodes.
        """
        fts_hits = self.repo.search_fts(query, limit=limit)
        like_hits = self.repo.find(name_like=query, limit=limit)
        seen: dict[str, BizEntity] = {}
        for e in fts_hits + like_hits:
            resolved = self.repo.resolve_alias(e.uid) or e
            seen[resolved.uid] = resolved
        results = list(seen.values())[:limit]
        if kinds:
            kind_set = set(kinds)
            results = [e for e in results if e.kind.value in kind_set]
        return {
            "query": query,
            "count": len(results),
            "results": [e.to_summary() for e in results],
        }

    def find_companies(self, name: str, limit: int = 20) -> dict:
        """Search for companies by name.

        Searches both COMPANY and COMPANY_ALIAS entities.  Alias hits are
        resolved to their canonical COMPANY so the caller always receives
        primary company nodes.  This means searching an alias name returns
        the canonical company that owns that alias.
        """
        hits = self.repo.find(kind=BizEntityKind.COMPANY, name_like=name, limit=limit)
        alias_hits = self.repo.find(kind=BizEntityKind.COMPANY_ALIAS, name_like=name, limit=limit)
        fts = self.repo.search_fts(name, limit=limit)
        fts_companies = [
            e for e in fts
            if e.kind in (BizEntityKind.COMPANY, BizEntityKind.COMPANY_ALIAS)
        ]
        seen: dict[str, BizEntity] = {}
        for e in hits + alias_hits + fts_companies:
            resolved = self.repo.resolve_alias(e.uid) or e
            if resolved.kind == BizEntityKind.COMPANY:
                seen[resolved.uid] = resolved
        results = list(seen.values())[:limit]
        return {"query": name, "count": len(results), "results": [e.to_summary() for e in results]}

    def related_companies(self, uid: str, limit: int = 20) -> dict:
        """
        Companies related to a given company via any relation chain ≤ 2 hops.
        Useful for competitive intelligence.
        """
        center = self.repo.get(uid)
        if not center:
            return {"error": f"Not found: {uid}"}

        seen: dict[str, BizEntity] = {}

        # Hop 1
        neighbors1 = self.repo.get_neighbors(uid, direction="both", limit=limit * 5)
        for rel, nb in neighbors1:
            if nb.kind == BizEntityKind.COMPANY and nb.uid != uid:
                seen[nb.uid] = nb
            # Hop 2 via non-company intermediaries
            if nb.kind != BizEntityKind.COMPANY:
                neighbors2 = self.repo.get_neighbors(
                    nb.uid, direction="both", limit=20
                )
                for _, nb2 in neighbors2:
                    if nb2.kind == BizEntityKind.COMPANY and nb2.uid != uid:
                        seen[nb2.uid] = nb2

        results = list(seen.values())[:limit]
        return {
            "center": center.to_summary(),
            "related_companies": [e.to_summary() for e in results],
            "count": len(results),
        }

    def contracts(self, company_uid: str, limit: int = 50) -> dict:
        """All contracts/tenders awarded to or submitted by a company."""
        company = self.repo.get(company_uid)
        if not company:
            return {"error": f"Not found: {company_uid}"}

        award_rels = self.repo.get_neighbors(
            company_uid,
            direction="both",
            kinds=[
                BizRelationKind.AWARDED_TO,
                BizRelationKind.AWARDED_BY,
                BizRelationKind.SUBMITTED_BID,
                BizRelationKind.HAS_CONTRACT,
            ],
            limit=limit,
        )
        results = []
        for rel, nb in award_rels:
            results.append({
                "relation": rel.kind.value,
                "entity": nb.to_summary(),
                "confidence": rel.confidence,
            })
        return {
            "company": company.to_summary(),
            "contracts": results,
            "count": len(results),
        }

    # ── Neighbor traversal ────────────────────────────────────────────────

    def neighbors(
        self,
        uid: str,
        direction: str = "both",
        kinds: Optional[list[str]] = None,
        active_only: bool = False,
        limit: int = 50,
    ) -> dict:
        """One-hop neighbours with optional relation-kind filter."""
        entity = self.repo.get(uid)
        if not entity:
            return {"error": f"Not found: {uid}"}
        rel_kinds = [BizRelationKind(k) for k in kinds] if kinds else None
        pairs = self.repo.get_neighbors(
            uid,
            direction=direction,
            kinds=rel_kinds,
            active_only=active_only,
            limit=limit,
        )
        return {
            "entity": entity.to_summary(),
            "direction": direction,
            "neighbors": [
                {"relation": rel.to_dict(), "entity": nb.to_summary()}
                for rel, nb in pairs
            ],
            "count": len(pairs),
        }

    # ── Path finding ──────────────────────────────────────────────────────

    def find_path(
        self,
        uid1: str,
        uid2: str,
        max_depth: int = 6,
        relation_kinds: Optional[list[str]] = None,
    ) -> dict:
        """
        Find ANY path between uid1 and uid2 using BFS.
        Returns the first path found (not necessarily shortest in edge-weight terms).
        """
        e1 = self.repo.get(uid1)
        e2 = self.repo.get(uid2)
        if not e1:
            return {"error": f"Source not found: {uid1}"}
        if not e2:
            return {"error": f"Target not found: {uid2}"}
        if uid1 == uid2:
            return {"path": [e1.to_summary()], "hops": 0}

        rel_kinds = [BizRelationKind(k) for k in relation_kinds] if relation_kinds else None
        path = self._bfs_path(uid1, uid2, max_depth, rel_kinds)
        if path is None:
            return {
                "found": False,
                "source": e1.to_summary(),
                "target": e2.to_summary(),
                "message": f"No path found within {max_depth} hops",
            }
        return {
            "found": True,
            "hops": len(path) - 1,
            "path": path,
        }

    def shortest_path(
        self,
        uid1: str,
        uid2: str,
        max_depth: int = 6,
    ) -> dict:
        """
        Unweighted shortest path (minimum hop count) via BFS.
        Identical to find_path for unweighted graphs.
        """
        return self.find_path(uid1, uid2, max_depth=max_depth)

    def _bfs_path(
        self,
        start: str,
        goal: str,
        max_depth: int,
        rel_kinds: Optional[list[BizRelationKind]],
    ) -> Optional[list[dict]]:
        """
        BFS returning a list of entity summary dicts representing the path,
        or None if unreachable within max_depth hops.
        """
        # queue entries: (current_uid, path_so_far_as_list_of_entity_summaries)
        visited: set[str] = {start}
        start_entity = self.repo.get(start)
        if not start_entity:
            return None
        queue: deque[tuple[str, list[dict]]] = deque(
            [(start, [start_entity.to_summary()])]
        )

        while queue:
            current_uid, path = queue.popleft()
            if len(path) > max_depth + 1:
                break
            neighbors = self.repo.get_neighbors(
                current_uid, direction="both", kinds=rel_kinds, limit=200
            )
            for rel, nb in neighbors:
                if nb.uid == goal:
                    return path + [{"via": rel.kind.value, **nb.to_summary()}]
                if nb.uid not in visited:
                    visited.add(nb.uid)
                    queue.append((nb.uid, path + [{"via": rel.kind.value, **nb.to_summary()}]))
        return None

    # ── Aggregates ────────────────────────────────────────────────────────

    def graph_statistics(self) -> dict:
        """High-level statistics for the business graph."""
        biz_stats = self.repo.get_stats()
        return {"business_graph": biz_stats}

    def entity_history(self, uid: str) -> dict:
        """Full audit trail for an entity."""
        entity = self.repo.get(uid)
        if not entity:
            return {"error": f"Not found: {uid}"}
        history = self.repo.entity_history(uid)
        return {
            "uid": uid,
            "current": entity.to_full(),
            "history": history,
            "versions": len(history),
        }

    def list_by_kind(self, kind: str, limit: int = 50, offset: int = 0) -> dict:
        """Paginated list of all entities of a given kind."""
        try:
            bk = BizEntityKind(kind)
        except ValueError:
            return {"error": f"Unknown entity kind: {kind}"}
        entities = self.repo.find(kind=bk, limit=limit, offset=offset)
        return {
            "kind": kind,
            "count": len(entities),
            "offset": offset,
            "results": [e.to_summary() for e in entities],
        }
