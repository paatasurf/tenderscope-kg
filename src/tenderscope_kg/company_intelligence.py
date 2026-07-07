"""
TenderScope Intelligence Engine — Company Intelligence Engine (CIE).

Produces complete, explainable company profiles by aggregating graph relations.
Nothing is duplicated in storage: every metric is computed from the live graph.

Design principles
-----------------
* **Graph-first**: all metrics are derived from biz_entities + biz_relations.
  No denormalised summary tables.  The same code works after a PostgreSQL/Neo4j
  migration without change.
* **Explainable**: every numeric result ships with an ``evidence`` list of
  (uid, relation_kind, entity_name) triples so callers can trace every figure
  back to raw graph edges.
* **Composable**: each method returns an independent dict.  ``company_profile``
  assembles them all; individual methods can be called cheaply for partial views.
* **Scalable**: neighbour queries use the (source_uid, kind) / (target_uid, kind)
  indexes; no full-table scans.  The BFS traversal used for competitor detection
  is depth-bounded (default 2 hops).
* **Backward compatible**: no existing table, index, or public method is changed.
  The CIE is a pure read-only layer above BizRepository.

Public API
----------
  company_profile(uid)         → complete profile dict (all sub-queries combined)
  company_summary(uid)         → lightweight overview (no timeline, no raw evidence)
  company_stats(uid)           → financial + activity statistics
  company_buyers(uid)          → government / private buyers
  company_competitors(uid)     → 2-hop competitor network with shared evidence
  company_contracts(uid)       → awarded contracts with parsed values
  company_tenders(uid)         → tenders won / submitted / issued-by
  company_timeline(uid)        → chronological activity events
  company_locations(uid)       → cities, provinces, addresses
  company_industries(uid)      → industry + category associations

Graph traversal
---------------
  top_competitors(limit)       → companies sorted by shared-buyer count
  companies_by_city(city)      → all companies in a city
  companies_by_province(prov)  → all companies in a province
  most_connected_companies(n)  → companies ranked by total edge count
"""
from __future__ import annotations

import re
from collections import defaultdict
from typing import Any, Optional

from .domain import BizEntity, BizEntityKind, BizRelationKind
from .repository._base import BizRepository


# ── helpers ────────────────────────────────────────────────────────────────────

