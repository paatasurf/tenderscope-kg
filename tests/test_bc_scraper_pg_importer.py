"""BCScraperPGImporter identity-boundary regression tests."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from tenderscope_kg.domain import BizEntityKind, BizRelationKind
from tenderscope_kg.importers.bc_scraper_pg_importer import BCScraperPGImporter
from tests.fakes.repository import FakeBizRepository


@pytest.fixture
def repo() -> FakeBizRepository:
    return FakeBizRepository()


@pytest.fixture
def importer(repo: FakeBizRepository) -> BCScraperPGImporter:
    return BCScraperPGImporter(repo, conn=MagicMock())


def test_lookup_company_uid_readonly_finds_existing_company(
    importer: BCScraperPGImporter, repo: FakeBizRepository
) -> None:
    company, _ = repo.put_entity(BizEntityKind.COMPANY, "Ledcor Group Ltd.")
    assert importer._lookup_company_uid_readonly("Ledcor Group Ltd.") == company.uid


def test_lookup_company_uid_readonly_resolves_alias_to_canonical(
    importer: BCScraperPGImporter, repo: FakeBizRepository
) -> None:
    canonical, _ = repo.put_entity(BizEntityKind.COMPANY, "Ledcor Construction")
    alias, _ = repo.put_entity(BizEntityKind.COMPANY_ALIAS, "Ledcor DBA Name")
    repo.put_relation(alias.uid, BizRelationKind.ALIAS_OF, canonical.uid)
    assert importer._lookup_company_uid_readonly("Ledcor DBA Name") == canonical.uid


def test_lookup_company_uid_readonly_returns_none_for_unknown_name(
    importer: BCScraperPGImporter,
) -> None:
    assert importer._lookup_company_uid_readonly("Totally Unknown Contractor Ltd.") is None


def test_lookup_company_uid_readonly_never_calls_resolve_company_uid(
    importer: BCScraperPGImporter, repo: FakeBizRepository
) -> None:
    repo.resolve_company_uid = MagicMock(  # type: ignore[method-assign]
        side_effect=AssertionError("CREATE path must not run")
    )
    assert importer._lookup_company_uid_readonly("Unknown Name Inc.") is None
    repo.resolve_company_uid.assert_not_called()


def test_official_importer_module_has_no_resolve_company_uid_calls() -> None:
    import inspect

    from tenderscope_kg.importers import bc_scraper_pg_importer as mod

    source = inspect.getsource(mod)
    assert "resolve_company_uid" not in source


class _RowsCursor:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = list(rows)
        self._idx = 0

    def execute(self, _query: str) -> None:
        self._idx = 0

    def fetchmany(self, size: int) -> list[tuple]:
        batch = self._rows[self._idx : self._idx + size]
        self._idx += size
        return batch

    def close(self) -> None:
        return None


class _RowsConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self) -> _RowsCursor:
        return _RowsCursor(self._rows)


def _company_row(
    db_id: int,
    display_name: str,
    *,
    entity_role: str = "canonical",
    canonical_company_id: int | None = None,
) -> tuple:
    return (
        db_id,
        display_name,
        display_name,
        entity_role,
        canonical_company_id,
        None,
        None,
        None,
        None,
        "",
        "",
        "",
        "",
        "",
        "",
    )


def test_company_import_uses_scraper_id_not_name_as_identity_key(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection(
            [
                _company_row(101, "Same Name Construction"),
                _company_row(202, "Same Name Construction"),
            ]
        ),
    )

    result = importer._import_companies()

    companies = repo.find(kind=BizEntityKind.COMPANY)
    assert result.errors == []
    assert result.entities_created == 2
    assert len(companies) == 2
    assert {c.attributes["scraper_id"] for c in companies} == {101, 202}
    assert len({c.uid for c in companies}) == 2
    assert {c.canonical_name for c in companies} == {"same name construction"}


def test_company_import_preserves_uid_when_scraper_company_name_changes(repo: FakeBizRepository) -> None:
    first = BCScraperPGImporter(repo, conn=_RowsConnection([_company_row(101, "Old Name Ltd.")]))
    first_result = first._import_companies()
    original = repo.find_by_attribute("scraper_id", 101, kind=BizEntityKind.COMPANY, limit=1)[0]

    second = BCScraperPGImporter(repo, conn=_RowsConnection([_company_row(101, "New Name Ltd.")]))
    second_result = second._import_companies()
    updated = repo.find_by_attribute("scraper_id", 101, kind=BizEntityKind.COMPANY, limit=1)[0]

    assert first_result.entities_created == 1
    assert second_result.entities_created == 0
    assert second_result.entities_updated == 1
    assert updated.uid == original.uid
    assert updated.name == "New Name Ltd."
    assert updated.canonical_name == "new name ltd."


def test_probable_person_does_not_create_company_node(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection([_company_row(301, "John Smith", entity_role="probable_person")]),
    )

    result = importer._import_companies()

    assert repo.find(kind=BizEntityKind.COMPANY) == []
    assert result.entities_created == 0
    assert any("probable_person" in warning for warning in result.warnings)
    assert 301 not in importer._company_id_to_uid


def test_standalone_projects_as_company(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection([_company_row(401, "Local Builder Inc.", entity_role="standalone")]),
    )

    result = importer._import_companies()

    companies = repo.find(kind=BizEntityKind.COMPANY)
    assert result.errors == []
    assert result.entities_created == 1
    assert len(companies) == 1
    assert companies[0].attributes["entity_role"] == "standalone"
    assert companies[0].attributes["scraper_id"] == 401


def test_empty_entity_role_projects_as_standalone(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection([_company_row(501, "Legacy Row Ltd.", entity_role="")]),
    )

    result = importer._import_companies()

    companies = repo.find(kind=BizEntityKind.COMPANY)
    assert result.entities_created == 1
    assert len(companies) == 1
    assert companies[0].attributes["scraper_id"] == 501
    assert "entity_role" not in companies[0].attributes


def test_unsupported_entity_role_skipped_with_warning(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection([_company_row(601, "Mystery Row", entity_role="legacy_bucket")]),
    )

    result = importer._import_companies()

    assert repo.find(kind=BizEntityKind.COMPANY) == []
    assert result.entities_created == 0
    assert any("unsupported entity_role" in warning for warning in result.warnings)


def test_applicant_alias_projects_as_company_alias_not_company(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_RowsConnection(
            [
                _company_row(701, "Canonical Co", entity_role="canonical"),
                _company_row(702, "DBA Alias Name", entity_role="applicant_alias", canonical_company_id=701),
            ]
        ),
    )

    result = importer._import_companies()

    companies = repo.find(kind=BizEntityKind.COMPANY)
    aliases = repo.find(kind=BizEntityKind.COMPANY_ALIAS)
    assert result.errors == []
    assert len(companies) == 1
    assert len(aliases) == 1
    assert companies[0].attributes["scraper_id"] == 701
    assert aliases[0].attributes["entity_role"] == "applicant_alias"
    assert importer._company_id_to_uid[702] == companies[0].uid


def test_entity_role_dispatch_is_explicit_no_implicit_fallthrough() -> None:
    import inspect

    source = inspect.getsource(BCScraperPGImporter._import_companies)

    assert "_ENTITY_ROLE_PROBABLE_PERSON" in source
    assert "_COMPANY_PROJECTABLE_ROLES" in source
    assert "_normalize_sor_entity_role(entity_role)" in source
    assert 'if _s(entity_role) == "applicant_alias"' not in source


def _permit_row(
    db_id: int,
    external_id: str = "",
    *,
    company_id: int | None = None,
) -> tuple:
    return (
        db_id,
        external_id,
        "",  # address
        "",  # city
        "",  # permit_type
        None,  # project_value
        "",  # applicant
        "",  # contractor
        "",  # lifecycle_status
        "",  # source
        company_id,
    )


class _OrderedBatchCursor:
    """Simulates `WHERE id > %s ORDER BY id LIMIT %s` over an in-memory row list.

    Generic over row shape — used by both permits and contract_awards batch
    tests, since _fetch_ordered_batch() is the same shared pagination
    primitive for both.
    """

    def __init__(self, rows: list[tuple]) -> None:
        self._all_rows = sorted(rows, key=lambda r: r[0])
        self._result: list[tuple] = []

    def execute(self, _query: str, params: tuple | None = None) -> None:
        after_id, fetch_limit = params
        self._result = [r for r in self._all_rows if r[0] > after_id][:fetch_limit]

    def fetchall(self) -> list[tuple]:
        return self._result

    def close(self) -> None:
        return None


class _OrderedBatchConnection:
    def __init__(self, rows: list[tuple]) -> None:
        self._rows = rows

    def cursor(self) -> _OrderedBatchCursor:
        return _OrderedBatchCursor(self._rows)


def test_permits_batch_reports_has_more_when_rows_remain(repo: FakeBizRepository) -> None:
    rows = [_permit_row(i) for i in range(1, 11)]  # ids 1..10
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    importer._company_id_to_uid = {}

    result, last_id, has_more = importer._import_permits_batch(after_id=0, limit=5)

    assert result.errors == []
    assert result.entities_created == 5
    assert last_id == 5
    assert has_more is True


def test_permits_batch_final_page_has_more_false(repo: FakeBizRepository) -> None:
    rows = [_permit_row(i) for i in range(1, 11)]  # ids 1..10
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    importer._company_id_to_uid = {}

    result, last_id, has_more = importer._import_permits_batch(after_id=5, limit=5)

    assert result.entities_created == 5
    assert last_id == 10
    assert has_more is False


def test_permits_batch_empty_tail_returns_after_id_unchanged(repo: FakeBizRepository) -> None:
    rows = [_permit_row(i) for i in range(1, 4)]  # ids 1..3
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    importer._company_id_to_uid = {}

    result, last_id, has_more = importer._import_permits_batch(after_id=3, limit=5)

    assert result.entities_created == 0
    assert last_id == 3
    assert has_more is False


def test_permits_batch_resumes_correctly_across_two_calls(repo: FakeBizRepository) -> None:
    rows = [_permit_row(i) for i in range(1, 8)]  # ids 1..7
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    importer._company_id_to_uid = {}

    first_result, after_id, has_more = importer._import_permits_batch(after_id=0, limit=4)
    assert has_more is True
    second_result, after_id, has_more = importer._import_permits_batch(after_id=after_id, limit=4)
    assert has_more is False

    from tenderscope_kg.domain import BizEntityKind

    all_permits = repo.find(kind=BizEntityKind.PERMIT, limit=100)
    assert first_result.entities_created + second_result.entities_created == 7
    assert len(all_permits) == 7


def test_permits_batch_attaches_has_permit_relation_like_full_import(repo: FakeBizRepository) -> None:
    company, _ = repo.put_entity(BizEntityKind.COMPANY, "Batch Builder Ltd.")
    importer = BCScraperPGImporter(
        repo, conn=_OrderedBatchConnection([_permit_row(1, "PMT-1", company_id=42)])
    )
    importer._company_id_to_uid = {42: company.uid}

    result, _last_id, _has_more = importer._import_permits_batch(after_id=0, limit=10)

    assert result.relations_created == 1
    neighbours = repo.get_neighbors(company.uid, kinds=[BizRelationKind.HAS_PERMIT])
    assert len(neighbours) == 1


def test_permits_batch_resolves_company_id_via_graph_lookup_without_prior_import(
    repo: FakeBizRepository,
) -> None:
    """A fresh importer instance (no prior _import_companies() call) must
    still attach HAS_PERMIT correctly, by resolving company_id directly
    against the graph — this is what lets the batch endpoint skip re-running
    the full companies import on every call."""
    company, _ = repo.put_entity(
        BizEntityKind.COMPANY, "Direct Lookup Co.", attributes={"scraper_id": 77}
    )
    importer = BCScraperPGImporter(
        repo, conn=_OrderedBatchConnection([_permit_row(1, "PMT-2", company_id=77)])
    )

    result, _last_id, _has_more = importer._import_permits_batch(after_id=0, limit=10)

    assert result.relations_created == 1
    assert importer._company_id_to_uid[77] == company.uid
    neighbours = repo.get_neighbors(company.uid, kinds=[BizRelationKind.HAS_PERMIT])
    assert len(neighbours) == 1


def test_permits_batch_resolves_alias_company_id_to_canonical(repo: FakeBizRepository) -> None:
    """company_id may reference an applicant_alias row's own scraper id;
    resolution must follow ALIAS_OF to the canonical company, matching
    _import_companies()'s Pass 2 behavior."""
    canonical, _ = repo.put_entity(
        BizEntityKind.COMPANY, "Canonical Co.", attributes={"scraper_id": 1}
    )
    alias, _ = repo.put_entity(
        BizEntityKind.COMPANY_ALIAS, "Canonical Co. DBA", attributes={"scraper_id": 2}
    )
    repo.put_relation(alias.uid, BizRelationKind.ALIAS_OF, canonical.uid)

    importer = BCScraperPGImporter(
        repo, conn=_OrderedBatchConnection([_permit_row(1, "PMT-3", company_id=2)])
    )

    result, _last_id, _has_more = importer._import_permits_batch(after_id=0, limit=10)

    assert result.relations_created == 1
    assert importer._company_id_to_uid[2] == canonical.uid
    neighbours = repo.get_neighbors(canonical.uid, kinds=[BizRelationKind.HAS_PERMIT])
    assert len(neighbours) == 1


