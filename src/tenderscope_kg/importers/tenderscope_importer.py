"""
TenderScope-native importer.

Reads the bc-tender-scraper output formats directly:
  - tenders.csv / arch_tenders.csv / commercial_tenders.csv
  - building_permits.csv
  - contract_awards.csv
  - companies (from any CSV with company-shaped columns)

Column mappings are based on the actual schema observed in the TenderScope
scraper output.  The importer is intentionally tolerant — missing columns
are skipped rather than erroring out, so it works across schema versions.
"""
from __future__ import annotations

import csv
import time
from pathlib import Path
from typing import Optional

from ..domain import BizEntityKind, BizRelationKind, canonicalize
from ..domain.results import ImportResult
from ..repository._base import BizRepository
from .base import BaseImporter


# ── Column name constants (as seen in bc-tender-scraper output) ───────────────

_TENDER_COLS = {
    "name":         ["title", "tender_title", "name", "project_name"],
    "source_url":   ["url", "source_url", "link"],
    "closing_at":   ["closing_at", "closing_date", "close_date"],
    "value":        ["value", "estimated_value", "contract_value"],
    "region":       ["region", "municipality", "location"],
    "category":     ["category", "sector", "type"],
    "source":       ["source", "origin", "scraper"],
    "external_id":  ["external_id", "tender_id", "id"],
}

_COMPANY_COLS = {
    "name":         ["company_name", "vendor_name", "name", "legal_name", "company"],
    "address":      ["address", "street_address", "company_address"],
    "city":         ["city", "municipality"],
    "province":     ["province", "prov", "state"],
    "phone":        ["phone", "telephone", "phone_number"],
    "email":        ["email", "contact_email"],
    "website":      ["website", "url", "web"],
    "naics":        ["naics", "naics_code"],
    "category":     ["category", "sector", "industry"],
}

_PERMIT_COLS = {
    "name":         ["permit_number", "application_number", "permit_no"],
    "address":      ["address", "civic_address", "site_address"],
    "city":         ["city", "municipality"],
    "applicant":    ["applicant", "applicant_name", "owner_name"],
    "value":        ["project_value", "permit_value", "construction_value"],
    "type":         ["permit_type", "type", "category"],
    "status":       ["status", "permit_status"],
}

_AWARD_COLS = {
    "tender_name":  ["tender_title", "title", "contract_title", "description"],
    "company_name": ["vendor_name", "company_name", "awarded_to", "contractor"],
    "value":        ["contract_value", "award_amount", "value"],
    "awarded_at":   ["awarded_at", "award_date", "date"],
}


def _first_val(row: dict, candidates: list[str]) -> str:
    """Return the first non-empty value from candidate column names."""
    for col in candidates:
        v = (row.get(col) or "").strip()
        if v:
            return v
    return ""


def _read_csv(path: Path, encoding: str = "utf-8-sig") -> list[dict]:
    with open(path, encoding=encoding, newline="", errors="replace") as fh:
        return list(csv.DictReader(fh))


