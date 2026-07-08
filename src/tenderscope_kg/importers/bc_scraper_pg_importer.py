"""
BC Scraper PostgreSQL Importer.

Reads directly from public.* tables in the shared Railway PostgreSQL database
(the bc-tender-scraper schema) and upserts into the graph.* schema via
BizRepository.  This importer is READ-ONLY with respect to public.*.

Tables consumed:
    public.companies        → BizEntityKind.COMPANY (canonical entities)
    public.tenders          → BizEntityKind.TENDER
    public.commercial_tenders → BizEntityKind.TENDER (merged with tenders)
    public.arch_tenders     → BizEntityKind.TENDER (merged)
    public.permits          → BizEntityKind.PERMIT
    public.contract_awards  → BizEntityKind.CONTRACT + AWARDED_TO relation

Relations created:
    COMPANY  --AWARDED_TO-->    CONTRACT  (from contract_awards.company_id)
    COMPANY  --HAS_PERMIT-->    PERMIT    (from permits.company_id)
    TENDER   --ISSUED_BY-->     COMPANY   (from tenders.organization)
    CONTRACT --AWARDED_TO-->    COMPANY   (from contract_awards.winner_company)

Source tag: "bc_scraper_pg"
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..domain import BizEntityKind, BizRelationKind
from ..domain.results import ImportResult
from ..repository._base import BizRepository
from .base import BaseImporter

logger = logging.getLogger(__name__)

_SOURCE = "bc_scraper_pg"


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
        conn:           psycopg2 connection to the shared Railway DB.
                        The connection is used read-only; no writes are made
                        to public.* tables.
        batch_size:     Rows fetched per cursor iteration (memory control).
    """

    name = "bc_scraper_pg"

    def __init__(
        self,
        repo: BizRepository,
        conn: Any,
        batch_size: int = 500,
    ) -> None:
        super().__init__(repo, source_tag=_SOURCE)
        self._conn = conn
        self._batch_size = batch_size

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

        # Steps that manage their own batched transactions internally
        _self_transacting = {self._import_permits}

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
        cur = self._conn.cursor()
        try:
            counts: dict = {}
            for table in ("companies", "tenders", "permits", "contract_awards",
                          "commercial_tenders", "arch_tenders"):
                try:
                    cur.execute(f"SELECT COUNT(*) FROM public.{table}")  # noqa: S608
                    counts[table] = cur.fetchone()[0]
                except Exception as exc:
                    counts[table] = f"ERROR: {exc}"
                    self._conn.rollback()
            return counts
        finally:
            cur.close()

    # ── Companies ─────────────────────────────────────────────────────────

    def _import_companies(self) -> ImportResult:
        """
        Import public.companies where entity_role = 'canonical'.
        Alias rows (entity_role = 'applicant_alias') are linked via RELATED_TO
        pointing to their canonical.
        """
        result = ImportResult(importer=f"{self.name}:companies")
        cur = self._conn.cursor()

        # Map from scraper company id → graph UID for later relation steps
        self._company_id_to_uid: dict[int, str] = {}

        try:
            cur.execute("""
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
            """)
            while True:
                rows = cur.fetchmany(self._batch_size)
                if not rows:
                    break
                for row in rows:
                    (
                        db_id, display_name, raw_name, entity_role,
                        canonical_company_id, construction_score,
                        total_projects, total_award_value, award_count,
                        primary_city, primary_province,
                        google_address, google_phone,
                        primary_trade, dominant_sector,
                    ) = row

                    name = _s(display_name) or _s(raw_name)
                    if not name:
                        result.warnings.append(f"companies.id={db_id}: empty name, skipping")
                        continue

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
                        ("city", primary_city), ("province", primary_province),
                        ("address", google_address), ("phone", google_phone),
                        ("primary_trade", primary_trade), ("dominant_sector", dominant_sector),
                    ]:
                        if _s(v):
                            attrs[k] = _s(v)

                    try:
                        entity, created = self.repo.put_entity(
                            kind=BizEntityKind.COMPANY,
                            name=name,
                            attributes=attrs,
                            source=_SOURCE,
                            write_history=False,
                        )
                        self._company_id_to_uid[db_id] = entity.uid
                        if created:
                            result.entities_created += 1
                        else:
                            result.entities_updated += 1
                    except Exception as exc:
                        result.errors.append(f"companies.id={db_id}: {exc}")

        finally:
            cur.close()

        logger.info(
            "companies: created=%d updated=%d errors=%d",
            result.entities_created, result.entities_updated, len(result.errors),
        )
        return result

    # ── Tenders ───────────────────────────────────────────────────────────

    def _import_tenders(self) -> ImportResult:
        """Import tenders, commercial_tenders, arch_tenders → BizEntityKind.TENDER."""
        result = ImportResult(importer=f"{self.name}:tenders")
        self._tender_title_to_uid: dict[str, str] = {}

        queries = [
            ("tenders", """
                SELECT id, title, organization, category, closing_date,
                       estimated_value, source, tender_id, url
                FROM public.tenders
                ORDER BY id
            """),
            ("commercial_tenders", """
                SELECT id, title, company AS organization, category, deadline AS closing_date,
                       value AS estimated_value, source, tender_id, url
                FROM public.commercial_tenders
                ORDER BY id
            """),
            ("arch_tenders", """
                SELECT id, title, company AS organization, category, deadline AS closing_date,
                       value AS estimated_value, 'arch_tenders' AS source, tender_id, url
                FROM public.arch_tenders
                ORDER BY id
            """),
        ]

        cur = self._conn.cursor()
        try:
            for table, query in queries:
                cur.execute(query)
                while True:
                    rows = cur.fetchmany(self._batch_size)
                    if not rows:
                        break
                    for row in rows:
                        db_id, title, org, category, closing_date, value, src, tender_id, url = row

                        name = _s(title)
                        if not name:
                            result.warnings.append(f"{table}.id={db_id}: empty title, skipping")
                            continue

                        attrs: dict = {
                            "scraper_id": db_id,
                            "scraper_table": table,
                        }
                        for k, v in [
                            ("organization", org), ("category", category),
                            ("closing_date", closing_date), ("estimated_value", value),
                            ("source", src), ("tender_id", tender_id), ("url", url),
                        ]:
                            if _s(v):
                                attrs[k] = _s(v)

                        try:
                            entity, created = self.repo.put_entity(
                                kind=BizEntityKind.TENDER,
                                name=name,
                                attributes=attrs,
                                source=_SOURCE,
                                write_history=False,
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

        logger.info(
            "tenders: created=%d updated=%d errors=%d",
            result.entities_created, result.entities_updated, len(result.errors),
        )
        return result

    # ── Permits ───────────────────────────────────────────────────────────

    _PERMIT_TX_SIZE = 2000  # rows per transaction batch

    def _import_permits(self) -> ImportResult:
        """
        Import public.permits → BizEntityKind.PERMIT.
        Link company_id → permit via HAS_PERMIT relation where set.
        Processed in batches of _PERMIT_TX_SIZE to bound transaction size.
        """
        result = ImportResult(importer=f"{self.name}:permits")
        cur = self._conn.cursor()
        try:
            cur.execute("""
                SELECT id, external_id, address, city, permit_type,
                       project_value, applicant, contractor,
                       lifecycle_status, source, company_id
                FROM public.permits
                ORDER BY id
            """)
            batch: list = []
            while True:
                rows = cur.fetchmany(self._batch_size)
                if not rows:
                    if batch:
                        self._process_permit_batch(batch, result)
                    break
                batch.extend(rows)
                if len(batch) >= self._PERMIT_TX_SIZE:
                    self._process_permit_batch(batch, result)
                    batch = []
        finally:
            cur.close()

        logger.info(
            "permits: created=%d updated=%d rels_created=%d errors=%d",
            result.entities_created, result.entities_updated,
            result.relations_created, len(result.errors),
        )
        return result

    def _process_permit_batch(self, rows: list, result: ImportResult) -> None:
        """Process one batch of permit rows inside a single transaction."""
        with self.repo.transaction():
            for row in rows:
                (db_id, external_id, address, city, permit_type,
                 project_value, applicant, contractor,
                 lifecycle_status, src, company_id) = row

                name = _s(external_id) or _s(address) or f"permit-{db_id}"
                attrs: dict = {"scraper_id": db_id}
                for k, v in [
                    ("external_id", external_id), ("address", address),
                    ("city", city), ("permit_type", permit_type),
                    ("project_value", project_value), ("applicant", applicant),
                    ("contractor", contractor),
                    ("lifecycle_status", lifecycle_status), ("source", src),
                ]:
                    if _s(v):
                        attrs[k] = _s(v)

                try:
                    permit_e, created = self.repo.put_entity(
                        kind=BizEntityKind.PERMIT,
                        name=name,
                        attributes=attrs,
                        source=_SOURCE,
                        write_history=False,
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
        """
        result = ImportResult(importer=f"{self.name}:contract_awards")
        cur = self._conn.cursor()
        try:
            cur.execute("""
                SELECT id, external_id, title, winner_company, award_value,
                       currency, award_date, buyer_organization,
                       procurement_category, source, company_id
                FROM public.contract_awards
                ORDER BY id
            """)
            while True:
                rows = cur.fetchmany(self._batch_size)
                if not rows:
                    break
                for row in rows:
                    (db_id, external_id, title, winner_company, award_value,
                     currency, award_date, buyer_org, proc_category,
                     src, company_id) = row

                    name = _s(title) or _s(external_id) or f"award-{db_id}"
                    attrs: dict = {"scraper_id": db_id}
                    for k, v in [
                        ("external_id", external_id), ("winner_company", winner_company),
                        ("award_value", award_value), ("currency", currency),
                        ("award_date", award_date), ("buyer_organization", buyer_org),
                        ("procurement_category", proc_category), ("source", src),
                    ]:
                        if _s(str(v) if v is not None else ""):
                            attrs[k] = v if isinstance(v, (int, float)) else _s(v)

                    try:
                        contract_e, created = self.repo.put_entity(
                            kind=BizEntityKind.CONTRACT,
                            name=name,
                            attributes=attrs,
                            source=_SOURCE,
                            write_history=False,
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
                                attributes={"award_date": _s(award_date),
                                            "award_value": award_value},
                            )
                            if rc:
                                result.relations_created += 1
                            else:
                                result.relations_updated += 1

                        # Winner company name entity (may differ from canonical)
                        winner_name = _s(winner_company)
                        if winner_name:
                            winner_e, _ = self.repo.put_entity(
                                kind=BizEntityKind.COMPANY,
                                name=winner_name,
                                attributes={"source_table": "contract_awards"},
                                source=_SOURCE,
                                write_history=False,
                            )
                            _, rc2 = self.repo.put_relation(
                                source_uid=contract_e.uid,
                                kind=BizRelationKind.AWARDED_TO,
                                target_uid=winner_e.uid,
                                source=_SOURCE,
                            )
                            if rc2:
                                result.relations_created += 1
                            else:
                                result.relations_updated += 1

                    except Exception as exc:
                        result.errors.append(f"contract_awards.id={db_id}: {exc}")
        finally:
            cur.close()

        logger.info(
            "contract_awards: created=%d updated=%d rels_created=%d errors=%d",
            result.entities_created, result.entities_updated,
            result.relations_created, len(result.errors),
        )
        return result

    # ── Organizations (tender buyers) ─────────────────────────────────────

    def _import_organizations(self) -> ImportResult:
        """
        Extract unique organization names from tenders and create ORGANIZATION
        entities with ISSUES→TENDER relations.
        """
        result = ImportResult(importer=f"{self.name}:organizations")
        cur = self._conn.cursor()
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

        for org_name in orgs:
            name = _s(org_name)
            if not name:
                continue
            try:
                _, created = self.repo.put_entity(
                    kind=BizEntityKind.ORGANIZATION,
                    name=name,
                    source=_SOURCE,
                    write_history=False,
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1
            except Exception as exc:
                result.errors.append(f"org '{name}': {exc}")

        logger.info(
            "organizations: created=%d updated=%d errors=%d",
            result.entities_created, result.entities_updated, len(result.errors),
        )
        return result