# ── contract_awards batching ────────────────────────────────────────────────


def _contract_award_row(
    db_id: int,
    title: str = "",
    *,
    external_id: str = "",
    winner_company: str = "",
    company_id: int | None = None,
) -> tuple:
    return (
        db_id,
        external_id,
        title,
        winner_company,
        None,  # award_value
        "",  # currency
        "",  # award_date
        "",  # buyer_organization
        "",  # procurement_category
        "",  # source
        company_id,
    )


def test_contract_awards_batch_reports_has_more_when_rows_remain(repo: FakeBizRepository) -> None:
    rows = [_contract_award_row(i, f"Award {i}") for i in range(1, 11)]  # ids 1..10
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))

    result, last_id, has_more = importer._import_contract_awards_batch(after_id=0, limit=5)

    assert result.errors == []
    assert result.entities_created == 5
    assert last_id == 5
    assert has_more is True


def test_contract_awards_batch_final_page_has_more_false(repo: FakeBizRepository) -> None:
    rows = [_contract_award_row(i, f"Award {i}") for i in range(1, 11)]  # ids 1..10
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))

    result, last_id, has_more = importer._import_contract_awards_batch(after_id=5, limit=5)

    assert result.entities_created == 5
    assert last_id == 10
    assert has_more is False


def test_contract_awards_batch_empty_tail_returns_after_id_unchanged(repo: FakeBizRepository) -> None:
    rows = [_contract_award_row(i, f"Award {i}") for i in range(1, 4)]  # ids 1..3
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))

    result, last_id, has_more = importer._import_contract_awards_batch(after_id=3, limit=5)

    assert result.entities_created == 0
    assert last_id == 3
    assert has_more is False