class TenderScopeImporter(BaseImporter):
    """
    Import TenderScope scraper output into the business knowledge graph.

    Supports four file types detected by content-column heuristics.
    Call run() to auto-detect; or call import_tenders(), import_companies(),
    import_permits(), import_awards() directly for explicit control.
    """

    name = "tenderscope"

    def __init__(
        self,
        repo: BizRepository,
        path: str,
        source_tag: str = "tenderscope",
        encoding: str = "utf-8-sig",
        limit: Optional[int] = None,
    ) -> None:
        super().__init__(repo)
        self.path = Path(path)
        self.source_tag = source_tag
        self.encoding = encoding
        self.limit = limit

    def run(self) -> ImportResult:
        """Auto-detect file type and dispatch to the right importer."""
        result = self._make_result()
        result.importer = f"tenderscope:{self.path.name}"
        t0 = time.perf_counter()

        if not self.path.exists():
            result.errors.append(f"File not found: {self.path}")
            return result

        rows = _read_csv(self.path, self.encoding)
        if not rows:
            result.warnings.append("Empty file")
            return result

        if self.limit:
            rows = rows[: self.limit]

        cols = set(rows[0].keys())
        # Detect by column presence
        if any(c in cols for c in ["vendor_name", "awarded_to", "award_date"]):
            sub = self.import_awards(rows)
        elif any(c in cols for c in ["permit_number", "application_number", "permit_no"]):
            sub = self.import_permits(rows)
        elif any(c in cols for c in ["tender_title", "closing_at", "tender_id"]):
            sub = self.import_tenders(rows)
        elif any(c in cols for c in ["company_name", "legal_name", "vendor_name"]):
            sub = self.import_companies(rows)
        else:
            # Generic fallback: treat first column as name, entity_kind=company
            sub = self.import_companies(rows)

        self.repo.rebuild_fts()
        result.entities_created = sub.entities_created
        result.entities_updated = sub.entities_updated
        result.relations_created = sub.relations_created
        result.relations_updated = sub.relations_updated
        result.errors.extend(sub.errors)
        result.warnings.extend(sub.warnings)
        result.elapsed_s = time.perf_counter() - t0
        return result

    # ── Tender import ─────────────────────────────────────────────────────

    def import_tenders(self, rows: list[dict]) -> ImportResult:
        result = ImportResult(importer=f"{self.name}:tenders")
        for i, row in enumerate(rows):
            name = _first_val(row, _TENDER_COLS["name"])
            if not name:
                result.warnings.append(f"Row {i+2}: no tender name, skipping")
                continue
            attrs = {
                k: v for k, v in {
                    "source_url":  _first_val(row, _TENDER_COLS["source_url"]),
                    "closing_at":  _first_val(row, _TENDER_COLS["closing_at"]),
                    "value":       _first_val(row, _TENDER_COLS["value"]),
                    "region":      _first_val(row, _TENDER_COLS["region"]),
                    "category":    _first_val(row, _TENDER_COLS["category"]),
                    "external_id": _first_val(row, _TENDER_COLS["external_id"]),
                    "source":      _first_val(row, _TENDER_COLS["source"]),
                }.items() if v
            }
            try:
                _, created = self.repo.put_entity(
                    kind=BizEntityKind.TENDER,
                    name=name,
                    attributes=attrs,
                    source=self.source_tag,
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1
            except Exception as exc:
                result.errors.append(f"Row {i+2}: {exc}")
        return result

    # ── Company import ────────────────────────────────────────────────────

    def import_companies(self, rows: list[dict]) -> ImportResult:
        result = ImportResult(importer=f"{self.name}:companies")
        for i, row in enumerate(rows):
            name = _first_val(row, _COMPANY_COLS["name"])
            if not name:
                continue
            attrs = {
                k: v for k, v in {
                    "address":  _first_val(row, _COMPANY_COLS["address"]),
                    "city":     _first_val(row, _COMPANY_COLS["city"]),
                    "province": _first_val(row, _COMPANY_COLS["province"]),
                    "phone":    _first_val(row, _COMPANY_COLS["phone"]),
                    "email":    _first_val(row, _COMPANY_COLS["email"]),
                    "website":  _first_val(row, _COMPANY_COLS["website"]),
                    "naics":    _first_val(row, _COMPANY_COLS["naics"]),
                    "category": _first_val(row, _COMPANY_COLS["category"]),
                }.items() if v
            }
            try:
                existing = self.repo.find_by_canonical(
                    BizEntityKind.COMPANY, canonicalize(name)
                )
                entity = self.repo.resolve_company_uid(
                    name,
                    source=self.source_tag,
                    attributes=attrs,
                )
                created = existing is None
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                # Create City / Province sub-entities and locate_at relations
                city_name = attrs.get("city")
                if city_name:
                    city_e, _ = self.repo.put_entity(
                        kind=BizEntityKind.CITY,
                        name=city_name,
                        source=self.source_tag,
                    )
                    _, rc = self.repo.put_relation(
                        source_uid=entity.uid,
                        kind=BizRelationKind.IN_CITY,
                        target_uid=city_e.uid,
                        source=self.source_tag,
                    )
                    if rc:
                        result.relations_created += 1

                prov_name = attrs.get("province")
                if prov_name:
                    prov_e, _ = self.repo.put_entity(
                        kind=BizEntityKind.PROVINCE,
                        name=prov_name,
                        source=self.source_tag,
                    )
                    _, rc = self.repo.put_relation(
                        source_uid=entity.uid,
                        kind=BizRelationKind.IN_PROVINCE,
                        target_uid=prov_e.uid,
                        source=self.source_tag,
                    )
                    if rc:
                        result.relations_created += 1

            except Exception as exc:
                result.errors.append(f"Row {i+2}: {exc}")
        return result

    # ── Permit import ─────────────────────────────────────────────────────

    def import_permits(self, rows: list[dict]) -> ImportResult:
        result = ImportResult(importer=f"{self.name}:permits")
        for i, row in enumerate(rows):
            name = _first_val(row, _PERMIT_COLS["name"])
            if not name:
                result.warnings.append(f"Row {i+2}: no permit number, skipping")
                continue
            attrs = {
                k: v for k, v in {
                    "address": _first_val(row, _PERMIT_COLS["address"]),
                    "city":    _first_val(row, _PERMIT_COLS["city"]),
                    "value":   _first_val(row, _PERMIT_COLS["value"]),
                    "type":    _first_val(row, _PERMIT_COLS["type"]),
                    "status":  _first_val(row, _PERMIT_COLS["status"]),
                }.items() if v
            }
            try:
                permit_e, created = self.repo.put_entity(
                    kind=BizEntityKind.PERMIT,
                    name=name,
                    attributes=attrs,
                    source=self.source_tag,
                )
                if created:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                # Link applicant as a company if present.
                # resolve_company_uid() ensures we attach to the canonical UID.
                applicant = _first_val(row, _PERMIT_COLS["applicant"])
                if applicant:
                    company_e = self.repo.resolve_company_uid(
                        applicant,
                        source=self.source_tag,
                    )
                    _, rc = self.repo.put_relation(
                        source_uid=company_e.uid,
                        kind=BizRelationKind.HAS_PERMIT,
                        target_uid=permit_e.uid,
                        source=self.source_tag,
                    )
                    if rc:
                        result.relations_created += 1

            except Exception as exc:
                result.errors.append(f"Row {i+2}: {exc}")
        return result

    # ── Contract awards import ────────────────────────────────────────────

    def import_awards(self, rows: list[dict]) -> ImportResult:
        result = ImportResult(importer=f"{self.name}:awards")
        for i, row in enumerate(rows):
            tender_name = _first_val(row, _AWARD_COLS["tender_name"])
            company_name = _first_val(row, _AWARD_COLS["company_name"])
            if not tender_name or not company_name:
                result.warnings.append(f"Row {i+2}: missing tender or company name, skipping")
                continue
            attrs = {
                k: v for k, v in {
                    "value":      _first_val(row, _AWARD_COLS["value"]),
                    "awarded_at": _first_val(row, _AWARD_COLS["awarded_at"]),
                }.items() if v
            }
            try:
                tender_e, tc = self.repo.put_entity(
                    kind=BizEntityKind.TENDER,
                    name=tender_name,
                    source=self.source_tag,
                )
                # resolve_company_uid() — name → canonical UID, alias-aware.
                _existing_co = self.repo.find_by_canonical(
                    BizEntityKind.COMPANY, canonicalize(company_name)
                )
                company_e = self.repo.resolve_company_uid(
                    company_name,
                    source=self.source_tag,
                )
                cc = _existing_co is None
                result.entities_created += int(tc) + int(cc)
                result.entities_updated += int(not tc) + int(not cc)

                # Bidirectional award relation
                _, rc1 = self.repo.put_relation(
                    source_uid=tender_e.uid,
                    kind=BizRelationKind.AWARDED_TO,
                    target_uid=company_e.uid,
                    source=self.source_tag,
                    attributes=attrs,
                )
                _, rc2 = self.repo.put_relation(
                    source_uid=company_e.uid,
                    kind=BizRelationKind.AWARDED_BY,
                    target_uid=tender_e.uid,
                    source=self.source_tag,
                    attributes=attrs,
                )
                result.relations_created += int(rc1) + int(rc2)

            except Exception as exc:
                result.errors.append(f"Row {i+2}: {exc}")
        return result