def _parse_value(raw: Any) -> Optional[float]:
    """Parse 'CAD 273,936.60' or 500000.0 → float, else None."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    s = re.sub(r"[^\d.]", "", str(raw))
    try:
        return float(s) if s else None
    except ValueError:
        return None


def _evidence(rel_kind: str, entity: BizEntity) -> dict:
    return {
        "uid": entity.uid,
        "kind": entity.kind.value,
        "name": entity.name,
        "relation": rel_kind,
    }


def _date_key(d: Optional[str]) -> str:
    """Return d or '' for safe min/max comparisons."""
    return d or ""


# ── main class ─────────────────────────────────────────────────────────────────

class CompanyIntelligenceEngine:
    """
    Aggregates all graph information about a company into explainable profiles.

    All methods are read-only; they never write to the repository.
    """

    def __init__(self, repo: BizRepository) -> None:
        self.repo = repo

    # ══════════════════════════════════════════════════════════════════════════
    # Core profile methods
    # ══════════════════════════════════════════════════════════════════════════

    def company_profile(self, uid: str) -> dict:
        """
        Complete explainable company profile.
        Assembles all sub-queries into one structured response.
        """
        company = self._require_company(uid)
        if "error" in company:
            return company

        ent: BizEntity = company["_entity"]

        return {
            "uid": ent.uid,
            "name": ent.name,
            "kind": ent.kind.value,
            "source": ent.source,
            "confidence": ent.confidence,
            "created_at": ent.created_at,
            "updated_at": ent.updated_at,
            "attributes": ent.attributes,
            # Sub-profiles (all evidence-backed)
            "summary":     self.company_summary(uid),
            "stats":       self.company_stats(uid),
            "buyers":      self.company_buyers(uid),
            "competitors": self.company_competitors(uid),
            "contracts":   self.company_contracts(uid),
            "tenders":     self.company_tenders(uid),
            "timeline":    self.company_timeline(uid),
            "locations":   self.company_locations(uid),
            "industries":  self.company_industries(uid),
        }

    def company_summary(self, uid: str) -> dict:
        """
        Lightweight explainable overview — suitable for list views and MCP responses.
        Includes confidence score with evidence count.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        neighbors = self.repo.get_neighbors(uid, direction="both", limit=500)

        # Collect counts by category
        awarded_tenders: list[BizEntity] = []
        submitted_tenders: list[BizEntity] = []
        issued_tenders: list[BizEntity] = []
        permits: list[BizEntity] = []
        buyers: set[str] = set()
        locations: set[str] = set()
        industries: set[str] = set()

        for rel, nb in neighbors:
            rk = rel.kind
            if rk == BizRelationKind.AWARDED_TO and nb.kind == BizEntityKind.TENDER:
                awarded_tenders.append(nb)
            elif rk == BizRelationKind.SUBMITTED_BID and nb.kind == BizEntityKind.TENDER:
                submitted_tenders.append(nb)
            elif rk == BizRelationKind.ISSUED_BY and nb.kind == BizEntityKind.ORGANIZATION:
                issued_tenders.append(nb)
            elif rk in (BizRelationKind.APPLIED_FOR, BizRelationKind.CONTRACTED_FOR):
                if nb.kind == BizEntityKind.PERMIT:
                    permits.append(nb)
            elif rk == BizRelationKind.AWARDED_TO and nb.kind == BizEntityKind.ORGANIZATION:
                buyers.add(nb.uid)
            elif nb.kind == BizEntityKind.ORGANIZATION:
                buyers.add(nb.uid)
            elif nb.kind in (BizEntityKind.CITY, BizEntityKind.PROVINCE):
                locations.add(nb.name)
            elif nb.kind == BizEntityKind.INDUSTRY:
                industries.add(nb.name)

        # Compute financials
        total_value = 0.0
        values = []
        for nb in awarded_tenders:
            v = _parse_value(nb.attributes.get("contract_value") or
                             nb.attributes.get("estimated_value") or
                             nb.attributes.get("project_value"))
            if v:
                total_value += v
                values.append(v)

        # Evidence count = number of unique graph edges
        evidence_count = len(neighbors)

        # Confidence: 1.0 if we have at least 3 direct edges, else scaled
        confidence = min(1.0, 0.3 + 0.07 * min(10, evidence_count))

        # Activity window
        dates = []
        for _, nb in neighbors:
            for attr in ("award_date", "posted_date", "issue_date", "application_date"):
                d = nb.attributes.get(attr)
                if d:
                    dates.append(d)
        if ent.created_at:
            dates.append(ent.created_at[:10])

        first_activity = min(dates) if dates else None
        latest_activity = max(dates) if dates else None

        return {
            "uid": ent.uid,
            "name": ent.name,
            "source": ent.source,
            "confidence_score": round(confidence, 3),
            "evidence_count": evidence_count,
            "tenders_won": len(awarded_tenders),
            "tenders_submitted": len(submitted_tenders),
            "permits": len(permits),
            "unique_buyers": len(buyers),
            "locations": sorted(locations),
            "industries": sorted(industries),
            "total_awarded_value": round(total_value, 2),
            "average_contract_value": round(total_value / len(values), 2) if values else 0.0,
            "largest_contract": round(max(values), 2) if values else 0.0,
            "first_activity": first_activity,
            "latest_activity": latest_activity,
        }

    def company_stats(self, uid: str) -> dict:
        """
        Financial + activity statistics with full evidence references.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        # Fetch all outbound + inbound contract/permit relations
        award_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[
                BizRelationKind.AWARDED_TO,
                BizRelationKind.APPLIED_FOR,
                BizRelationKind.CONTRACTED_FOR,
                BizRelationKind.SUBMITTED_BID,
                BizRelationKind.HAS_CONTRACT,
            ],
            limit=1000,
        )

        contract_values: list[float] = []
        permit_values: list[float] = []
        award_dates: list[str] = []
        permit_dates: list[str] = []
        evidence: list[dict] = []

        for rel, nb in award_rels:
            ev = _evidence(rel.kind.value, nb)
            evidence.append(ev)
            if nb.kind == BizEntityKind.TENDER:
                v = _parse_value(
                    rel.attributes.get("contract_value") or
                    nb.attributes.get("contract_value") or
                    nb.attributes.get("estimated_value")
                )
                if v:
                    contract_values.append(v)
                d = (
                    rel.attributes.get("award_date") or
                    nb.attributes.get("award_date") or
                    rel.valid_from
                )
                if d:
                    award_dates.append(d)
            elif nb.kind == BizEntityKind.PERMIT:
                v = _parse_value(nb.attributes.get("project_value_numeric") or
                                 nb.attributes.get("project_value"))
                if v:
                    permit_values.append(v)
                d = nb.attributes.get("issue_date") or nb.attributes.get("application_date")
                if d:
                    permit_dates.append(d)

        # Yearly breakdown
        yearly: dict[str, dict] = defaultdict(lambda: {"contract_count": 0, "contract_value": 0.0,
                                                         "permit_count": 0, "permit_value": 0.0})
        for rel, nb in award_rels:
            if nb.kind == BizEntityKind.TENDER:
                d = rel.attributes.get("award_date") or nb.attributes.get("award_date") or rel.valid_from
                year = d[:4] if d and len(d) >= 4 else "unknown"
                yearly[year]["contract_count"] += 1
                v = _parse_value(rel.attributes.get("contract_value") or nb.attributes.get("contract_value"))
                if v:
                    yearly[year]["contract_value"] += v
            elif nb.kind == BizEntityKind.PERMIT:
                d = nb.attributes.get("issue_date") or nb.attributes.get("application_date")
                year = d[:4] if d and len(d) >= 4 else "unknown"
                yearly[year]["permit_count"] += 1
                v = _parse_value(nb.attributes.get("project_value_numeric") or nb.attributes.get("project_value"))
                if v:
                    yearly[year]["permit_value"] += v

        all_values = contract_values + permit_values
        all_dates = award_dates + permit_dates

        return {
            "uid": uid,
            "contract_count": len(contract_values),
            "total_contract_value": round(sum(contract_values), 2),
            "average_contract_value": round(sum(contract_values) / len(contract_values), 2) if contract_values else 0.0,
            "largest_contract": round(max(contract_values), 2) if contract_values else 0.0,
            "smallest_contract": round(min(contract_values), 2) if contract_values else 0.0,
            "permit_count": len(permit_values),
            "total_permit_value": round(sum(permit_values), 2),
            "total_value": round(sum(all_values), 2),
            "first_activity": min(all_dates) if all_dates else None,
            "latest_activity": max(all_dates) if all_dates else None,
            "yearly_stats": {
                yr: {
                    "contract_count": v["contract_count"],
                    "contract_value": round(v["contract_value"], 2),
                    "permit_count": v["permit_count"],
                    "permit_value": round(v["permit_value"], 2),
                }
                for yr, v in sorted(yearly.items())
            },
            "evidence": evidence[:50],  # cap for response size
            "evidence_count": len(evidence),
        }

    def company_buyers(self, uid: str) -> dict:
        """
        Organizations that issued tenders the company won or bid on.
        Each buyer includes which tenders link them.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        # Step 1: collect tenders the company is connected to
        tender_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
            limit=500,
        )
        tender_uids = {nb.uid: nb for _, nb in tender_rels if nb.kind == BizEntityKind.TENDER}

        # Step 2: for each tender, find its issuing org
        buyer_map: dict[str, dict] = {}  # org_uid → {org, tenders}
        for tender_uid, tender in tender_uids.items():
            org_rels = self.repo.get_neighbors(
                tender_uid, direction="out",
                kinds=[BizRelationKind.ISSUED_BY],
                limit=10,
            )
            for rel, org in org_rels:
                if org.kind == BizEntityKind.ORGANIZATION:
                    if org.uid not in buyer_map:
                        buyer_map[org.uid] = {
                            "uid": org.uid,
                            "name": org.name,
                            "kind": org.kind.value,
                            "tenders": [],
                        }
                    buyer_map[org.uid]["tenders"].append({
                        "uid": tender.uid,
                        "name": tender.name,
                        "evidence_path": f"{uid} → awarded_to → {tender_uid} → issued_by → {org.uid}",
                    })

        # Step 3: also check direct AWARDED_BY relations
        direct_buyers = self.repo.get_neighbors(
            uid, direction="in",
            kinds=[BizRelationKind.AWARDED_BY, BizRelationKind.ISSUES],
            limit=200,
        )
        for rel, org in direct_buyers:
            if org.kind == BizEntityKind.ORGANIZATION and org.uid not in buyer_map:
                buyer_map[org.uid] = {
                    "uid": org.uid,
                    "name": org.name,
                    "kind": org.kind.value,
                    "tenders": [],
                    "evidence_path": f"{org.uid} → {rel.kind.value} → {uid}",
                }

        buyers = sorted(buyer_map.values(), key=lambda b: -len(b.get("tenders", [])))
        return {
            "uid": uid,
            "company": ent.name,
            "buyers": buyers,
            "buyer_count": len(buyers),
        }

    def company_competitors(self, uid: str, limit: int = 20) -> dict:
        """
        Companies that share buyers or tenders with the given company.
        Each competitor entry shows the shared evidence (buyer or tender).
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        # Collect company's tenders
        tender_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[BizRelationKind.AWARDED_TO, BizRelationKind.SUBMITTED_BID],
            limit=500,
        )
        my_tender_uids = {nb.uid for _, nb in tender_rels if nb.kind == BizEntityKind.TENDER}

        # Collect company's buyers (org UIDs)
        buyer_uids: set[str] = set()
        for tender_uid in my_tender_uids:
            org_rels = self.repo.get_neighbors(
                tender_uid, direction="out",
                kinds=[BizRelationKind.ISSUED_BY],
                limit=10,
            )
            for _, org in org_rels:
                buyer_uids.add(org.uid)

        # For each tender, find other companies that also have AWARDED_TO it
        competitor_evidence: dict[str, dict] = {}  # company_uid → {name, shared_tenders, shared_buyers}
        for tender_uid in my_tender_uids:
            co_rels = self.repo.get_neighbors(
                tender_uid, direction="in",
                kinds=[BizRelationKind.AWARDED_TO],
                limit=50,
            )
            for rel, co in co_rels:
                if co.kind == BizEntityKind.COMPANY and co.uid != uid:
                    if co.uid not in competitor_evidence:
                        competitor_evidence[co.uid] = {
                            "uid": co.uid,
                            "name": co.name,
                            "kind": co.kind.value,
                            "shared_tenders": [],
                            "shared_buyers": [],
                        }
                    tender_ent = self.repo.get(tender_uid)
                    competitor_evidence[co.uid]["shared_tenders"].append({
                        "uid": tender_uid,
                        "name": tender_ent.name if tender_ent else tender_uid,
                        "evidence_path": f"{co.uid} → awarded_to → {tender_uid} ← awarded_to ← {uid}",
                    })

        # For each buyer, find other companies that issued_by the same org
        for buyer_uid in buyer_uids:
            issued_rels = self.repo.get_neighbors(
                buyer_uid, direction="out",
                kinds=[BizRelationKind.ISSUES],
                limit=100,
            )
            issued_tender_uids = {nb.uid for _, nb in issued_rels if nb.kind == BizEntityKind.TENDER}
            for t_uid in issued_tender_uids:
                co_rels = self.repo.get_neighbors(
                    t_uid, direction="in",
                    kinds=[BizRelationKind.AWARDED_TO],
                    limit=50,
                )
                for _, co in co_rels:
                    if co.kind == BizEntityKind.COMPANY and co.uid != uid:
                        if co.uid not in competitor_evidence:
                            competitor_evidence[co.uid] = {
                                "uid": co.uid,
                                "name": co.name,
                                "kind": co.kind.value,
                                "shared_tenders": [],
                                "shared_buyers": [],
                            }
                        buyer_ent = self.repo.get(buyer_uid)
                        bname = buyer_ent.name if buyer_ent else buyer_uid
                        already = any(b.get("uid") == buyer_uid
                                      for b in competitor_evidence[co.uid]["shared_buyers"])
                        if not already:
                            competitor_evidence[co.uid]["shared_buyers"].append({
                                "uid": buyer_uid,
                                "name": bname,
                                "evidence_path": f"{uid} ← issued_by ← {bname} → issues → tender → {co.uid}",
                            })

        # Sort by total shared evidence
        ranked = sorted(
            competitor_evidence.values(),
            key=lambda c: -(len(c["shared_tenders"]) + len(c["shared_buyers"]))
        )[:limit]

        for comp in ranked:
            comp["shared_evidence_count"] = len(comp["shared_tenders"]) + len(comp["shared_buyers"])

        return {
            "uid": uid,
            "company": ent.name,
            "competitors": ranked,
            "competitor_count": len(ranked),
            "analysis": {
                "tenders_analysed": len(my_tender_uids),
                "buyers_analysed": len(buyer_uids),
            },
        }

    def company_contracts(self, uid: str, limit: int = 100) -> dict:
        """
        All awarded contracts with values, dates, and evidence paths.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        award_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[
                BizRelationKind.AWARDED_TO,
                BizRelationKind.HAS_CONTRACT,
            ],
            limit=limit,
        )

        contracts = []
        total = 0.0
        for rel, nb in award_rels:
            if nb.kind not in (BizEntityKind.TENDER, BizEntityKind.CONTRACT):
                continue
            v = _parse_value(
                rel.attributes.get("contract_value") or
                nb.attributes.get("contract_value") or
                nb.attributes.get("estimated_value")
            )
            if v:
                total += v
            contracts.append({
                "uid": nb.uid,
                "name": nb.name,
                "entity_kind": nb.kind.value,
                "relation": rel.kind.value,
                "contract_value": v,
                "award_date": rel.attributes.get("award_date") or nb.attributes.get("award_date") or rel.valid_from,
                "url": nb.attributes.get("url"),
                "source": rel.source,
                "confidence": rel.confidence,
                "evidence_path": f"{uid} → {rel.kind.value} → {nb.uid}",
            })

        contracts.sort(key=lambda c: -(c.get("contract_value") or 0))

        return {
            "uid": uid,
            "company": ent.name,
            "contracts": contracts,
            "contract_count": len(contracts),
            "total_value": round(total, 2),
            "average_value": round(total / len(contracts), 2) if contracts else 0.0,
            "largest_contract": max((c.get("contract_value") or 0) for c in contracts) if contracts else 0.0,
        }

    def company_tenders(self, uid: str, limit: int = 200) -> dict:
        """
        All tenders: won, submitted, and issued-by this company's buyers.
        Each tender includes its buyer org and evidence path.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        all_rels = self.repo.get_neighbors(
            uid, direction="both",
            kinds=[
                BizRelationKind.AWARDED_TO,
                BizRelationKind.SUBMITTED_BID,
                BizRelationKind.PARTICIPATED_IN,
            ],
            limit=limit,
        )

        won: list[dict] = []
        submitted: list[dict] = []

        for rel, nb in all_rels:
            if nb.kind != BizEntityKind.TENDER:
                continue
            entry = {
                "uid": nb.uid,
                "name": nb.name,
                "relation": rel.kind.value,
                "category": nb.attributes.get("category"),
                "posted_date": nb.attributes.get("posted_date"),
                "closing_date": nb.attributes.get("closing_date"),
                "award_date": nb.attributes.get("award_date") or rel.attributes.get("award_date"),
                "contract_value": _parse_value(
                    nb.attributes.get("contract_value") or
                    rel.attributes.get("contract_value")
                ),
                "url": nb.attributes.get("url"),
                "source": rel.source,
                "evidence_path": f"{uid} → {rel.kind.value} → {nb.uid}",
            }
            if rel.kind == BizRelationKind.AWARDED_TO:
                won.append(entry)
            else:
                submitted.append(entry)

        return {
            "uid": uid,
            "company": ent.name,
            "tenders_won": won,
            "tenders_won_count": len(won),
            "tenders_submitted": submitted,
            "tenders_submitted_count": len(submitted),
            "total_tender_activity": len(won) + len(submitted),
        }

    def company_timeline(self, uid: str) -> dict:
        """
        Chronological activity timeline derived from graph edges.
        Each event names the relation kind, counterpart entity, and a date.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        all_rels = self.repo.get_neighbors(uid, direction="both", limit=1000)

        events: list[dict] = []
        for rel, nb in all_rels:
            # Try to find the best date for this event
            date = None
            if rel.valid_from:
                date = rel.valid_from
            elif nb.kind == BizEntityKind.TENDER:
                date = (nb.attributes.get("award_date") or
                        nb.attributes.get("closing_date") or
                        nb.attributes.get("posted_date"))
            elif nb.kind == BizEntityKind.PERMIT:
                date = (nb.attributes.get("issue_date") or
                        nb.attributes.get("application_date"))
            elif nb.kind == BizEntityKind.CONTRACT:
                date = nb.attributes.get("date") or nb.attributes.get("award_date")

            if not date:
                continue  # Skip undated events from timeline

            events.append({
                "date": date[:10] if len(date) >= 10 else date,
                "event_type": rel.kind.value,
                "counterpart_uid": nb.uid,
                "counterpart_name": nb.name,
                "counterpart_kind": nb.kind.value,
                "value": _parse_value(
                    rel.attributes.get("contract_value") or
                    nb.attributes.get("contract_value") or
                    nb.attributes.get("project_value_numeric")
                ),
                "source": rel.source,
                "evidence_path": f"{uid} ↔ {rel.kind.value} ↔ {nb.uid}",
            })

        events.sort(key=lambda e: e["date"])

        # Yearly summary from timeline
        yearly: dict[str, int] = defaultdict(int)
        for e in events:
            year = e["date"][:4] if e["date"] else "unknown"
            yearly[year] += 1

        return {
            "uid": uid,
            "company": ent.name,
            "events": events,
            "event_count": len(events),
            "yearly_activity": dict(sorted(yearly.items())),
            "first_event": events[0]["date"] if events else None,
            "latest_event": events[-1]["date"] if events else None,
        }

    def company_locations(self, uid: str) -> dict:
        """
        All cities, provinces, and addresses associated with a company.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        loc_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[
                BizRelationKind.IN_CITY,
                BizRelationKind.IN_PROVINCE,
                BizRelationKind.HAS_ADDRESS,
                BizRelationKind.LOCATED_AT,
            ],
            limit=100,
        )

        cities: list[dict] = []
        provinces: list[dict] = []
        addresses: list[dict] = []

        for rel, nb in loc_rels:
            entry = {
                "uid": nb.uid,
                "name": nb.name,
                "relation": rel.kind.value,
                "source": rel.source,
                "evidence_path": f"{uid} → {rel.kind.value} → {nb.uid}",
            }
            if nb.kind == BizEntityKind.CITY:
                cities.append(entry)
            elif nb.kind == BizEntityKind.PROVINCE:
                provinces.append(entry)
            elif nb.kind == BizEntityKind.ADDRESS:
                addresses.append(entry)

        # Also pull from attributes
        attr_city = ent.attributes.get("city") or ent.attributes.get("primary_city")
        attr_province = ent.attributes.get("province") or ent.attributes.get("primary_province")
        attr_address = ent.attributes.get("address") or ent.attributes.get("google_address") or ent.attributes.get("primary_address")

        return {
            "uid": uid,
            "company": ent.name,
            "cities": cities,
            "provinces": provinces,
            "addresses": addresses,
            "attribute_city": attr_city,
            "attribute_province": attr_province,
            "attribute_address": attr_address,
            "location_count": len(cities) + len(provinces) + len(addresses),
        }

    def company_industries(self, uid: str) -> dict:
        """
        Industry and category associations with evidence paths.
        """
        check = self._require_company(uid)
        if "error" in check:
            return check
        ent: BizEntity = check["_entity"]

        # Direct industry relations
        ind_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[BizRelationKind.IN_INDUSTRY, BizRelationKind.HAS_NAICS],
            limit=50,
        )

        industries: list[dict] = []
        for rel, nb in ind_rels:
            industries.append({
                "uid": nb.uid,
                "name": nb.name,
                "kind": nb.kind.value,
                "relation": rel.kind.value,
                "source": rel.source,
                "evidence_path": f"{uid} → {rel.kind.value} → {nb.uid}",
            })

        # Infer industries from won tenders
        tender_rels = self.repo.get_neighbors(
            uid, direction="out",
            kinds=[BizRelationKind.AWARDED_TO],
            limit=200,
        )
        inferred: dict[str, dict] = {}
        for _, tender in tender_rels:
            if tender.kind != BizEntityKind.TENDER:
                continue
            cat = tender.attributes.get("category")
            if cat and cat not in inferred:
                inferred[cat] = {
                    "name": cat,
                    "inferred_from": "tender_category",
                    "evidence_count": 0,
                    "example_tender_uid": tender.uid,
                    "example_tender_name": tender.name[:60],
                }
            if cat:
                inferred[cat]["evidence_count"] += 1

        return {
            "uid": uid,
            "company": ent.name,
            "industries": industries,
            "inferred_categories": list(inferred.values()),
            "industry_count": len(industries),
            "inferred_count": len(inferred),
        }

    # ══════════════════════════════════════════════════════════════════════════
    # Graph traversal queries
    # ══════════════════════════════════════════════════════════════════════════

    def top_competitors(self, limit: int = 20) -> dict:
        """
        Rank companies by the number of distinct buyers they share with others.
        Returns top N pairs sorted by shared-buyer count.
        """
        # For each company, find its buyer set (orgs reachable via tender→issued_by)
        companies = self.repo.find(kind=BizEntityKind.COMPANY, limit=10000)

        company_buyers: dict[str, set[str]] = {}
        for co in companies:
            tender_rels = self.repo.get_neighbors(
                co.uid, direction="out",
                kinds=[BizRelationKind.AWARDED_TO],
                limit=200,
            )
            buyers: set[str] = set()
            for _, tender in tender_rels:
                if tender.kind != BizEntityKind.TENDER:
                    continue
                org_rels = self.repo.get_neighbors(
                    tender.uid, direction="out",
                    kinds=[BizRelationKind.ISSUED_BY],
                    limit=10,
                )
                for _, org in org_rels:
                    buyers.add(org.uid)
            company_buyers[co.uid] = buyers

        # Count unique buyers per company (proxy for market breadth)
        ranked = sorted(
            [(co.uid, co.name, len(bset)) for co, bset in
             zip(companies, [company_buyers[c.uid] for c in companies])],
            key=lambda x: -x[2]
        )[:limit]

        return {
            "top_competitors": [
                {"uid": uid, "name": name, "buyer_count": count}
                for uid, name, count in ranked
            ],
            "count": len(ranked),
        }

    def companies_by_city(self, city: str, limit: int = 100) -> dict:
        """All companies located in the given city."""
        # Find city entity
        cities = self.repo.find(kind=BizEntityKind.CITY, name_like=city, limit=5)
        if not cities:
            return {"city": city, "companies": [], "count": 0, "error": "City not found in graph"}

        results: list[dict] = []
        for city_ent in cities:
            in_city = self.repo.get_neighbors(
                city_ent.uid, direction="in",
                kinds=[BizRelationKind.IN_CITY],
                limit=limit,
            )
            for rel, co in in_city:
                if co.kind == BizEntityKind.COMPANY:
                    results.append({
                        "uid": co.uid,
                        "name": co.name,
                        "source": co.source,
                        "city_uid": city_ent.uid,
                        "city_name": city_ent.name,
                        "evidence_path": f"{co.uid} → in_city → {city_ent.uid}",
                    })

        return {"city": city, "companies": results, "count": len(results)}

    def companies_by_province(self, province: str, limit: int = 200) -> dict:
        """All companies in the given province."""
        provs = self.repo.find(kind=BizEntityKind.PROVINCE, name_like=province, limit=5)
        if not provs:
            return {"province": province, "companies": [], "count": 0, "error": "Province not found in graph"}

        results: list[dict] = []
        for prov_ent in provs:
            in_prov = self.repo.get_neighbors(
                prov_ent.uid, direction="in",
                kinds=[BizRelationKind.IN_PROVINCE],
                limit=limit,
            )
            for rel, co in in_prov:
                if co.kind == BizEntityKind.COMPANY:
                    results.append({
                        "uid": co.uid,
                        "name": co.name,
                        "source": co.source,
                        "province_uid": prov_ent.uid,
                        "province_name": prov_ent.name,
                        "evidence_path": f"{co.uid} → in_province → {prov_ent.uid}",
                    })

        return {"province": province, "companies": results, "count": len(results)}

    def most_connected_companies(self, limit: int = 20) -> dict:
        """
        Companies ranked by total edge count (in + out).
        High connectivity = strong evidence base = higher confidence profiles.
        """
        companies = self.repo.find(kind=BizEntityKind.COMPANY, limit=10000)
        ranked = []
        for co in companies:
            out_edges = len(self.repo.get_neighbors(co.uid, direction="out", limit=10000))
            in_edges = len(self.repo.get_neighbors(co.uid, direction="in", limit=10000))
            ranked.append({
                "uid": co.uid,
                "name": co.name,
                "out_edges": out_edges,
                "in_edges": in_edges,
                "total_edges": out_edges + in_edges,
            })
        ranked.sort(key=lambda x: -x["total_edges"])
        ranked = ranked[:limit]
        return {"companies": ranked, "count": len(ranked)}

    # ══════════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ══════════════════════════════════════════════════════════════════════════

    def _require_company(self, uid: str) -> dict:
        """Fetch entity, validate it is a company. Returns dict with _entity key or error."""
        ent = self.repo.get(uid)
        if not ent:
            return {"error": f"Entity not found: {uid}"}
        if ent.kind != BizEntityKind.COMPANY:
            return {"error": f"{uid} is {ent.kind.value}, expected company"}
        return {"_entity": ent}
