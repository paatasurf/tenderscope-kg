"""
Repository contract tests.

Every test in this module runs against ALL backends via the parametrized
'repo' fixture defined in conftest.py.

These tests define correct behavior.  Both BizRepositorySQLite and
FakeBizRepository must pass every assertion.  When BizRepositoryPG is
implemented it must also pass the same suite without modification.

INTENTIONAL DIVERGENCES (documented, not tested for equality):
- search_fts() result ordering: FTS5 and tsvector rank differently.
  Tests assert on set membership only, never on order.
- get_stats() may include backend-specific extra keys (e.g. "sequences").
  Tests only assert on the required keys (entities, relations, by_kind).
"""
from __future__ import annotations

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind, canonicalize


# ── UID contract ──────────────────────────────────────────────────────────────

class TestUIDContract:

    def test_put_entity_uid_format(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        prefix, number = e.uid.split("-")
        assert prefix == "CMP"
        assert len(number) == 8
        assert number.isdigit()

    def test_uid_monotonically_increases(self, repo):
        e1, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
        e2, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
        n1 = int(e1.uid.split("-")[1])
        n2 = int(e2.uid.split("-")[1])
        assert n2 > n1

    def test_uid_per_kind_independent(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge Rehab 2025")
        assert c.uid.startswith("CMP-")
        assert t.uid.startswith("TEN-")
        assert int(c.uid.split("-")[1]) == 1
        assert int(t.uid.split("-")[1]) == 1

    def test_uid_stable_on_update(self, repo):
        e1, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        e2, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp", attributes={"phone": "604"})
        assert e1.uid == e2.uid


# ── Entity create / dedup / update contract ───────────────────────────────────

class TestEntityContract:

    def test_created_flag_first_insert(self, repo):
        _, created = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        assert created is True

    def test_created_flag_second_insert(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        _, created = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        assert created is False

    def test_dedup_by_canonical_name(self, repo):
        e1, c1 = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        e2, c2 = repo.put_entity(BizEntityKind.COMPANY, "ACME CORP")
        assert c1 is True
        assert c2 is False
        assert e1.uid == e2.uid

    def test_dedup_different_kinds_not_same(self, repo):
        e1, _ = repo.put_entity(BizEntityKind.COMPANY, "Riverside")
        e2, _ = repo.put_entity(BizEntityKind.CITY, "Riverside")
        assert e1.uid != e2.uid

    def test_canonical_name_stored(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "  Pacific   RIM  ")
        assert e.canonical_name == canonicalize("  Pacific   RIM  ")
        assert e.canonical_name == "pacific rim"

    def test_attributes_merged_on_update(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"city": "Vancouver"})
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"phone": "604"})
        assert e.attributes.get("city") == "Vancouver"
        assert e.attributes.get("phone") == "604"

    def test_incoming_attribute_wins_on_conflict(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"city": "Vancouver"})
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", attributes={"city": "Victoria"})
        assert e.attributes["city"] == "Victoria"

    def test_confidence_raised_on_update(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme", confidence=0.5)
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", confidence=0.9)
        assert e.confidence == 0.9

    def test_confidence_not_lowered_on_update(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme", confidence=0.9)
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", confidence=0.3)
        assert e.confidence == 0.9

    def test_name_updated_on_upsert(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "acme corp")
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "ACME Corp")
        assert e.name == "ACME Corp"

    def test_get_by_uid(self, repo):
        e, _ = repo.put_entity(BizEntityKind.TENDER, "Park Renovation 2025")
        fetched = repo.get(e.uid)
        assert fetched is not None
        assert fetched.uid == e.uid
        assert fetched.kind == BizEntityKind.TENDER

    def test_get_missing_uid_returns_none(self, repo):
        assert repo.get("CMP-99999999") is None

    def test_find_by_canonical_exact(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        found = repo.find_by_canonical(BizEntityKind.COMPANY, "acme corp")
        assert found is not None
        assert found.uid == e.uid

    def test_find_by_canonical_wrong_kind_returns_none(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        found = repo.find_by_canonical(BizEntityKind.TENDER, "acme corp")
        assert found is None

    def test_find_by_kind(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme Corp")
        repo.put_entity(BizEntityKind.COMPANY, "BuildCo")
        repo.put_entity(BizEntityKind.TENDER, "Road Repair")
        companies = repo.find(kind=BizEntityKind.COMPANY)
        tenders = repo.find(kind=BizEntityKind.TENDER)
        assert len(companies) == 2
        assert len(tenders) == 1

    def test_find_name_like_substring(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.put_entity(BizEntityKind.COMPANY, "Atlantic Dredging")
        hits = repo.find(name_like="Pacific")
        assert len(hits) == 1
        assert hits[0].name == "Pacific Rim Construction"

    def test_find_name_like_case_insensitive(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        hits = repo.find(name_like="PACIFIC")
        assert len(hits) == 1

    def test_find_no_filter_returns_all(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme")
        repo.put_entity(BizEntityKind.TENDER, "Bridge")
        all_entities = repo.find()
        assert len(all_entities) >= 2

    def test_find_pagination(self, repo):
        for i in range(5):
            repo.put_entity(BizEntityKind.COMPANY, f"Company {i:02d}")
        page1 = repo.find(limit=3, offset=0)
        page2 = repo.find(limit=3, offset=3)
        assert len(page1) == 3
        assert len(page2) == 2
        uids_p1 = {e.uid for e in page1}
        uids_p2 = {e.uid for e in page2}
        assert uids_p1.isdisjoint(uids_p2)


# ── Relation contract ─────────────────────────────────────────────────────────

class TestRelationContract:

    def test_put_relation_created_flag(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        _, created = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        assert created is True

    def test_put_relation_dedup(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        _, c1 = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        _, c2 = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        assert c1 is True
        assert c2 is False

    def test_put_relation_idempotent_id(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        r1, _ = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        r2, _ = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        assert r1.id == r2.id

    def test_relation_confidence_raised(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid, confidence=0.5)
        r, _ = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid, confidence=0.9)
        assert r.confidence == 0.9

    def test_relation_confidence_not_lowered(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid, confidence=0.9)
        r, _ = repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid, confidence=0.1)
        assert r.confidence == 0.9

    def test_get_neighbors_out(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
        repo.put_relation(c.uid, BizRelationKind.IN_CITY, city.uid)
        neighbors = repo.get_neighbors(c.uid, direction="out")
        assert len(neighbors) == 1
        rel, nb = neighbors[0]
        assert rel.kind == BizRelationKind.IN_CITY
        assert nb.uid == city.uid

    def test_get_neighbors_in(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
        repo.put_relation(c.uid, BizRelationKind.IN_CITY, city.uid)
        neighbors = repo.get_neighbors(city.uid, direction="in")
        assert len(neighbors) == 1
        _, nb = neighbors[0]
        assert nb.uid == c.uid

    def test_get_neighbors_both(self, repo):
        a, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
        b, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
        repo.put_relation(a.uid, BizRelationKind.WORKS_WITH, b.uid)
        out = repo.get_neighbors(a.uid, direction="out")
        assert any(nb.uid == b.uid for _, nb in out)

    def test_get_neighbors_kind_filter(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
        prov, _ = repo.put_entity(BizEntityKind.PROVINCE, "BC")
        repo.put_relation(c.uid, BizRelationKind.IN_CITY, city.uid)
        repo.put_relation(c.uid, BizRelationKind.IN_PROVINCE, prov.uid)
        city_only = repo.get_neighbors(c.uid, direction="out", kinds=[BizRelationKind.IN_CITY])
        assert len(city_only) == 1
        assert city_only[0][1].uid == city.uid

    def test_get_neighbors_active_only(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        city, _ = repo.put_entity(BizEntityKind.CITY, "Vancouver")
        repo.put_relation(c.uid, BizRelationKind.IN_CITY, city.uid,
                          valid_to="2020-01-01T00:00:00+00:00")
        active = repo.get_neighbors(c.uid, direction="out", active_only=True)
        assert len(active) == 0

    def test_get_relations_between(self, repo):
        a, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
        b, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
        repo.put_relation(a.uid, BizRelationKind.WORKS_WITH, b.uid)
        rels = repo.get_relations_between(a.uid, b.uid)
        assert len(rels) == 1
        assert rels[0].kind == BizRelationKind.WORKS_WITH

    def test_get_relations_between_directional(self, repo):
        a, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
        b, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
        repo.put_relation(a.uid, BizRelationKind.WORKS_WITH, b.uid)
        assert repo.get_relations_between(b.uid, a.uid) == []

    def test_get_relations_between_empty(self, repo):
        a, _ = repo.put_entity(BizEntityKind.COMPANY, "Alpha")
        b, _ = repo.put_entity(BizEntityKind.COMPANY, "Beta")
        assert repo.get_relations_between(a.uid, b.uid) == []


# ── History contract ──────────────────────────────────────────────────────────

class TestHistoryContract:

    def test_history_written_on_create(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=True)
        history = repo.entity_history(e.uid)
        assert len(history) == 1
        assert history[0]["snapshot"]["uid"] == e.uid

    def test_history_appended_on_update(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=True)
        repo.put_entity(BizEntityKind.COMPANY, "Acme",
                        attributes={"city": "Victoria"}, write_history=True)
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=False)
        history = repo.entity_history(e.uid)
        assert len(history) == 2

    def test_history_oldest_first(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme",
                        attributes={"v": "1"}, write_history=True)
        repo.put_entity(BizEntityKind.COMPANY, "Acme",
                        attributes={"v": "2"}, write_history=True)
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=False)
        history = repo.entity_history(e.uid)
        assert history[0]["changed_at"] <= history[1]["changed_at"]

    def test_history_no_write_when_false(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=False)
        assert repo.entity_history(e.uid) == []

    def test_history_missing_uid_returns_empty(self, repo):
        assert repo.entity_history("CMP-99999999") == []

    def test_history_snapshot_has_required_keys(self, repo):
        e, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme", write_history=True)
        snap = repo.entity_history(e.uid)[0]["snapshot"]
        for key in ("uid", "kind", "name", "canonical_name", "attributes"):
            assert key in snap


# ── FTS contract ──────────────────────────────────────────────────────────────

class TestFTSContract:

    def test_empty_query_returns_empty(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.rebuild_fts()
        assert repo.search_fts("") == []

    def test_whitespace_query_returns_empty(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.rebuild_fts()
        assert repo.search_fts("   ") == []

    def test_matching_entity_in_results(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.put_entity(BizEntityKind.COMPANY, "Atlantic Dredging")
        repo.rebuild_fts()
        results = repo.search_fts("Pacific")
        names = {r.name for r in results}
        assert "Pacific Rim Construction" in names

    def test_non_matching_entity_excluded(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.put_entity(BizEntityKind.COMPANY, "Atlantic Dredging")
        repo.rebuild_fts()
        results = repo.search_fts("Atlantic")
        names = {r.name for r in results}
        assert "Atlantic Dredging" in names
        assert "Pacific Rim Construction" not in names

    def test_limit_respected(self, repo):
        for i in range(10):
            repo.put_entity(BizEntityKind.COMPANY, f"River {i:02d} Corp")
        repo.rebuild_fts()
        results = repo.search_fts("River", limit=3)
        assert len(results) <= 3

    def test_rebuild_fts_is_idempotent(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Pacific Rim Construction")
        repo.rebuild_fts()
        repo.rebuild_fts()
        results = repo.search_fts("Pacific")
        assert len(results) >= 1


# ── Stats contract ────────────────────────────────────────────────────────────

class TestStatsContract:

    def test_stats_required_keys(self, repo):
        stats = repo.get_stats()
        assert "entities" in stats
        assert "relations" in stats
        assert "by_kind" in stats

    def test_stats_entity_count(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme")
        repo.put_entity(BizEntityKind.TENDER, "Bridge")
        stats = repo.get_stats()
        assert stats["entities"] == 2

    def test_stats_relation_count(self, repo):
        c, _ = repo.put_entity(BizEntityKind.COMPANY, "Acme")
        t, _ = repo.put_entity(BizEntityKind.TENDER, "Bridge")
        repo.put_relation(c.uid, BizRelationKind.SUBMITTED_BID, t.uid)
        stats = repo.get_stats()
        assert stats["relations"] == 1

    def test_stats_by_kind(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme")
        repo.put_entity(BizEntityKind.COMPANY, "BuildCo")
        repo.put_entity(BizEntityKind.TENDER, "Bridge")
        stats = repo.get_stats()
        assert stats["by_kind"]["company"] == 2
        assert stats["by_kind"]["tender"] == 1

    def test_stats_dedup_does_not_double_count(self, repo):
        repo.put_entity(BizEntityKind.COMPANY, "Acme")
        repo.put_entity(BizEntityKind.COMPANY, "ACME")
        stats = repo.get_stats()
        assert stats["entities"] == 1


# ── Transaction contract ──────────────────────────────────────────────────────

class TestTransactionContract:

    def test_transaction_commits_on_success(self, repo):
        with repo.transaction():
            repo.put_entity(BizEntityKind.COMPANY, "Acme")
        assert repo.get_stats()["entities"] == 1

    def test_transaction_rolls_back_on_exception(self, repo):
        try:
            with repo.transaction():
                repo.put_entity(BizEntityKind.COMPANY, "Alpha")
                raise ValueError("forced failure")
        except ValueError:
            pass
        stats = repo.get_stats()
        assert stats["entities"] == 0

    def test_transaction_does_not_expose_backend_objects(self, repo):
        with repo.transaction() as ctx:
            assert ctx is None or not hasattr(ctx, "execute"), (
                "transaction() must not yield a raw connection/cursor/session"
            )


# ── Bulk operations contract ──────────────────────────────────────────────────

class TestBulkContract:

    def test_bulk_put_returns_counts(self, repo):
        records = [
            {"kind": "company", "name": "Acme"},
            {"kind": "company", "name": "BuildCo"},
            {"kind": "tender",  "name": "Bridge Rehab"},
        ]
        created, updated = repo.bulk_put_entities(records)
        assert created == 3
        assert updated == 0

    def test_bulk_put_dedup_counts_update(self, repo):
        records = [{"kind": "company", "name": "Acme"}]
        repo.bulk_put_entities(records)
        created, updated = repo.bulk_put_entities(records)
        assert created == 0
        assert updated == 1

    def test_bulk_put_entities_accessible(self, repo):
        records = [
            {"kind": "company", "name": "Pacific Rim"},
            {"kind": "tender",  "name": "Highway 1 Upgrade"},
        ]
        repo.bulk_put_entities(records, source="test")
        companies = repo.find(kind=BizEntityKind.COMPANY)
        assert len(companies) == 1
        assert companies[0].name == "Pacific Rim"
