"""
End-to-end smoke test for the PostgreSQL production path.

Usage:
    DATABASE_URL=postgres://... python scripts/smoke_test_pg.py

Exit code 0 = all checks passed.
Exit code 1 = one or more checks failed.

Smoke entities (__smoke__*) are left in the database; put_entity is
idempotent so re-runs are safe and will not create duplicates.
"""
from __future__ import annotations

import os
import sys
import traceback

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from tenderscope_kg.repository import open_repository
from tenderscope_kg.domain import BizEntityKind, BizRelationKind

PASSED: list[str] = []
FAILED: list[tuple[str, Exception]] = []


def check(name: str, fn):
    try:
        fn()
        PASSED.append(name)
        print(f"  OK   {name}")
    except Exception as exc:
        FAILED.append((name, exc))
        print(f"  FAIL {name}")
        traceback.print_exc()


def main() -> int:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        print("ERROR: DATABASE_URL is not set.")
        print("  export DATABASE_URL=postgres://user:pass@host/db")
        return 1

    redacted = url.split("@")[-1] if "@" in url else url
    print(f"\nTarget: ...@{redacted}")
    print("Running smoke checks...\n")

    # 1. open_repository selects PostgreSQL
    repo = None

    def step_open():
        nonlocal repo
        repo = open_repository()
        assert repo is not None

    check("open_repository() succeeds", step_open)
    if repo is None:
        print("\nABORTED: cannot open repository.")
        return 1

    # 2. correct backend class
    def step_backend():
        from tenderscope_kg.repository._postgres import BizRepositoryPG
        assert isinstance(repo, BizRepositoryPG), (
            f"Expected BizRepositoryPG, got {type(repo).__name__}"
        )

    check("backend is BizRepositoryPG", step_backend)

    # 3. schema present (get_stats works means tables exist)
    def step_stats_baseline():
        stats = repo.get_stats()
        assert isinstance(stats, dict)
        assert "entities" in stats, f"Missing 'entities' key: {stats}"
        print(f"       baseline: {stats['entities']} entities, "
              f"{stats.get('relations', '?')} relations")

    check("get_stats() succeeds (schema present)", step_stats_baseline)

    # 4. create entity
    SMOKE_COMPANY = "__smoke_test_company__"
    uid: dict = {}

    def step_create():
        entity, created = repo.put_entity(
            BizEntityKind.COMPANY,
            SMOKE_COMPANY,
            {"city": "Railway", "smoke": True},
            source="smoke_test",
        )
        assert entity.uid.startswith("CMP-"), f"Bad UID prefix: {entity.uid}"
        uid["company"] = entity.uid

    check("put_entity (create or upsert)", step_create)

    # 5. get by UID
    def step_get():
        entity = repo.get(uid["company"])
        assert entity is not None, "get() returned None"
        assert entity.name == SMOKE_COMPANY

    check("get(uid) round-trip", step_get)

    # 6. idempotency
    def step_idempotent():
        entity2, created2 = repo.put_entity(
            BizEntityKind.COMPANY,
            SMOKE_COMPANY,
            {"city": "Railway", "smoke": True, "run2": True},
            source="smoke_test",
        )
        assert entity2.uid == uid["company"], (
            f"UID changed on second put: {entity2.uid} != {uid['company']}"
        )
        assert not created2, "Second put_entity should return created=False"

    check("put_entity idempotency (stable UID, created=False)", step_idempotent)

    # 7. find_by_canonical
    def step_canonical():
        entity = repo.find_by_canonical(BizEntityKind.COMPANY, SMOKE_COMPANY.lower())
        assert entity is not None, "find_by_canonical returned None"
        assert entity.uid == uid["company"]

    check("find_by_canonical", step_canonical)

    # 8. find (listing with name_like)
    def step_find():
        results = repo.find(kind=BizEntityKind.COMPANY, name_like="smoke", limit=10)
        uids = [e.uid for e in results]
        assert uid["company"] in uids, f"find() missed smoke entity. Got: {uids}"

    check("find(kind, name_like)", step_find)

    # 9. FTS search
    def step_fts():
        results = repo.search_fts("smoke test company", limit=10)
        uids = [e.uid for e in results]
        assert uid["company"] in uids, (
            f"search_fts() missed smoke entity. Got: {uids}"
        )

    check("search_fts (full-text)", step_fts)

    # 10. create tender + relation
    SMOKE_TENDER = "__smoke_test_tender__"

    def step_relation():
        tender, _ = repo.put_entity(
            BizEntityKind.TENDER, SMOKE_TENDER, {"value": 100000},
            source="smoke_test",
        )
        uid["tender"] = tender.uid
        repo.put_relation(
            uid["company"], BizRelationKind.SUBMITTED_BID,
            tender.uid, source="smoke_test",
        )

    check("put_entity (tender) + put_relation", step_relation)

    # 11. get_neighbors reads relation back
    def step_neighbors():
        pairs = repo.get_neighbors(uid["company"], direction="out")
        kinds = [r.kind for r, _ in pairs]
        assert BizRelationKind.SUBMITTED_BID in kinds, (
            f"SUBMITTED_BID not found in neighbors: {kinds}"
        )

    check("get_neighbors (out) returns SUBMITTED_BID", step_neighbors)

    # 12. get_relations_between
    def step_between():
        rels = repo.get_relations_between(uid["company"], uid["tender"])
        assert len(rels) >= 1, f"Expected >= 1 relation, got {len(rels)}"
        assert rels[0].kind == BizRelationKind.SUBMITTED_BID

    check("get_relations_between", step_between)

    # 13. final stats
    def step_stats_final():
        stats = repo.get_stats()
        assert stats["entities"] >= 2, f"Expected >= 2 entities: {stats}"

    check("get_stats() reflects inserted entities", step_stats_final)

    # Summary
    total = len(PASSED) + len(FAILED)
    print(f"\n{'='*52}")
    print(f"  PASSED : {len(PASSED)}/{total}")
    print(f"  FAILED : {len(FAILED)}/{total}")
    if FAILED:
        print("\n  Failed checks:")
        for name, exc in FAILED:
            print(f"    * {name}: {exc}")
    print(f"{'='*52}\n")
    return 0 if not FAILED else 1


if __name__ == "__main__":
    sys.exit(main())
