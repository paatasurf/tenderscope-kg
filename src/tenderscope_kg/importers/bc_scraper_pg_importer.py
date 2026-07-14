"""
BC Scraper PostgreSQL Importer.

Reads directly from public.* tables in the shared Railway PostgreSQL database
(the bc-tender-scraper schema) and upserts into the graph.* schema via
BizRepository.  This importer is READ-ONLY with respect to public.*.

Tables consumed:
    public.companies        → BizEntityKind.COMPANY (canonical/standalone only)
                            → BizEntityKind.COMPANY_ALIAS (applicant_alias)
                            → skipped: probable_person, unsupported roles
    public.tenders          → BizEntityKind.TENDER
    public.commercial_tenders → BizEntityKind.TENDER (merged with tenders)
    public.arch_tenders     → BizEntityKind.TENDER (merged)
    public.permits          → BizEntityKind.PERMIT
    public.contract_awards  → BizEntityKind.CONTRACT + AWARDED_TO relation

Relations created:
    COMPANY  --AWARDED_TO-->    CONTRACT  (from contract_awards.company_id)
    COMPANY  --HAS_PERMIT-->    PERMIT    (from permits.company_id)
    TENDER   --ISSUED_BY-->     COMPANY   (from tenders.organization)
    CONTRACT --AWARDED_TO-->    COMPANY   (from contract_awards.winner_company
                                 when that name already exists in the graph;
                                 never creates new COMPANY nodes)

Source tag: "bc_scraper_pg"
"""

from __future__ import annotations

import logging
import time
from typing import Any, Optional

import psycopg2

from ..domain import BizEntityKind, BizRelationKind, IdentityEvidence
from ..domain.kinds import canonicalize
from ..domain.results import ImportResult
from ..repository._base import BizRepository
from .base import BaseImporter

logger = logging.getLogger(__name__)

_SOURCE = "bc_scraper_pg"

# Mirrors bc-tender-scraper/db/company_canonical_constants.py (SoR CHECK constraint).
_ENTITY_ROLE_CANONICAL = "canonical"
_ENTITY_ROLE_APPLICANT_ALIAS = "applicant_alias"
_ENTITY_ROLE_STANDALONE = "standalone"
_ENTITY_ROLE_PROBABLE_PERSON = "probable_person"
_SOR_ENTITY_ROLES = frozenset(
    {
        _ENTITY_ROLE_CANONICAL,
        _ENTITY_ROLE_APPLICANT_ALIAS,
        _ENTITY_ROLE_STANDALONE,
        _ENTITY_ROLE_PROBABLE_PERSON,
    }
)
_COMPANY_PROJECTABLE_ROLES = frozenset({_ENTITY_ROLE_CANONICAL, _ENTITY_ROLE_STANDALONE})


def _normalize_sor_entity_role(raw: Any) -> str:
    """Return a known SoR entity_role string.

    Empty/NULL uses the SoR column default (migration 014: standalone).
    """
    role = _s(raw)
    if not role:
        return _ENTITY_ROLE_STANDALONE
    return role


def _s(val: Any) -> str:
    """Coerce a DB value to a clean string; return '' for None/empty."""
    if val is None:
        return ""
    return str(val).strip()


def _f(val: Any) -> Optional[float]:
    """Coerce to float, None on failure."""
    try:
        return float(val) if val is not None else None
    except (TypeError, ValueError):
        return None