def test_contract_awards_batch_resumes_correctly_across_two_calls(repo: FakeBizRepository) -> None:
    rows = [_contract_award_row(i, f"Award {i}") for i in range(1, 8)]  # ids 1..7
    importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))

    first_result, after_id, has_more = importer._import_contract_awards_batch(after_id=0, limit=4)
    assert has_more is True
    second_result, after_id, has_more = importer._import_contract_awards_batch(
        after_id=after_id, limit=4
    )
    assert has_more is False

    all_contracts = repo.find(kind=BizEntityKind.CONTRACT, limit=100)
    assert first_result.entities_created + second_result.entities_created == 7
    assert len(all_contracts) == 7


def test_contract_awards_batch_attaches_company_relation_like_full_import(
    repo: FakeBizRepository,
) -> None:
    """Canonical company -> contract AWARDED_TO edge, resolved via the same
    per-batch graph lookup permits batching already uses (no companies
    re-import needed)."""
    company, _ = repo.put_entity(
        BizEntityKind.COMPANY, "Batch Contractor Ltd.", attributes={"scraper_id": 55}
    )
    importer = BCScraperPGImporter(
        repo,
        conn=_OrderedBatchConnection(
            [_contract_award_row(1, "Award A", company_id=55)]
        ),
    )

    result, _last_id, _has_more = importer._import_contract_awards_batch(after_id=0, limit=10)

    assert result.relations_created == 1
    assert importer._company_id_to_uid[55] == company.uid
    neighbours = repo.get_neighbors(company.uid, kinds=[BizRelationKind.AWARDED_TO])
    assert len(neighbours) == 1


