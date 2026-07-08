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

from ..domain import BizEntityKind, BizRelationKind, IdentityEvidence, EXTERNAL_ID_KEYS
from ..domain.kinds import canonicalize
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
        self._conn = conn
        self._batch_size = batch_size
        self._uid_snapshot: dict[tuple[str, str], str] = uid_snapshot or {}

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
        Two-pass import of public.companies.

        Pass 1 — canonical rows (entity_role = 'canonical' or NULL):
            Inserted as BizEntityKind.COMPANY.  These are the permanent
            identity nodes for the entire platform.

        Pass 2 — alias rows (entity_role = 'applicant_alias'):
            Inserted as BizEntityKind.COMPANY_ALIAS.
            Each alias gets a single ALIAS_OF edge → its canonical COMPANY.
            Aliases are NOT primary company nodes and will not appear in
            company listings or be used as relation targets.

        After both passes, self._company_id_to_uid maps every scraper id
        (canonical AND alias) to the canonical COMPANY uid.  All downstream
        steps (permits, contracts, tenders) therefore always attach to the
        canonical node, never to an alias.
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
            db_id, entity_role, canonical_company_id,
            construction_score, total_projects, total_award_value,
            award_count, primary_city, primary_province,
            google_address, google_phone, primary_trade, dominant_sector,
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
                ("city", primary_city), ("province", primary_province),
                ("address", google_address), ("phone", google_phone),
                ("primary_trade", primary_trade), ("dominant_sector", dominant_sector),
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

        cur = self._conn.cursor()
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

        for row in all_rows:
            (
                db_id, display_name, raw_name, entity_role,
                canonical_company_id, construction_score,
                total_projects, total_award_value, award_count,
                primary_city, primary_province,
                google_address, google_phone,
                primary_trade, dominant_sector,
            ) = row

            if _s(entity_role) == "applicant_alias":
                continue  # handled in Pass 2

            name = _s(display_name) or _s(raw_name)
            if not name:
                result.warnings.append(f"companies.id={db_id}: empty name, skipping")
                continue

            attrs = _build_attrs(
                db_id, entity_role, canonical_company_id,
                construction_score, total_projects, total_award_value,
                award_count, primary_city, primary_province,
                google_address, google_phone, primary_trade, dominant_sector,
            )

            try:
                _preserved_uid = self._uid_snapshot.get(
                    (BizEntityKind.COMPANY.value, canonicalize(name))
                )
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
                result.errors.append(f"companies.id={db_id} (canonical): {exc}")

        # ── Pass 2: alias companies ────────────────────────────────────────
        for row in all_rows:
            (
                db_id, display_name, raw_name, entity_role,
                canonical_company_id, construction_score,
                total_projects, total_award_value, award_count,
                primary_city, primary_province,
                google_address, google_phone,
                primary_trade, dominant_sector,
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
                db_id, entity_role, canonical_company_id,
                construction_score, total_projects, total_award_value,
                award_count, primary_city, primary_province,
                google_address, google_phone, primary_trade, dominant_sector,
            )
            attrs["alias_for_uid"] = canonical_uid

            try:
                _preserved_alias_uid = self._uid_snapshot.get(
                    (BizEntityKind.COMPANY_ALIAS.value, canonicalize(name))
                )
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
                    evidence=[{
                        "field":  "canonical_company_id",
                        "value":  canonical_company_id,
                        "source": _SOURCE,
                    }],
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
            sum(1 for v in self._company_id_to_uid.values()
                if v not in _canonical_scraper_id_to_uid.values()
                or len([k for k, u in self._company_id_to_uid.items() if u == v]) > 1),
            len(result.errors),
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

                        # Resolve winner company name → canonical UID.
                        # resolve_company_uid() is the single safe entry point:
                        # it checks COMPANY, then COMPANY_ALIAS, then creates
                        # a new COMPANY only if the name is genuinely unknown.
                        winner_name = _s(winner_company)
                        if winner_name:
                            winner_e = self.repo.resolve_company_uid(
                                winner_name,
                                source=_SOURCE,
                                attributes={"source_table": "contract_awards"},
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