class BCScraperPGImporter(BaseImporter):
    """
    Import bc-tender-scraper public.* tables into graph.* via BizRepository.

    Args:
        repo:           Target graph repository (must be initialised).
        conn:           psycopg2 connection OR a DSN string for the shared
                        Railway DB.  When a DSN string is provided (preferred),
                        each import stage opens and closes its own fresh
                        connection, preventing Railway proxy SSL timeouts on
                        long-running migrations.  When a live connection object
                        is provided (legacy / test usage), it is used directly
                        as before — no per-stage reconnection.
        batch_size:     Rows fetched per cursor iteration (memory control).
        uid_snapshot:   Optional mapping of (kind_str, canonical_name_str) ->
                        uid_str captured before a re-import truncation.  When
                        provided, put_entity() receives the original uid= for
                        every entity whose key appears in the snapshot,
                        guaranteeing UID stability across migrations.  Pass
                        None (the default) for a fresh import from scratch.
    """

    name = "bc_scraper_pg"

    def __init__(
        self,
        repo: BizRepository,
        conn: Any,
        batch_size: int = 500,
        uid_snapshot: Optional[dict[tuple[str, str], str]] = None,
    ) -> None:
        super().__init__(repo, source_tag=_SOURCE)
        # Accept either a DSN string (preferred — enables per-stage reconnect)
        # or a live psycopg2 connection (legacy / unit-test usage).
        if isinstance(conn, str):
            self._dsn: Optional[str] = conn
            self._conn: Any = None  # not used when DSN is set
        else:
            self._dsn = None
            self._conn = conn
        self._batch_size = batch_size
        self._uid_snapshot: dict[tuple[str, str], str] = uid_snapshot or {}

    def _lookup_company_uid_readonly(self, name: str) -> str | None:
        """Resolve a company name to an existing graph UID without creating entities.

        Used only for linking contract awards to companies already projected from
        public.companies.  Identity CREATE belongs to bc-tender-scraper Registry.
        """
        canon = canonicalize(name)
        existing = self.repo.find_by_canonical(BizEntityKind.COMPANY, canon)
        if existing is not None:
            return existing.uid

        alias = self.repo.find_by_canonical(BizEntityKind.COMPANY_ALIAS, canon)
        if alias is not None:
            resolved = self.repo.resolve_alias(alias.uid)
            if resolved is not None and resolved.kind == BizEntityKind.COMPANY:
                return resolved.uid
        return None

    def _get_source_conn(self) -> Any:
        """Open a fresh source connection.

        When a DSN string was supplied, a brand-new psycopg2 connection is
        created on every call.  The caller is responsible for closing it
        (always in a finally block).

        When a legacy connection object was supplied (unit tests, external
        callers), that object is returned as-is — closing is the caller's
        responsibility as before.
        """
        if self._dsn is not None:
            return psycopg2.connect(self._dsn)
        return self._conn

    def _close_source_conn(self, conn: Any) -> None:
        """Close a source connection only when it was opened by _get_source_conn.

        Legacy connection objects (self._dsn is None) are NOT closed here;
        their lifetime is managed externally.
        """
        if self._dsn is not None:
            try:
                conn.close()
            except Exception:
                pass

    # ── Public entry point ────────────────────────────────────────────────

    def run(self) -> ImportResult:
        result = self._make_result()
        t0 = time.perf_counter()

        try:
            verify = self._verify_access()
            if verify.get("error"):
                result.errors.append(verify["error"])
                return result
            result.warnings.append(
                f"Source table counts: companies={verify['companies']}, "
                f"tenders={verify['tenders']}, permits={verify['permits']}, "
                f"contract_awards={verify['contract_awards']}"
            )
        except Exception as exc:
            result.errors.append(f"Verify access failed: {exc}")
            return result

        # Steps that manage their own batched transactions internally.
        # Both now loop their *_batch() method to completion (see
        # _run_stage_to_completion), and each batch call provides its own
        # transaction boundary -- wrapping them again here would nest
        # transactions, which the repository contract documents as
        # implementation-defined.
        _self_transacting = {self._import_permits, self._import_contract_awards}

        steps = [
            self._import_companies,
            self._import_tenders,
            self._import_permits,
            self._import_contract_awards,
            self._import_organizations,
        ]
        for step in steps:
            try:
                if step in _self_transacting:
                    sub = step()
                else:
                    with self.repo.transaction():
                        sub = step()
                result.entities_created += sub.entities_created
                result.entities_updated += sub.entities_updated
                result.relations_created += sub.relations_created
                result.relations_updated += sub.relations_updated
                result.errors.extend(sub.errors)
                result.warnings.extend(sub.warnings)
            except Exception as exc:
                result.errors.append(f"{step.__name__} failed: {exc}")
                logger.exception("Import step %s failed", step.__name__)

        result.elapsed_s = time.perf_counter() - t0
        return result

    # ── Verification ──────────────────────────────────────────────────────

    def verify_access(self) -> dict:
        """Public method for the /api/verify endpoint."""
        return self._verify_access()

    def _verify_access(self) -> dict:
        """Return row counts from public.* tables; raise on connection failure."""
        conn = self._get_source_conn()
        try:
            cur = conn.cursor()
            try:
                counts: dict = {}
                for table in (
                    "companies",
                    "tenders",
                    "permits",
                    "contract_awards",
                    "commercial_tenders",
                    "arch_tenders",
                ):
                    try:
                        cur.execute(f"SELECT COUNT(*) FROM public.{table}")  # noqa: S608
                        counts[table] = cur.fetchone()[0]
                    except Exception as exc:
                        counts[table] = f"ERROR: {exc}"
                        conn.rollback()
                return counts
            finally:
                cur.close()
        finally:
            self._close_source_conn(conn)

    # ── Companies ─────────────────────────────────────────────────────────

    def _import_companies(self) -> ImportResult:
        """
        Two-pass import of public.companies — passive SoR projection only.

        Pass 1 — company-projectable rows (entity_role canonical or standalone):
            Inserted as BizEntityKind.COMPANY keyed by scraper_id.

        Pass 1 skip — probable_person:
            Not projected; SoR marks these as person rows, not companies.

        Pass 1 skip — unsupported entity_role values:
            Warned explicitly; never projected.

        Pass 2 — alias rows (entity_role = applicant_alias):
            Inserted as BizEntityKind.COMPANY_ALIAS.
            Each alias gets a single ALIAS_OF edge → its canonical COMPANY.
            Aliases are NOT primary company nodes and will not appear in
            company listings or be used as relation targets.

        After both passes, self._company_id_to_uid maps scraper ids for
        projected companies and aliases to a COMPANY uid.  Downstream steps
        (permits, contracts, tenders) attach only when the scraper id was
        projected; probable_person rows are intentionally absent.
        """
        result = ImportResult(importer=f"{self.name}:companies")

        _QUERY = """
            SELECT
                id,
                COALESCE(NULLIF(display_name, ''), name, '') AS display_name,
                name,
                entity_role,
                canonical_company_id,
                construction_score,
                total_projects,
                total_award_value,
                award_count,
                primary_city,
                primary_province,
                google_address,
                google_phone,
                primary_trade,
                dominant_sector
            FROM public.companies
            ORDER BY id
        """

        # ── shared attribute builder ───────────────────────────────────────
        def _build_attrs(
            db_id,
            entity_role,
            canonical_company_id,
            construction_score,
            total_projects,
            total_award_value,
            award_count,
            primary_city,
            primary_province,
            google_address,
            google_phone,
            primary_trade,
            dominant_sector,
        ) -> dict:
            attrs: dict = {"scraper_id": db_id}
            if _s(entity_role):
                attrs["entity_role"] = _s(entity_role)
            if canonical_company_id:
                attrs["canonical_company_id"] = canonical_company_id
            if construction_score is not None:
                attrs["construction_score"] = construction_score
            if total_projects:
                attrs["total_projects"] = total_projects
            if total_award_value:
                attrs["total_award_value"] = total_award_value
            if award_count:
                attrs["award_count"] = award_count
            for k, v in [
                ("city", primary_city),
                ("province", primary_province),
                ("address", google_address),
                ("phone", google_phone),
                ("primary_trade", primary_trade),
                ("dominant_sector", dominant_sector),
            ]:
                if _s(v):
                    attrs[k] = _s(v)
            return attrs

        # ── Pass 1: canonical companies ────────────────────────────────────
        # self._company_id_to_uid maps scraper id → canonical COMPANY uid.
        # Populated here for canonicals; updated in Pass 2 to point aliases
        # at the same canonical uid.
        self._company_id_to_uid: dict[int, str] = {}
        # Temporary: scraper canonical_id → graph uid (for alias resolution)
        _canonical_scraper_id_to_uid: dict[int, str] = {}

        conn = self._get_source_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(_QUERY)
                all_rows = []
                while True:
                    batch = cur.fetchmany(self._batch_size)
                    if not batch:
                        break
                    all_rows.extend(batch)
            finally:
                cur.close()
        finally:
            self._close_source_conn(conn)

        for row in all_rows:
            (
                db_id,
                display_name,
                raw_name,
                entity_role,
                canonical_company_id,
                construction_score,
                total_projects,
                total_award_value,
                award_count,
                primary_city,
                primary_province,
                google_address,
                google_phone,
                primary_trade,
                dominant_sector,
            ) = row

            role = _normalize_sor_entity_role(entity_role)

            if role == _ENTITY_ROLE_APPLICANT_ALIAS:
                continue  # handled in Pass 2

            if role == _ENTITY_ROLE_PROBABLE_PERSON:
                result.warnings.append(
                    f"companies.id={db_id}: entity_role=probable_person — "
                    "not projected as COMPANY (SoR person row)"
                )
                continue

            if role not in _COMPANY_PROJECTABLE_ROLES:
                if role in _SOR_ENTITY_ROLES:
                    result.warnings.append(
                        f"companies.id={db_id}: entity_role={role!r} is known in SoR "
                        "but has no graph COMPANY projection, skipping"
                    )
                else:
                    result.warnings.append(
                        f"companies.id={db_id}: unsupported entity_role={role!r}, skipping"
                    )
                continue

            name = _s(display_name) or _s(raw_name)
            if not name:
                result.warnings.append(f"companies.id={db_id}: empty name, skipping")
                continue

            attrs = _build_attrs(
                db_id,
                entity_role,
                canonical_company_id,
                construction_score,
                total_projects,
                total_award_value,
                award_count,
                primary_city,
                primary_province,
                google_address,
                google_phone,
                primary_trade,
                dominant_sector,
            )

            try:
                _preserved_uid = self._uid_snapshot.get((BizEntityKind.COMPANY.value, canonicalize(name)))
                entity, created = self.repo.put_entity(
                    kind=BizEntityKind.COMPANY,
                    name=name,
                    attributes=attrs,
                    source=_SOURCE,
                    write_history=False,
                    uid=_preserved_uid,
                )
                self._company_id_to_uid[db_id] = entity.uid
                _canonical_scraper_id_to_uid[db_id] = entity.uid
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1
            except Exception as exc:
                result.errors.append(f"companies.id={db_id} (company): {exc}")

        # ── Pass 2: alias companies ────────────────────────────────────────
        for row in all_rows:
            (
                db_id,
                display_name,
                raw_name,
                entity_role,
                canonical_company_id,
                construction_score,
                total_projects,
                total_award_value,
                award_count,
                primary_city,
                primary_province,
                google_address,
                google_phone,
                primary_trade,
                dominant_sector,
            ) = row

            if _s(entity_role) != "applicant_alias":
                continue

            name = _s(display_name) or _s(raw_name)
            if not name:
                result.warnings.append(f"companies.id={db_id} (alias): empty name, skipping")
                continue

            # Resolve which canonical COMPANY this alias points at.
            canonical_uid: str | None = None
            if canonical_company_id and canonical_company_id in _canonical_scraper_id_to_uid:
                canonical_uid = _canonical_scraper_id_to_uid[canonical_company_id]
            else:
                result.warnings.append(
                    f"companies.id={db_id} (alias): "
                    f"canonical_company_id={canonical_company_id} not found in graph, skipping"
                )
                continue

            attrs = _build_attrs(
                db_id,
                entity_role,
                canonical_company_id,
                construction_score,
                total_projects,
                total_award_value,
                award_count,
                primary_city,
                primary_province,
                google_address,
                google_phone,
                primary_trade,
                dominant_sector,
            )
            attrs["alias_for_uid"] = canonical_uid

            try:
                _preserved_alias_uid = self._uid_snapshot.get((BizEntityKind.COMPANY_ALIAS.value, canonicalize(name)))
                alias_entity, created = self.repo.put_entity(
                    kind=BizEntityKind.COMPANY_ALIAS,
                    name=name,
                    attributes=attrs,
                    source=_SOURCE,
                    write_history=False,
                    uid=_preserved_alias_uid,
                )
                # ALIAS_OF edge: alias → canonical COMPANY.
                # Carry a structured IdentityEvidence payload so every
                # alias match decision is auditable and reversible.
                _ev = IdentityEvidence(
                    confidence=1.0,
                    reason="canonical_id_match",
                    explanation=(
                        f"'{name}' is a registered alias of canonical "
                        f"company_id={canonical_company_id} "
                        f"(graph uid={canonical_uid}) "
                        f"per public.companies.canonical_company_id"
                    ),
                    evidence=[
                        {
                            "field": "canonical_company_id",
                            "value": canonical_company_id,
                            "source": _SOURCE,
                        }
                    ],
                    source=_SOURCE,
                )
                self.repo.put_relation(
                    source_uid=alias_entity.uid,
                    kind=BizRelationKind.ALIAS_OF,
                    target_uid=canonical_uid,
                    source=_SOURCE,
                    confidence=1.0,
                    attributes=_ev.to_dict(),
                )
                # Map alias scraper id → canonical uid so downstream steps
                # (permits, contracts, etc.) always attach to the canonical node.
                self._company_id_to_uid[db_id] = canonical_uid
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1
                result.relations_created += 1
            except Exception as exc:
                result.errors.append(f"companies.id={db_id} (alias): {exc}")

        logger.info(
            "companies: canonical=%d aliases=%d errors=%d",
            len(_canonical_scraper_id_to_uid),
            sum(
                1
                for v in self._company_id_to_uid.values()
                if v not in _canonical_scraper_id_to_uid.values()
                or len([k for k, u in self._company_id_to_uid.items() if u == v]) > 1
            ),
            len(result.errors),
        )
        return result

    # ── Tenders ───────────────────────────────────────────────────────────

    def _import_tenders(self) -> ImportResult:
        """Import tenders, commercial_tenders, arch_tenders → BizEntityKind.TENDER."""
        result = ImportResult(importer=f"{self.name}:tenders")
        self._tender_title_to_uid: dict[str, str] = {}

        queries = [
            (
                "tenders",
                """
                SELECT id, title, organization, category, closing_date,
                       estimated_value, source, tender_id, url
                FROM public.tenders
                ORDER BY id
            """,
            ),
            (
                "commercial_tenders",
                """
                SELECT id, title, company AS organization, category, deadline AS closing_date,
                       value AS estimated_value, source, tender_id, url
                FROM public.commercial_tenders
                ORDER BY id
            """,
            ),
            (
                "arch_tenders",
                """
                SELECT id, title, company AS organization, category, deadline AS closing_date,
                       value AS estimated_value, 'arch_tenders' AS source, tender_id, url
                FROM public.arch_tenders
                ORDER BY id
            """,
            ),
        ]

        conn = self._get_source_conn()
        try:
            cur = conn.cursor()
            try:
                for table, query in queries:
                    cur.execute(query)
                    while True:
                        rows = cur.fetchmany(self._batch_size)
                        if not rows:
                            break
                        for row in rows:
                            (
                                db_id,
                                title,
                                org,
                                category,
                                closing_date,
                                value,
                                src,
                                tender_id,
                                url,
                            ) = row

                            name = _s(title)
                            if not name:
                                result.warnings.append(f"{table}.id={db_id}: empty title, skipping")
                                continue

                            attrs: dict = {
                                "scraper_id": db_id,
                                "scraper_table": table,
                            }
                            for k, v in [
                                ("organization", org),
                                ("category", category),
                                ("closing_date", closing_date),
                                ("estimated_value", value),
                                ("source", src),
                                ("tender_id", tender_id),
                                ("url", url),
                            ]:
                                if _s(v):
                                    attrs[k] = _s(v)

                            try:
                                _preserved_uid = self._uid_snapshot.get(
                                    (BizEntityKind.TENDER.value, canonicalize(name))
                                )
                                entity, created = self.repo.put_entity(
                                    kind=BizEntityKind.TENDER,
                                    name=name,
                                    attributes=attrs,
                                    source=_SOURCE,
                                    write_history=False,
                                    uid=_preserved_uid,
                                )
                                self._tender_title_to_uid[f"{table}:{db_id}"] = entity.uid
                                if created:
                                    result.entities_created += 1
                                else:
                                    result.entities_updated += 1
                            except Exception as exc:
                                result.errors.append(f"{table}.id={db_id}: {exc}")
            finally:
                cur.close()
        finally:
            self._close_source_conn(conn)

        logger.info(
            "tenders: created=%d updated=%d errors=%d",
            result.entities_created,
            result.entities_updated,
            len(result.errors),
        )
        return result

    # ── Batch stage orchestration ───────────────────────────────────────────

    _DEFAULT_STAGE_BATCH_LIMIT = 5000

    def _run_stage_to_completion(self, batch_method, limit: int | None = None) -> ImportResult:
        """
        Loop a `*_batch(after_id, limit) -> (result, last_id, has_more)`
        method until has_more=False, merging every page's ImportResult into
        one aggregate.

        This is what lets the full (unbounded) importer methods
        (_import_permits, _import_contract_awards) reuse the exact same
        per-batch connection lifecycle and row-processing logic as the
        standalone REST batch endpoints, instead of maintaining a second,
        separate fetch-loop implementation per stage. As a side effect this
        also removes a latent reliability gap the old fetch-loops had: each
        one held a single Postgres connection open for the entire unbounded
        scan (permits: 111k+ rows), which is exactly the kind of long-lived
        connection the original SSL-timeout fix (per-stage reconnection) was
        meant to avoid -- just not at the sub-stage level. Looping the batch
        method means each page gets its own short-lived connection instead.
        """
        effective_limit = limit if limit is not None else self._DEFAULT_STAGE_BATCH_LIMIT
        aggregate = ImportResult(importer="stage")
        after_id = 0
        while True:
            sub, after_id, has_more = batch_method(after_id=after_id, limit=effective_limit)
            aggregate.entities_created += sub.entities_created
            aggregate.entities_updated += sub.entities_updated
            aggregate.relations_created += sub.relations_created
            aggregate.relations_updated += sub.relations_updated
            aggregate.errors.extend(sub.errors)
            aggregate.warnings.extend(sub.warnings)
            if not has_more:
                break
        return aggregate

    # ── Permits ───────────────────────────────────────────────────────────

    def _import_permits(self) -> ImportResult:
        """
        Import public.permits → BizEntityKind.PERMIT.
        Link company_id → permit via HAS_PERMIT relation where set.

        Reuses _import_permits_batch() via _run_stage_to_completion() --
        looping the same batch method POST /api/import/permits/batch calls,
        rather than a separate full-table fetch-loop implementation.
        """
        result = self._run_stage_to_completion(self._import_permits_batch)
        result.importer = f"{self.name}:permits"

        logger.info(
            "permits: created=%d updated=%d rels_created=%d errors=%d",
            result.entities_created,
            result.entities_updated,
            result.relations_created,
            len(result.errors),
        )
        return result

    def _fetch_ordered_batch(
        self, columns_sql: str, table: str, after_id: int, limit: int
    ) -> tuple[list, bool]:
        """
        Fetch up to `limit` rows from `public.{table}`, ordered by id, with
        id > after_id.  Shared pagination primitive for every batched-import
        endpoint (permits, contract_awards, ...) — opens and closes its own
        source connection via _get_source_conn()/_close_source_conn(), same
        per-call reconnection every other stage already uses.

        `columns_sql` and `table` are internal literals supplied by importer
        code only, never derived from request input — after_id/limit are the
        only caller-controlled values, and both are passed as query params.

        Returns (rows, has_more) where rows has at most `limit` entries and
        has_more is True iff more rows exist beyond this page.
        """
        conn = self._get_source_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute(
                    f"""
                    SELECT {columns_sql}
                    FROM public.{table}
                    WHERE id > %s
                    ORDER BY id
                    LIMIT %s
                    """,
                    (after_id, limit + 1),
                )
                rows = cur.fetchall()
            finally:
                cur.close()
        finally:
            self._close_source_conn(conn)

        has_more = len(rows) > limit
        return rows[:limit], has_more

    def _resolve_company_ids_for_batch(self, rows: list) -> None:
        """
        Populate self._company_id_to_uid for exactly the company_ids referenced
        in this batch of rows (last column), via direct graph lookups.

        Unlike _import_companies() (which walks and upserts all of
        public.companies), this only resolves the handful of scraper company
        ids actually referenced by the batch, using the existing
        find_by_attribute()/resolve_alias() read APIs — no re-import needed.
        Mirrors the same canonical-vs-alias resolution _import_companies()
        performs when it populates this same dict.
        """
        if not hasattr(self, "_company_id_to_uid"):
            self._company_id_to_uid: dict[int, str] = {}
        distinct_ids = {
            row[-1] for row in rows if row[-1] is not None and row[-1] not in self._company_id_to_uid
        }
        for cid in distinct_ids:
            canonical = self.repo.find_by_attribute("scraper_id", cid, kind=BizEntityKind.COMPANY, limit=1)
            if canonical:
                self._company_id_to_uid[cid] = canonical[0].uid
                continue
            alias = self.repo.find_by_attribute("scraper_id", cid, kind=BizEntityKind.COMPANY_ALIAS, limit=1)
            if alias:
                resolved = self.repo.resolve_alias(alias[0].uid)
                if resolved is not None:
                    self._company_id_to_uid[cid] = resolved.uid

    def _import_permits_batch(self, after_id: int = 0, limit: int = 5000) -> tuple[ImportResult, int, bool]:
        """
        Import one bounded slice of public.permits, ordered by id, for use
        behind an HTTP endpoint where the caller must bound request duration
        (e.g. Railway's public-edge timeout).  Row processing is identical to
        _import_permits() — this only adds pagination around the same
        _process_permit_batch() call.

        Resolves company_id → uid per-batch via _resolve_company_ids_for_batch()
        (direct graph lookups), so callers do NOT need to run
        _import_companies() first — that would re-upsert the entire
        public.companies table on every batch call, which is both unnecessary
        and, at production scale, itself slow enough to risk exceeding the
        same request timeout this batching exists to avoid.

        Returns (result, last_id, has_more):
            last_id:  highest permits.id processed in this batch (pass as the
                      next call's after_id to resume; unchanged from the
                      input after_id when the batch is empty).
            has_more: True if more rows exist beyond this batch.
        """
        result = ImportResult(importer=f"{self.name}:permits_batch")
        batch, has_more = self._fetch_ordered_batch(
            columns_sql="id, external_id, address, city, permit_type, "
            "project_value, applicant, contractor, lifecycle_status, "
            "source, company_id",
            table="permits",
            after_id=after_id,
            limit=limit,
        )
        last_id = batch[-1][0] if batch else after_id
        if batch:
            self._resolve_company_ids_for_batch(batch)
            self._process_permit_batch(batch, result)

        logger.info(
            "permits_batch: after_id=%d limit=%d processed=%d created=%d updated=%d has_more=%s",
            after_id,
            limit,
            len(batch),
            result.entities_created,
            result.entities_updated,
            has_more,
        )
        return result, last_id, has_more

    def _process_permit_batch(self, rows: list, result: ImportResult) -> None:
        """Process one batch of permit rows inside a single transaction."""
        with self.repo.transaction():
            for row in rows:
                (
                    db_id,
                    external_id,
                    address,
                    city,
                    permit_type,
                    project_value,
                    applicant,
                    contractor,
                    lifecycle_status,
                    src,
                    company_id,
                ) = row

                name = _s(external_id) or _s(address) or f"permit-{db_id}"
                attrs: dict = {"scraper_id": db_id}
                for k, v in [
                    ("external_id", external_id),
                    ("address", address),
                    ("city", city),
                    ("permit_type", permit_type),
                    ("project_value", project_value),
                    ("applicant", applicant),
                    ("contractor", contractor),
                    ("lifecycle_status", lifecycle_status),
                    ("source", src),
                ]:
                    if _s(v):
                        attrs[k] = _s(v)

                try:
                    _preserved_uid = self._uid_snapshot.get((BizEntityKind.PERMIT.value, canonicalize(name)))
                    permit_e, created = self.repo.put_entity(
                        kind=BizEntityKind.PERMIT,
                        name=name,
                        attributes=attrs,
                        source=_SOURCE,
                        write_history=False,
                        uid=_preserved_uid,
                    )
                    if created:
                        result.entities_created += 1
                    else:
                        result.entities_updated += 1

                    if company_id and company_id in self._company_id_to_uid:
                        company_uid = self._company_id_to_uid[company_id]
                        _, rc = self.repo.put_relation(
                            source_uid=company_uid,
                            kind=BizRelationKind.HAS_PERMIT,
                            target_uid=permit_e.uid,
                            source=_SOURCE,
                            confidence=1.0,
                        )
                        if rc:
                            result.relations_created += 1
                        else:
                            result.relations_updated += 1

                except Exception as exc:
                    result.errors.append(f"permits.id={db_id}: {exc}")

    # ── Contract awards ───────────────────────────────────────────────────

    def _import_contract_awards(self) -> ImportResult:
        """
        Import public.contract_awards → BizEntityKind.CONTRACT.
        Relations:
          canonical company --AWARDED_TO--> contract
          contract          --AWARDED_TO--> winner company entity

        Reuses _import_contract_awards_batch() via _run_stage_to_completion()
        -- looping the same batch method POST /api/import/contract_awards/batch
        calls, rather than a separate full-table fetch-loop implementation.
        Each page provides its own transaction (see _import_contract_awards_batch),
        which is why this step is in run()'s _self_transacting set.
        """
        result = self._run_stage_to_completion(self._import_contract_awards_batch)
        result.importer = f"{self.name}:contract_awards"

        logger.info(
            "contract_awards: created=%d updated=%d rels_created=%d errors=%d",
            result.entities_created,
            result.entities_updated,
            result.relations_created,
            len(result.errors),
        )
        return result

    def _process_contract_awards_batch(self, rows: list, result: ImportResult) -> None:
        """
        Process one batch of contract_awards rows — identical business logic
        to the original inline loop in _import_contract_awards(), extracted
        so the batched endpoint can reuse it without duplicating it.

        Deliberately does NOT open its own transaction (unlike
        _process_permit_batch): _import_contract_awards() is not
        self-transacting in run() — the whole step is already wrapped in one
        outer transaction there, and nesting another transaction inside this
        helper would risk implementation-defined behavior on that existing
        path. Callers that need their own transaction boundary (e.g. the
        batched endpoint) wrap their call to this method themselves.
        """
        for row in rows:
            (
                db_id,
                external_id,
                title,
                winner_company,
                award_value,
                currency,
                award_date,
                buyer_org,
                proc_category,
                src,
                company_id,
            ) = row

            name = _s(title) or _s(external_id) or f"award-{db_id}"
            attrs: dict = {"scraper_id": db_id}
            for k, v in [
                ("external_id", external_id),
                ("winner_company", winner_company),
                ("award_value", award_value),
                ("currency", currency),
                ("award_date", award_date),
                ("buyer_organization", buyer_org),
                ("procurement_category", proc_category),
                ("source", src),
            ]:
                if _s(str(v) if v is not None else ""):
                    attrs[k] = v if isinstance(v, (int, float)) else _s(v)

            try:
                _preserved_uid = self._uid_snapshot.get((BizEntityKind.CONTRACT.value, canonicalize(name)))
                contract_e, created = self.repo.put_entity(
                    kind=BizEntityKind.CONTRACT,
                    name=name,
                    attributes=attrs,
                    source=_SOURCE,
                    write_history=False,
                    uid=_preserved_uid,
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                # Canonical company → contract
                if company_id and company_id in self._company_id_to_uid:
                    cmp_uid = self._company_id_to_uid[company_id]
                    _, rc = self.repo.put_relation(
                        source_uid=cmp_uid,
                        kind=BizRelationKind.AWARDED_TO,
                        target_uid=contract_e.uid,
                        source=_SOURCE,
                        attributes={
                            "award_date": _s(award_date),
                            "award_value": award_value,
                        },
                    )
                    if rc:
                        result.relations_created += 1
                    else:
                        result.relations_updated += 1

                # Link contract → winner only when the company already
                # exists in the graph (projected from SoR).  Never CREATE.
                winner_name = _s(winner_company)
                if winner_name:
                    winner_uid = self._lookup_company_uid_readonly(winner_name)
                    if winner_uid is not None:
                        _, rc2 = self.repo.put_relation(
                            source_uid=contract_e.uid,
                            kind=BizRelationKind.AWARDED_TO,
                            target_uid=winner_uid,
                            source=_SOURCE,
                        )
                        if rc2:
                            result.relations_created += 1
                        else:
                            result.relations_updated += 1
                    else:
                        result.warnings.append(
                            f"contract_awards.id={db_id}: "
                            f"unresolved winner_company={winner_name!r}; "
                            "no existing graph COMPANY match; "
                            "contract→winner edge skipped"
                        )

            except Exception as exc:
                result.errors.append(f"contract_awards.id={db_id}: {exc}")

    def _import_contract_awards_batch(
        self, after_id: int = 0, limit: int = 5000
    ) -> tuple[ImportResult, int, bool]:
        """
        Import one bounded slice of public.contract_awards, ordered by id —
        the same batching pattern as _import_permits_batch(): resumable via
        after_id/has_more, resolves company_id per-batch via
        _resolve_company_ids_for_batch() (no companies re-import needed), and
        reuses _process_contract_awards_batch() for row processing so
        business logic is identical to the full _import_contract_awards()
        path. Wraps the batch in its own transaction since, unlike permits,
        the full path's transaction is provided by run() rather than by the
        row-processing helper itself (see _process_contract_awards_batch).

        Returns (result, last_id, has_more) — same contract as
        _import_permits_batch().
        """
        result = ImportResult(importer=f"{self.name}:contract_awards_batch")
        batch, has_more = self._fetch_ordered_batch(
            columns_sql="id, external_id, title, winner_company, award_value, "
            "currency, award_date, buyer_organization, "
            "procurement_category, source, company_id",
            table="contract_awards",
            after_id=after_id,
            limit=limit,
        )
        last_id = batch[-1][0] if batch else after_id
        if batch:
            self._resolve_company_ids_for_batch(batch)
            with self.repo.transaction():
                self._process_contract_awards_batch(batch, result)

        logger.info(
            "contract_awards_batch: after_id=%d limit=%d processed=%d created=%d updated=%d has_more=%s",
            after_id,
            limit,
            len(batch),
            result.entities_created,
            result.entities_updated,
            has_more,
        )
        return result, last_id, has_more

    # ── Organizations (tender buyers) ─────────────────────────────────────

    def _import_organizations(self) -> ImportResult:
        """
        Extract unique organization names from tenders and create ORGANIZATION
        entities with ISSUES→TENDER relations.
        """
        result = ImportResult(importer=f"{self.name}:organizations")
        conn = self._get_source_conn()
        try:
            cur = conn.cursor()
            try:
                cur.execute("""
                    SELECT DISTINCT organization FROM public.tenders
                    WHERE organization IS NOT NULL AND organization <> ''
                    UNION
                    SELECT DISTINCT company FROM public.commercial_tenders
                    WHERE company IS NOT NULL AND company <> ''
                    UNION
                    SELECT DISTINCT company FROM public.arch_tenders
                    WHERE company IS NOT NULL AND company <> ''
                """)
                orgs = [r[0] for r in cur.fetchall() if r[0]]
            finally:
                cur.close()
        finally:
            self._close_source_conn(conn)

        for org_name in orgs:
            name = _s(org_name)
            if not name:
                continue
            try:
                _preserved_uid = self._uid_snapshot.get((BizEntityKind.ORGANIZATION.value, canonicalize(name)))
                _, created = self.repo.put_entity(
                    kind=BizEntityKind.ORGANIZATION,
                    name=name,
                    source=_SOURCE,
                    write_history=False,
                    uid=_preserved_uid,
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1
            except Exception as exc:
                result.errors.append(f"org '{name}': {exc}")

        logger.info(
            "organizations: created=%d updated=%d errors=%d",
            result.entities_created,
            result.entities_updated,
            len(result.errors),
        )
        return result