def test_contract_awards_batch_resolves_winner_company_readonly(repo: FakeBizRepository) -> None:
    """contract -> winner_company edge attaches only via read-only lookup,
    matching WP1 (_lookup_company_uid_readonly) — batching must not bypass
    that identity boundary."""
    winner, _ = repo.put_entity(BizEntityKind.COMPANY, "Winner Co.")
    importer = BCScraperPGImporter(
        repo,
        conn=_OrderedBatchConnection(
            [_contract_award_row(1, "Award B", winner_company="Winner Co.")]
        ),
    )

    result, _last_id, _has_more = importer._import_contract_awards_batch(after_id=0, limit=10)

    assert result.errors == []
    neighbours = repo.get_neighbors(winner.uid, kinds=[BizRelationKind.AWARDED_TO])
    assert len(neighbours) == 1


def test_contract_awards_batch_warns_on_unresolved_winner_company(repo: FakeBizRepository) -> None:
    importer = BCScraperPGImporter(
        repo,
        conn=_OrderedBatchConnection(
            [_contract_award_row(1, "Award C", winner_company="Totally Unknown Ltd.")]
        ),
    )

    result, _last_id, _has_more = importer._import_contract_awards_batch(after_id=0, limit=10)

    assert result.errors == []
    assert any("unresolved winner_company" in w for w in result.warnings)


