"""
BCTenderImporter — production importer for the bc-tender-scraper dataset.

Handles all five data sources from the bc-tender-scraper repository:

  tenders.csv / tenders.json    Federal + MERX tenders (buyandsell.gc.ca / merx.com)
  commercial_tenders.csv        Municipal/private RFPs (bidcentral)
  arch_tenders.csv              Architecture & engineering tenders (MERX)
  contract_awards.csv           Contract award notices (winner companies)
  building_permits.csv          Municipal building permits (applicants + contractors)

Entity types created
--------------------
  TEN   Tender          (from all tender sources)
  ORG   Organization    (buying organizations / issuers)
  CMP   Company         (award winners, permit applicants, permit contractors)
  PRM   Permit          (building permits)
  CTY   City            (extracted from location / permit city fields)
  PRV   Province        (extracted from location strings)
  IND   Industry        (from category fields)

Relations created
-----------------
  ORG  --[issues]-->        TEN   (buyer organization issues a tender)
  TEN  --[issued_by]-->     ORG   (inverse)
  CMP  --[awarded_to]-->    TEN   (company won a contract on this tender)
  TEN  --[awarded_to]-->    CMP   (tender was awarded to this company)
  CMP  --[applied_for]-->   PRM   (company is permit applicant)
  CMP  --[contracted_for]-->PRM   (company is permit contractor)
  CMP  --[in_city]-->       CTY
  CMP  --[in_province]-->   PRV
  TEN  --[in_city]-->       CTY
  TEN  --[in_province]-->   PRV
  TEN  --[in_industry]-->   IND
  ORG  --[in_city]-->       CTY

Design
------
* All imports are idempotent: re-running merges attributes, never duplicates.
* Every company gets a permanent CMP-XXXXXXXX UID on first insert.
* History is written for every entity on each call (write_history=True).
* Location parsing is best-effort: "British Columbia" → PRV, "Nanaimo" → CTY.
* Contract value strings like "CAD 273,936.60" are cleaned to floats.
* Empty / whitespace-only strings are treated as missing.
"""

from __future__ import annotations

import csv
import json
import re
import time
from pathlib import Path
from typing import Any, Optional

from ..domain import BizEntityKind, BizRelationKind
from ..domain.results import ImportResult
from ..repository._base import BizRepository
from .base import BaseImporter

# ── Known Canadian province names / abbreviations ─────────────────────────────
_PROVINCES: frozenset[str] = frozenset(
    {
        "british columbia",
        "bc",
        "alberta",
        "ab",
        "ontario",
        "on",
        "quebec",
        "qc",
        "nova scotia",
        "ns",
        "new brunswick",
        "nb",
        "manitoba",
        "mb",
        "saskatchewan",
        "sk",
        "newfoundland",
        "newfoundland and labrador",
        "nl",
        "prince edward island",
        "pei",
        "northwest territories",
        "nt",
        "yukon",
        "yt",
        "nunavut",
        "nu",
        "canada",
    }
)

_PROVINCE_NORM: dict[str, str] = {
    "bc": "British Columbia",
    "ab": "Alberta",
    "on": "Ontario",
    "qc": "Quebec",
    "ns": "Nova Scotia",
    "nb": "New Brunswick",
    "mb": "Manitoba",
    "sk": "Saskatchewan",
    "nl": "Newfoundland and Labrador",
    "pei": "Prince Edward Island",
    "nt": "Northwest Territories",
    "yt": "Yukon",
    "nu": "Nunavut",
}


def _clean(v: Any) -> str:
    """Strip whitespace; return empty string for None/nan."""
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in ("nan", "none", "n/a", "-") else s