def test_contract_awards_batch_retry_is_idempotent(repo: FakeBizRepository) -> None:
    """A client that times out and retries the same after_id/limit must not
    create duplicate entities or relations — put_entity/put_relation upserts
    make this safe by construction, but this test guards the contract."""
    company, _ = repo.put_entity(
        BizEntityKind.COMPANY, "Retry Co.", attributes={"scraper_id": 9}
    )
    rows = [_contract_award_row(1, "Retry Award", company_id=9)]

    first_importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    first_result, first_last_id, _ = first_importer._import_contract_awards_batch(
        after_id=0, limit=10
    )

    # Simulate a retry: a fresh importer instance re-processes the identical
    # after_id/limit window (as a client would after a timeout with no
    # response received).
    retry_importer = BCScraperPGImporter(repo, conn=_OrderedBatchConnection(rows))
    retry_result, retry_last_id, _ = retry_importer._import_contract_awards_batch(
        after_id=0, limit=10
    )

    assert first_result.entities_created == 1
    assert retry_result.entities_created == 0
    assert retry_result.entities_updated == 1
    assert retry_result.relations_created == 0
    assert retry_result.relations_updated == 1
    assert retry_last_id == first_last_id
    assert len(repo.find(kind=BizEntityKind.CONTRACT, limit=100)) == 1
    neighbours = repo.get_neighbors(company.uid, kinds=[BizRelationKind.AWARDED_TO])
    assert len(neighbours) == 1


def test_full_import_contract_awards_still_works_after_batch_extraction(
    repo: FakeBizRepository,
) -> None:
    """Regression guard for the refactor: _import_contract_awards() (the
    unbatched path used by run()) must produce identical results now that
    its row-processing loop has been extracted into
    _process_contract_awards_batch() and is shared with the batched path."""
    company, _ = repo.put_entity(
        BizEntityKind.COMPANY, "Full Path Co.", attributes={"scraper_id": 3}
    )
    rows = [
        _contract_award_row(1, "Full Award 1", company_id=3),
        _contract_award_row(2, "Full Award 2", winner_company="Unknown Winner Ltd."),
    ]
    importer = BCScraperPGImporter(repo, conn=_RowsConnection(rows))
    importer._company_id_to_uid = {3: company.uid}

    result = importer._import_contract_awards()

    assert result.entities_created == 2
    assert result.relations_created == 1
    assert any("unresolved winner_company" in w for w in result.warnings)
    assert len(repo.find(kind=BizEntityKind.CONTRACT, limit=100)) == 2


def test_contract_awards_batch_and_full_path_share_row_processing_helper() -> None:
    """Both callers must dispatch through the same extracted method — the
    whole point of this refactor was one copy of the business logic, not two."""
    import inspect

    full_source = inspect.getsource(BCScraperPGImporter._import_contract_awards)
    batch_source = inspect.getsource(BCScraperPGImporter._import_contract_awards_batch)

    assert "_process_contract_awards_batch(" in full_source
    assert "_process_contract_awards_batch(" in batch_source