def _parse_value(raw: str) -> Optional[float]:
    """Parse 'CAD 273,936.60' or '500000' → float, or None."""
    s = re.sub(r"[^\d.]", "", raw or "")
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _parse_location(location: str) -> tuple[list[str], list[str]]:
    """
    Split a location string into (cities, provinces).

    Examples:
      "Canada , British Columbia"  → ([], ["British Columbia"])
      "Nanaimo"                    → (["Nanaimo"], [])
      "Victoria, BC"               → (["Victoria"], ["British Columbia"])
    """
    if not location:
        return [], []
    parts = [p.strip() for p in re.split(r"[,/|]+", location) if p.strip()]
    cities: list[str] = []
    provinces: list[str] = []
    for part in parts:
        lower = part.lower()
        if lower in _PROVINCES:
            provinces.append(_PROVINCE_NORM.get(lower, part.title()))
        elif lower == "canada":
            pass  # skip generic country tokens
        else:
            cities.append(part)
    return cities, provinces


def _extract_industry(category: str) -> Optional[str]:
    """Return a normalised industry name from category strings."""
    cat = _clean(category)
    return cat if cat else None


class BCTenderImporter(BaseImporter):
    """
    Production importer for bc-tender-scraper data files.

    Accepts a directory path (the bc-tender-scraper repo root) and imports
    all available data files.  Individual file imports can also be run via
    the ``import_*`` methods.
    """

    name = "bc_tender_importer"

    def __init__(
        self,
        repo: BizRepository,
        data_dir: str,
        source_tag: str = "bc_tender_scraper",
        limit: Optional[int] = None,
        write_history: bool = True,
    ) -> None:
        super().__init__(repo, source_tag=source_tag)
        self.data_dir = Path(data_dir)
        self.limit = limit
        self.write_history = write_history

    # ── Entry point ────────────────────────────────────────────────────────────

    def run(self) -> ImportResult:
        t0 = time.perf_counter()
        result = self._make_result()

        files = {
            "tenders_csv": self.data_dir / "tenders.csv",
            "tenders_json": self.data_dir / "tenders.json",
            "commercial_tenders": self.data_dir / "commercial_tenders.csv",
            "arch_tenders": self.data_dir / "arch_tenders.csv",
            "contract_awards": self.data_dir / "contract_awards.csv",
            "building_permits": self.data_dir / "building_permits.csv",
        }

        for label, path in files.items():
            if not path.exists():
                result.warnings.append(f"File not found, skipping: {path.name}")
                continue
            try:
                sub = self._dispatch(label, path)
                result.entities_created += sub.entities_created
                result.entities_updated += sub.entities_updated
                result.relations_created += sub.relations_created
                result.relations_updated += sub.relations_updated
                result.errors += sub.errors
                result.warnings += sub.warnings
            except Exception as exc:
                result.errors.append(f"{label}: {exc}")

        self.repo.rebuild_fts()
        result.elapsed_s = time.perf_counter() - t0
        return result

    def _dispatch(self, label: str, path: Path) -> ImportResult:
        if label == "tenders_csv":
            return self.import_federal_tenders_csv(path)
        if label == "tenders_json":
            return self.import_federal_tenders_json(path)
        if label in ("commercial_tenders", "arch_tenders"):
            return self.import_buyer_tenders_csv(path)
        if label == "contract_awards":
            return self.import_contract_awards(path)
        if label == "building_permits":
            return self.import_building_permits(path)
        raise ValueError(f"Unknown label: {label}")

    # ── Federal tenders (tenders.csv) ──────────────────────────────────────────

    def import_federal_tenders_csv(self, path: Path) -> ImportResult:
        result = self._make_result(f"federal_tenders_csv:{path.name}")
        rows = self._read_csv(path)
        for i, row in enumerate(rows):
            if self.limit and i >= self.limit:
                break
            try:
                self._process_federal_tender_row(row, result)
            except Exception as exc:
                result.errors.append(f"Row {i + 2}: {exc}")
        return result

    def import_federal_tenders_json(self, path: Path) -> ImportResult:
        result = self._make_result(f"federal_tenders_json:{path.name}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            result.errors.append(f"Cannot read {path.name}: {exc}")
            return result
        if not isinstance(data, list):
            result.errors.append(f"{path.name}: expected JSON array")
            return result
        for i, row in enumerate(data):
            if self.limit and i >= self.limit:
                break
            try:
                self._process_federal_tender_row(row, result)
            except Exception as exc:
                result.errors.append(f"Item {i}: {exc}")
        return result

    def _process_federal_tender_row(self, row: dict, result: ImportResult) -> None:
        title = _clean(row.get("title"))
        org_name = _clean(row.get("organization"))
        category = _clean(row.get("category"))
        posted = _clean(row.get("posted_date"))
        closing = _clean(row.get("closing_date"))
        est_value = _clean(row.get("estimated_value"))
        location = _clean(row.get("location"))
        tender_id = _clean(row.get("tender_id"))
        url = _clean(row.get("url"))
        source = _clean(row.get("source")) or self.source_tag

        if not title:
            result.warnings.append("Skipping row with empty title")
            return

        # ── Tender entity ──────────────────────────────────────────────────
        attrs: dict = {"source": source}
        if posted:
            attrs["posted_date"] = posted
        if closing:
            attrs["closing_date"] = closing
        if est_value:
            attrs["estimated_value"] = est_value
        if tender_id:
            attrs["tender_id"] = tender_id
        if url:
            attrs["url"] = url
        if category:
            attrs["category"] = category
        if location:
            attrs["location"] = location

        tender, created = self.repo.put_entity(
            BizEntityKind.TENDER,
            title,
            attributes=attrs,
            source=self.source_tag,
            write_history=self.write_history,
        )
        if created:
            result.entities_created += 1
        else:
            result.entities_updated += 1

        # ── Buyer organization ─────────────────────────────────────────────
        if org_name:
            org, c = self.repo.put_entity(
                BizEntityKind.ORGANIZATION,
                org_name,
                source=self.source_tag,
                write_history=self.write_history,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1

            _, rc = self.repo.put_relation(
                org.uid,
                BizRelationKind.ISSUES,
                tender.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

            _, rc = self.repo.put_relation(
                tender.uid,
                BizRelationKind.ISSUED_BY,
                org.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

        # ── Location ───────────────────────────────────────────────────────
        if location:
            self._link_location(tender, location, result)

        # ── Industry / category ────────────────────────────────────────────
        ind_name = _extract_industry(category)
        if ind_name:
            ind, c = self.repo.put_entity(
                BizEntityKind.INDUSTRY,
                ind_name,
                source=self.source_tag,
                write_history=False,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1
            _, rc = self.repo.put_relation(
                tender.uid,
                BizRelationKind.IN_INDUSTRY,
                ind.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

    # ── Commercial / arch tenders (bidcentral / MERX) ─────────────────────────

    def import_buyer_tenders_csv(self, path: Path) -> ImportResult:
        """
        Handles both commercial_tenders.csv and arch_tenders.csv.
        Schema: title, company (=buyer org), value, deadline, status, category,
                url, tender_id[, source]
        """
        result = self._make_result(f"buyer_tenders_csv:{path.name}")
        rows = self._read_csv(path)
        for i, row in enumerate(rows):
            if self.limit and i >= self.limit:
                break
            try:
                self._process_buyer_tender_row(row, result)
            except Exception as exc:
                result.errors.append(f"Row {i + 2}: {exc}")
        return result

    def _process_buyer_tender_row(self, row: dict, result: ImportResult) -> None:
        title = _clean(row.get("title"))
        buyer_name = _clean(row.get("company"))
        value = _clean(row.get("value"))
        deadline = _clean(row.get("deadline"))
        status = _clean(row.get("status"))
        category = _clean(row.get("category"))
        url = _clean(row.get("url"))
        tender_id = _clean(row.get("tender_id"))
        source = _clean(row.get("source")) or self.source_tag

        if not title:
            return

        # Strip location suffix embedded in title: "… Location: Penticton | Posted: …"
        clean_title = re.sub(r"\s*Location:.*$", "", title, flags=re.IGNORECASE).strip()
        # Extract embedded location from title if present
        loc_match = re.search(r"Location:\s*([^|]+)", title, re.IGNORECASE)
        location = loc_match.group(1).strip() if loc_match else ""

        attrs: dict = {"source": source}
        if deadline:
            attrs["closing_date"] = deadline
        if value:
            attrs["estimated_value"] = value
        if status:
            attrs["status"] = status
        if tender_id:
            attrs["tender_id"] = tender_id
        if url:
            attrs["url"] = url
        if category:
            attrs["category"] = category

        tender, created = self.repo.put_entity(
            BizEntityKind.TENDER,
            clean_title,
            attributes=attrs,
            source=self.source_tag,
            write_history=self.write_history,
        )
        if created:
            result.entities_created += 1
        else:
            result.entities_updated += 1

        # Buyer organization
        if buyer_name:
            org, c = self.repo.put_entity(
                BizEntityKind.ORGANIZATION,
                buyer_name,
                source=self.source_tag,
                write_history=self.write_history,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1

            _, rc = self.repo.put_relation(
                org.uid,
                BizRelationKind.ISSUES,
                tender.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

            _, rc = self.repo.put_relation(
                tender.uid,
                BizRelationKind.ISSUED_BY,
                org.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

        if location:
            self._link_location(tender, location, result)

        ind_name = _extract_industry(category)
        if ind_name:
            ind, c = self.repo.put_entity(
                BizEntityKind.INDUSTRY,
                ind_name,
                source=self.source_tag,
                write_history=False,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1
            _, rc = self.repo.put_relation(
                tender.uid,
                BizRelationKind.IN_INDUSTRY,
                ind.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

    # ── Contract awards ────────────────────────────────────────────────────────

    def import_contract_awards(self, path: Path) -> ImportResult:
        """
        contract_awards.csv schema:
          winner_company, contract_value, date, tender_title, url
        """
        result = self._make_result(f"contract_awards:{path.name}")
        rows = self._read_csv(path)
        for i, row in enumerate(rows):
            if self.limit and i >= self.limit:
                break
            try:
                self._process_award_row(row, result)
            except Exception as exc:
                result.errors.append(f"Row {i + 2}: {exc}")
        return result

    def _process_award_row(self, row: dict, result: ImportResult) -> None:
        company_name = _clean(row.get("winner_company"))
        contract_value = _clean(row.get("contract_value"))
        award_date = _clean(row.get("date"))
        tender_title = _clean(row.get("tender_title"))
        url = _clean(row.get("url"))

        if not company_name:
            result.warnings.append("Award row missing winner_company, skipping")
            return

        # ── Winner company (CMP) ───────────────────────────────────────────
        value_float = _parse_value(contract_value)
        cmp_attrs: dict = {}
        if value_float is not None:
            # Accumulate award value and count in attributes (best-effort)
            cmp_attrs["last_award_value"] = value_float
        if award_date:
            cmp_attrs["last_award_date"] = award_date

        company, created = self.repo.put_entity(
            BizEntityKind.COMPANY,
            company_name,
            attributes=cmp_attrs,
            source=self.source_tag,
            write_history=self.write_history,
        )
        if created:
            result.entities_created += 1
        else:
            result.entities_updated += 1

        # ── Related tender ─────────────────────────────────────────────────
        if tender_title:
            tender_attrs: dict = {}
            if url:
                tender_attrs["url"] = url
            if award_date:
                tender_attrs["award_date"] = award_date
            if contract_value:
                tender_attrs["contract_value"] = contract_value

            tender, tc = self.repo.put_entity(
                BizEntityKind.TENDER,
                tender_title,
                attributes=tender_attrs,
                source=self.source_tag,
                write_history=self.write_history,
            )
            if tc:
                result.entities_created += 1
            else:
                result.entities_updated += 1

            # Company ──[awarded_to]──▶ Tender
            award_attrs: dict = {}
            if value_float is not None:
                award_attrs["contract_value"] = value_float
            if award_date:
                award_attrs["award_date"] = award_date
            if url:
                award_attrs["url"] = url

            _, rc = self.repo.put_relation(
                company.uid,
                BizRelationKind.AWARDED_TO,
                tender.uid,
                source=self.source_tag,
                attributes=award_attrs,
                valid_from=award_date or None,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

            # Tender ──[awarded_to]──▶ Company  (reverse lookup convenience)
            _, rc = self.repo.put_relation(
                tender.uid,
                BizRelationKind.AWARDED_TO,
                company.uid,
                source=self.source_tag,
                attributes=award_attrs,
                valid_from=award_date or None,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

    # ── Building permits ───────────────────────────────────────────────────────

    def import_building_permits(self, path: Path) -> ImportResult:
        """
        building_permits.csv schema:
          external_id, address, permit_type, project_value, applicant,
          issue_date, application_date, description, contractor,
          local_area, source, city
        """
        result = self._make_result(f"building_permits:{path.name}")
        rows = self._read_csv(path)
        for i, row in enumerate(rows):
            if self.limit and i >= self.limit:
                break
            try:
                self._process_permit_row(row, result)
            except Exception as exc:
                result.errors.append(f"Row {i + 2}: {exc}")
        return result

    def _process_permit_row(self, row: dict, result: ImportResult) -> None:
        external_id = _clean(row.get("external_id"))
        address = _clean(row.get("address"))
        permit_type = _clean(row.get("permit_type"))
        project_value = _clean(row.get("project_value"))
        applicant = _clean(row.get("applicant"))
        issue_date = _clean(row.get("issue_date"))
        application_date = _clean(row.get("application_date"))
        description = _clean(row.get("description"))
        contractor = _clean(row.get("contractor"))
        local_area = _clean(row.get("local_area"))
        city = _clean(row.get("city"))

        # Permit display name = external_id if available, else address
        permit_name = external_id if external_id else address
        if not permit_name:
            result.warnings.append("Permit row missing both external_id and address, skipping")
            return

        perm_attrs: dict = {}
        if external_id:
            perm_attrs["external_id"] = external_id
        if address:
            perm_attrs["address"] = address
        if permit_type:
            perm_attrs["permit_type"] = permit_type
        if project_value:
            perm_attrs["project_value"] = project_value
            v = _parse_value(project_value)
            if v is not None:
                perm_attrs["project_value_numeric"] = v
        if issue_date:
            perm_attrs["issue_date"] = issue_date
        if application_date:
            perm_attrs["application_date"] = application_date
        if description:
            perm_attrs["description"] = description[:500]
        if local_area:
            perm_attrs["local_area"] = local_area
        if city:
            perm_attrs["city"] = city

        permit, created = self.repo.put_entity(
            BizEntityKind.PERMIT,
            permit_name,
            attributes=perm_attrs,
            source=self.source_tag,
            write_history=self.write_history,
        )
        if created:
            result.entities_created += 1
        else:
            result.entities_updated += 1

        # City location for permit
        permit_city = city or local_area
        if permit_city:
            city_ent, c = self.repo.put_entity(
                BizEntityKind.CITY,
                permit_city,
                source=self.source_tag,
                write_history=False,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1
            _, rc = self.repo.put_relation(
                permit.uid,
                BizRelationKind.IN_CITY,
                city_ent.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

        # ── Applicant company ──────────────────────────────────────────────
        if applicant:
            app_name = self._extract_company_name(applicant)
            if app_name:
                person_name = self._extract_person_name(applicant)
                app_attrs: dict = {}
                if city:
                    app_attrs["city"] = city
                if local_area:
                    app_attrs["local_area"] = local_area
                if person_name:
                    app_attrs["contact_person"] = person_name

                app_co, c = self.repo.put_entity(
                    BizEntityKind.COMPANY,
                    app_name,
                    attributes=app_attrs,
                    source=self.source_tag,
                    write_history=self.write_history,
                )
                if c:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                _, rc = self.repo.put_relation(
                    app_co.uid,
                    BizRelationKind.APPLIED_FOR,
                    permit.uid,
                    source=self.source_tag,
                    valid_from=application_date or issue_date or None,
                )
                if rc:
                    result.relations_created += 1
                else:
                    result.relations_updated += 1

                if city:
                    self._link_city(app_co, city, result)

        # ── Contractor company ─────────────────────────────────────────────
        if contractor and contractor.lower() != (applicant or "").lower():
            con_name = self._extract_company_name(contractor)
            if con_name:
                con_attrs: dict = {}
                if city:
                    con_attrs["city"] = city

                con_co, c = self.repo.put_entity(
                    BizEntityKind.COMPANY,
                    con_name,
                    attributes=con_attrs,
                    source=self.source_tag,
                    write_history=self.write_history,
                )
                if c:
                    result.entities_created += 1
                else:
                    result.entities_updated += 1

                _, rc = self.repo.put_relation(
                    con_co.uid,
                    BizRelationKind.CONTRACTED_FOR,
                    permit.uid,
                    source=self.source_tag,
                    valid_from=issue_date or None,
                )
                if rc:
                    result.relations_created += 1
                else:
                    result.relations_updated += 1

                if city:
                    self._link_city(con_co, city, result)

    # ── Helpers ────────────────────────────────────────────────────────────────

    def _link_location(self, entity, location: str, result: ImportResult) -> None:
        """Parse a location string and create CTY/PRV entities + relations."""
        cities, provinces = _parse_location(location)
        for city_name in cities:
            self._link_city(entity, city_name, result)
        for prov_name in provinces:
            prov, c = self.repo.put_entity(
                BizEntityKind.PROVINCE,
                prov_name,
                source=self.source_tag,
                write_history=False,
            )
            if c:
                result.entities_created += 1
            else:
                result.entities_updated += 1
            _, rc = self.repo.put_relation(
                entity.uid,
                BizRelationKind.IN_PROVINCE,
                prov.uid,
                source=self.source_tag,
            )
            if rc:
                result.relations_created += 1
            else:
                result.relations_updated += 1

    def _link_city(self, entity, city_name: str, result: ImportResult) -> None:
        city_ent, c = self.repo.put_entity(
            BizEntityKind.CITY,
            city_name,
            source=self.source_tag,
            write_history=False,
        )
        if c:
            result.entities_created += 1
        else:
            result.entities_updated += 1
        _, rc = self.repo.put_relation(
            entity.uid,
            BizRelationKind.IN_CITY,
            city_ent.uid,
            source=self.source_tag,
        )
        if rc:
            result.relations_created += 1
        else:
            result.relations_updated += 1

    @staticmethod
    def _extract_company_name(raw: str) -> str:
        """
        Many permit applicant fields are "Person Name DBA: Company Name Ltd."
        or just "Company Name Ltd."  or just "Person Name".

        Return the company/DBA portion if present; otherwise the full string
        (treating the applicant as the company name for dedup purposes).
        """
        dba_match = re.search(r"DBA:\s*(.+)$", raw, re.IGNORECASE)
        if dba_match:
            return dba_match.group(1).strip()
        return raw.strip()

    @staticmethod
    def _extract_person_name(raw: str) -> str:
        """Return the person portion of 'Person DBA: Company' or empty str."""
        dba_match = re.search(r"^(.+?)\s+DBA:", raw, re.IGNORECASE)
        if dba_match:
            return dba_match.group(1).strip()
        return ""

    @staticmethod
    def _read_csv(path: Path) -> list[dict]:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return list(csv.DictReader(f))

    def _make_result(self, suffix: str = "") -> ImportResult:
        name = self.name
        if suffix:
            name = f"{self.name}:{suffix}"
        return ImportResult(importer=name)
