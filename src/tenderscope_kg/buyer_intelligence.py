"""
TenderScope Intelligence Engine — Buyer Intelligence Engine (BIE).

Analyses procurement organisations from the business graph.

Design principles
-----------------
* **Graph-first**: every metric derives purely from existing graph edges —
  no external data, no hard-coded assumptions.
* **Explainable**: every result includes ``evidence`` — the graph triples
  (entity, relation, entity) that justify each figure.
* **Read-only**: no writes, no schema changes.  Runs on top of BizRepository.
* **Composable**: each method returns an independent dict; the combined
  profile is assembled by ``buyer_profile``.
* **Confidence-scored**: numeric confidence accompanies every metric.

Public API
----------
  buyer_profile(uid)              → full buyer profile (all sub-queries)
  buyer_summary(uid)              → lightweight summary + key stats
  supplier_roster(uid)            → all suppliers with award counts + win rates
  preferred_suppliers(uid)        → suppliers consistently chosen (loyalty score)
  supplier_loyalty(uid)           → loyalty index per supplier
  supplier_diversity(uid)         → supplier diversity score (HHI-based)
  buying_patterns(uid)            → timing, frequency, size patterns
  procurement_seasonality(uid)    → tender distribution by month/quarter
  preferred_industries(uid)       → industries the buyer procures from most
  preferred_contract_sizes(uid)   → contract-value bucket distribution
  avg_procurement_value(uid)      → average tender/contract value with evidence
  avg_bidder_count(uid)           → average number of bidders per tender
  award_concentration(uid)        → HHI of awards to suppliers
  buyer_competitiveness(uid)      → how competitive the buyer's process is
  buyer_timeline(uid)             → year-by-year procurement timeline
  tender_forecast(uid)            → probability and timing of future tenders
"""

from __future__ import annotations

import datetime
import math
from collections import defaultdict
from typing import Any, Optional

from .domain import BizEntityKind, BizRelationKind
from .repository._base import BizRepository

# ── Constants ─────────────────────────────────────────────────────────────────

_BUYER_ENTITY_KINDS = [BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY]

# Relation kinds that mark a company's participation in a tender
_BID_KINDS = [
    BizRelationKind.AWARDED_TO,
    BizRelationKind.SUBMITTED_BID,
    BizRelationKind.PARTICIPATED_IN,
]

_AWARDED_KIND = BizRelationKind.AWARDED_TO
_ISSUED_BY = BizRelationKind.ISSUED_BY  # tender → org
_ISSUES = BizRelationKind.ISSUES  # org → tender (reverse)

_MONTH_NAMES = [
    "",
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
]


# ── Internal helpers ──────────────────────────────────────────────────────────


def _ev(entity_uid: str, rel_kind: str, target_uid: str, entity_name: str = "", target_name: str = "") -> dict:
    """Build a single evidence triple."""
    return {
        "entity_uid": entity_uid,
        "entity_name": entity_name,
        "relation": rel_kind,
        "target_uid": target_uid,
        "target_name": target_name,
    }


def _confidence(evidence_count: int, base: float = 0.3, scale: float = 0.07, cap_at: int = 10) -> float:
    """Confidence grows with evidence volume, capped at 1.0."""
    return round(min(1.0, base + scale * min(cap_at, evidence_count)), 4)


def _parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except (ValueError, TypeError):
        return None


def _parse_month(date_str: Optional[str]) -> Optional[int]:
    if not date_str or len(str(date_str)) < 7:
        return None
    try:
        return int(str(date_str)[5:7])
    except (ValueError, TypeError):
        return None


def _hhi(counts: list[float]) -> float:
    """Herfindahl–Hirschman Index (0–1 scale; 1 = pure monopoly)."""
    total = sum(counts)
    if total == 0:
        return 0.0
    shares = [c / total for c in counts]
    return round(sum(s * s for s in shares), 4)


def _safe_float(val: Any) -> Optional[float]:
    """Coerce a value to float, returning None if not possible."""
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _require_entity(repo: BizRepository, uid: str, allowed_kinds: Optional[list] = None) -> Optional[dict]:
    entity = repo.get(uid)
    if not entity:
        return {"error": f"Entity not found: {uid}"}
    if allowed_kinds and entity.kind not in allowed_kinds:
        kinds_str = ", ".join(k.value for k in allowed_kinds)
        return {"error": f"{uid} has kind '{entity.kind.value}', expected one of: {kinds_str}"}
    return None


# ── Graph traversal helpers ───────────────────────────────────────────────────


def _buyer_tenders(repo: BizRepository, buyer_uid: str) -> list[dict]:
    """
    Return all tenders issued by this buyer.
    Each result is a dict with uid, name, and all attributes.
    """
    results = []
    # Tenders store ISSUED_BY pointing outward to org → get reverse (in) direction
    for rel, ent in repo.get_neighbors(buyer_uid, direction="in", kinds=[_ISSUED_BY], limit=5000):
        if ent.kind == BizEntityKind.TENDER:
            results.append(
                {
                    "uid": ent.uid,
                    "name": ent.name,
                    "attributes": ent.attributes,
                    "relation_kind": rel.kind.value,
                }
            )
    return results


def _tender_participants(repo: BizRepository, tender_uid: str) -> list[dict]:
    """
    Return all companies connected to a tender (bidders, winners, participants).
    """
    results = []
    for rel, ent in repo.get_neighbors(tender_uid, direction="in", kinds=_BID_KINDS, limit=500):
        if ent.kind == BizEntityKind.COMPANY:
            results.append(
                {
                    "uid": ent.uid,
                    "name": ent.name,
                    "role": rel.kind.value,
                    "attributes": ent.attributes,
                }
            )
    return results


def _tender_winner(repo: BizRepository, tender_uid: str) -> Optional[dict]:
    """Return the awarded company dict, or None."""
    for rel, ent in repo.get_neighbors(tender_uid, direction="in", kinds=[BizRelationKind.AWARDED_TO], limit=5):
        if ent.kind == BizEntityKind.COMPANY:
            return {"uid": ent.uid, "name": ent.name, "attributes": ent.attributes}
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Engine
# ═════════════════════════════════════════════════════════════════════════════


class BuyerIntelligenceEngine:
    """
    Read-only procurement analysis layer on top of BizRepository.

    All methods return plain dicts (JSON-serialisable).
    All methods include an ``evidence`` list for explainability.
    """

    def __init__(self, repo: BizRepository) -> None:
        self._repo = repo

    # ── Full profile ──────────────────────────────────────────────────────────

    def buyer_profile(self, uid: str) -> dict:
        """
        Assemble every buyer intelligence dimension into one response.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        return {
            "uid": uid,
            "name": entity.name,
            "kind": entity.kind.value,
            "summary": self.buyer_summary(uid),
            "supplier_roster": self.supplier_roster(uid),
            "preferred_suppliers": self.preferred_suppliers(uid),
            "supplier_loyalty": self.supplier_loyalty(uid),
            "supplier_diversity": self.supplier_diversity(uid),
            "buying_patterns": self.buying_patterns(uid),
            "procurement_seasonality": self.procurement_seasonality(uid),
            "preferred_industries": self.preferred_industries(uid),
            "preferred_contract_sizes": self.preferred_contract_sizes(uid),
            "avg_procurement_value": self.avg_procurement_value(uid),
            "avg_bidder_count": self.avg_bidder_count(uid),
            "award_concentration": self.award_concentration(uid),
            "buyer_competitiveness": self.buyer_competitiveness(uid),
            "buyer_timeline": self.buyer_timeline(uid),
            "tender_forecast": self.tender_forecast(uid),
        }

    # ── Summary ───────────────────────────────────────────────────────────────

    def buyer_summary(self, uid: str) -> dict:
        """
        Lightweight summary: total tenders, active suppliers, award HHI,
        competitiveness score, and top industry.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)
        total_tenders = len(tenders)

        # Winner counts
        winner_counts: dict[str, int] = defaultdict(int)
        winner_names: dict[str, str] = {}
        for t in tenders:
            w = _tender_winner(self._repo, t["uid"])
            if w:
                winner_counts[w["uid"]] += 1
                winner_names[w["uid"]] = w["name"]

        active_suppliers = len(winner_counts)
        hhi = _hhi(list(winner_counts.values()))

        # Top supplier
        top_supplier = None
        if winner_counts:
            top_uid = max(winner_counts, key=lambda k: winner_counts[k])
            top_supplier = {
                "uid": top_uid,
                "name": winner_names[top_uid],
                "award_count": winner_counts[top_uid],
            }

        evidence = [_ev(uid, "issued_by", t["uid"], entity.name, t["name"]) for t in tenders[:10]]

        return {
            "uid": uid,
            "name": entity.name,
            "kind": entity.kind.value,
            "total_tenders": total_tenders,
            "active_suppliers": active_suppliers,
            "award_hhi": hhi,
            "top_supplier": top_supplier,
            "evidence": evidence,
            "confidence": _confidence(total_tenders),
        }

    # ── Supplier roster ───────────────────────────────────────────────────────

    def supplier_roster(self, uid: str, limit: int = 100) -> dict:
        """
        All companies that have ever won a tender from this buyer,
        with award counts, win rates, and evidence.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        # per supplier: wins, total bids
        sup_wins: dict[str, int] = defaultdict(int)
        sup_bids: dict[str, int] = defaultdict(int)
        sup_names: dict[str, str] = {}
        evidence: list[dict] = []

        for t in tenders:
            participants = _tender_participants(self._repo, t["uid"])
            for p in participants:
                sup_bids[p["uid"]] += 1
                sup_names[p["uid"]] = p["name"]
                if p["role"] == BizRelationKind.AWARDED_TO.value:
                    sup_wins[p["uid"]] += 1
                    evidence.append(_ev(t["uid"], "awarded_to", p["uid"], t["name"], p["name"]))

        suppliers = []
        for s_uid in set(sup_wins) | set(sup_bids):
            wins = sup_wins.get(s_uid, 0)
            bids = sup_bids.get(s_uid, 0)
            win_rt = round(wins / bids, 4) if bids else 0.0
            suppliers.append(
                {
                    "uid": s_uid,
                    "name": sup_names[s_uid],
                    "award_count": wins,
                    "bid_count": bids,
                    "win_rate": win_rt,
                    "confidence": _confidence(wins),
                }
            )

        suppliers.sort(key=lambda x: x["award_count"], reverse=True)
        return {
            "uid": uid,
            "name": entity.name,
            "supplier_count": len(suppliers),
            "suppliers": suppliers[:limit],
            "evidence": evidence[:20],
            "confidence": _confidence(len(tenders)),
        }

    # ── Preferred suppliers ───────────────────────────────────────────────────

    def preferred_suppliers(self, uid: str, min_awards: int = 2, limit: int = 30) -> dict:
        """
        Suppliers chosen at least ``min_awards`` times — the buyer's
        «go-to» suppliers.  Ranked by award count.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        roster_r = self.supplier_roster(uid, limit=5000)
        tenders = _buyer_tenders(self._repo, uid)
        len(tenders)

        preferred = [s for s in roster_r["suppliers"] if s["award_count"] >= min_awards]

        evidence = [_ev(uid, "prefers_supplier", s["uid"], entity.name, s["name"]) for s in preferred[:20]]

        return {
            "uid": uid,
            "name": entity.name,
            "preferred_supplier_count": len(preferred),
            "preferred_suppliers": preferred[:limit],
            "evidence": evidence,
            "confidence": _confidence(len(preferred)),
        }

    # ── Supplier loyalty ──────────────────────────────────────────────────────

    def supplier_loyalty(self, uid: str, limit: int = 30) -> dict:
        """
        Loyalty index per supplier: how consistently does this buyer
        return to the same supplier?

        loyalty_index = award_count / total_tenders  (0–1)

        High loyalty → buyer repeatedly picks the same supplier.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)
        total = max(len(tenders), 1)

        win_counts: dict[str, int] = defaultdict(int)
        win_names: dict[str, str] = {}
        evidence: list[dict] = []

        for t in tenders:
            w = _tender_winner(self._repo, t["uid"])
            if w:
                win_counts[w["uid"]] += 1
                win_names[w["uid"]] = w["name"]
                evidence.append(_ev(uid, "awarded_to_supplier", w["uid"], entity.name, w["name"]))

        loyalty_list = []
        for s_uid, count in win_counts.items():
            loyalty_index = round(count / total, 4)
            loyalty_list.append(
                {
                    "uid": s_uid,
                    "name": win_names[s_uid],
                    "award_count": count,
                    "loyalty_index": loyalty_index,
                    "confidence": _confidence(count),
                }
            )

        loyalty_list.sort(key=lambda x: x["loyalty_index"], reverse=True)

        # overall loyalty: how concentrated are awards?
        hhi = _hhi(list(win_counts.values()))
        overall_loyalty = round(math.sqrt(hhi), 4)  # sqrt(HHI) for 0-1 range

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": len(tenders),
            "unique_suppliers_awarded": len(win_counts),
            "overall_loyalty_score": overall_loyalty,
            "loyalty_interpretation": (
                "high" if overall_loyalty >= 0.5 else "medium" if overall_loyalty >= 0.25 else "low"
            ),
            "supplier_loyalty": loyalty_list[:limit],
            "evidence": evidence[:20],
            "confidence": _confidence(len(tenders)),
        }

    # ── Supplier diversity ────────────────────────────────────────────────────

    def supplier_diversity(self, uid: str) -> dict:
        """
        Supplier diversity score (0–1, higher = more diverse).

        diversity = 1 − HHI(award_counts)

        A buyer with HHI = 0 (perfectly distributed) scores 1.0.
        A monopoly buyer scores 0.0.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        win_counts: dict[str, int] = defaultdict(int)
        win_names: dict[str, str] = {}
        evidence: list[dict] = []

        for t in tenders:
            w = _tender_winner(self._repo, t["uid"])
            if w:
                win_counts[w["uid"]] += 1
                win_names[w["uid"]] = w["name"]
                evidence.append(_ev(uid, "awarded", w["uid"], entity.name, w["name"]))

        hhi = _hhi(list(win_counts.values()))
        diversity = round(1.0 - hhi, 4)

        return {
            "uid": uid,
            "name": entity.name,
            "total_awards": sum(win_counts.values()),
            "unique_suppliers": len(win_counts),
            "award_hhi": hhi,
            "diversity_score": diversity,
            "diversity_level": (
                "high"
                if diversity >= 0.75
                else "medium"
                if diversity >= 0.50
                else "low"
                if diversity >= 0.25
                else "very_low"
            ),
            "evidence": evidence[:20],
            "confidence": _confidence(len(tenders)),
        }

    # ── Buying patterns ───────────────────────────────────────────────────────

    def buying_patterns(self, uid: str) -> dict:
        """
        Temporal and structural buying patterns:
        - average time between tenders (cadence)
        - average bidder count
        - average contract value
        - most common month / quarter
        - busiest year
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        years: list[int] = []
        months: list[int] = []
        values: list[float] = []
        bidder_counts: list[int] = []
        evidence: list[dict] = []

        for t in tenders:
            attrs = t["attributes"]
            yr = _parse_year(attrs.get("valid_from") or attrs.get("date"))
            mo = _parse_month(attrs.get("valid_from") or attrs.get("date"))
            val = _safe_float(attrs.get("value") or attrs.get("contract_value") or attrs.get("estimated_value"))
            parts = _tender_participants(self._repo, t["uid"])

            if yr:
                years.append(yr)
            if mo:
                months.append(mo)
            if val is not None:
                values.append(val)
            bidder_counts.append(len(parts))
            evidence.append(_ev(uid, "issued", t["uid"], entity.name, t["name"]))

        total = len(tenders)

        # Most common month
        month_freq: dict[int, int] = defaultdict(int)
        for m in months:
            month_freq[m] += 1
        peak_month = max(month_freq, key=lambda k: month_freq[k]) if month_freq else None

        # Busiest year
        year_freq: dict[int, int] = defaultdict(int)
        for y in years:
            year_freq[y] += 1
        busiest_year = max(year_freq, key=lambda k: year_freq[k]) if year_freq else None

        avg_value = round(sum(values) / len(values), 2) if values else None
        avg_bidders = round(sum(bidder_counts) / total, 2) if total else 0.0

        # Cadence: avg months between tenders (using years with data)
        cadence_months = None
        if len(years) >= 2:
            span_years = max(years) - min(years)
            if span_years > 0:
                cadence_months = round((span_years * 12) / (total - 1), 1)

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": total,
            "avg_value": avg_value,
            "avg_bidder_count": avg_bidders,
            "peak_month": peak_month,
            "peak_month_name": _MONTH_NAMES[peak_month] if peak_month else None,
            "busiest_year": busiest_year,
            "cadence_months": cadence_months,
            "year_span": (max(years) - min(years)) if len(years) >= 2 else 0,
            "first_year": min(years) if years else None,
            "last_year": max(years) if years else None,
            "evidence": evidence[:10],
            "confidence": _confidence(total),
        }

    # ── Procurement seasonality ───────────────────────────────────────────────

    def procurement_seasonality(self, uid: str) -> dict:
        """
        Distribution of tenders by month and quarter.
        Returns monthly counts and a seasonality index for each month.

        seasonality_index = (month_share − uniform_share) / uniform_share
        Positive = above-average activity that month; negative = below.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        month_counts: dict[int, int] = defaultdict(int)
        evidence: list[dict] = []

        for t in tenders:
            attrs = t["attributes"]
            mo = _parse_month(attrs.get("valid_from") or attrs.get("date"))
            if mo:
                month_counts[mo] += 1
            evidence.append(_ev(uid, "issued", t["uid"], entity.name, t["name"]))

        total_dated = sum(month_counts.values())
        uniform = total_dated / 12 if total_dated else 0

        monthly = []
        for m in range(1, 13):
            count = month_counts.get(m, 0)
            share = round(count / total_dated, 4) if total_dated else 0.0
            idx = round((count - uniform) / uniform, 4) if uniform else 0.0
            monthly.append(
                {
                    "month": m,
                    "month_name": _MONTH_NAMES[m],
                    "count": count,
                    "share": share,
                    "seasonality_index": idx,
                }
            )

        # Quarter rollup
        quarters = {}
        for q, ms in {1: [1, 2, 3], 2: [4, 5, 6], 3: [7, 8, 9], 4: [10, 11, 12]}.items():
            q_count = sum(month_counts.get(m, 0) for m in ms)
            quarters[f"Q{q}"] = {
                "quarter": q,
                "count": q_count,
                "share": round(q_count / total_dated, 4) if total_dated else 0.0,
            }

        peak_month = max(monthly, key=lambda x: x["count"]) if monthly else None
        peak_quarter = max(quarters.values(), key=lambda x: x["count"]) if quarters else None

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": len(tenders),
            "tenders_with_dates": total_dated,
            "monthly": monthly,
            "quarterly": quarters,
            "peak_month": peak_month,
            "peak_quarter": peak_quarter,
            "evidence": evidence[:10],
            "confidence": _confidence(total_dated),
        }

    # ── Preferred industries ──────────────────────────────────────────────────

    def preferred_industries(self, uid: str, limit: int = 20) -> dict:
        """
        Industries this buyer procures from most frequently.

        Derived from: winning companies → IN_INDUSTRY → industry node.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        industry_counts: dict[str, int] = defaultdict(int)
        industry_names: dict[str, str] = {}
        evidence: list[dict] = []

        for t in tenders:
            participants = _tender_participants(self._repo, t["uid"])
            for p in participants:
                # each participant → IN_INDUSTRY → industry nodes
                for rel, ind_ent in self._repo.get_neighbors(
                    p["uid"], direction="out", kinds=[BizRelationKind.IN_INDUSTRY], limit=10
                ):
                    if ind_ent.kind == BizEntityKind.INDUSTRY:
                        industry_counts[ind_ent.uid] += 1
                        industry_names[ind_ent.uid] = ind_ent.name
                        evidence.append(_ev(p["uid"], "in_industry", ind_ent.uid, p["name"], ind_ent.name))

        total = sum(industry_counts.values())
        industries = []
        for ind_uid, count in sorted(industry_counts.items(), key=lambda x: x[1], reverse=True):
            industries.append(
                {
                    "uid": ind_uid,
                    "name": industry_names[ind_uid],
                    "count": count,
                    "share": round(count / total, 4) if total else 0.0,
                    "confidence": _confidence(count),
                }
            )

        return {
            "uid": uid,
            "name": entity.name,
            "industry_count": len(industries),
            "industries": industries[:limit],
            "evidence": evidence[:20],
            "confidence": _confidence(len(industries)),
        }

    # ── Preferred contract sizes ──────────────────────────────────────────────

    def preferred_contract_sizes(self, uid: str) -> dict:
        """
        Distribution of tenders by contract-value bucket.

        Buckets: micro (<10K), small (10K–100K), medium (100K–1M),
                 large (1M–10M), mega (>10M).
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        _BUCKETS = [
            ("micro", 0, 10_000),
            ("small", 10_000, 100_000),
            ("medium", 100_000, 1_000_000),
            ("large", 1_000_000, 10_000_000),
            ("mega", 10_000_000, float("inf")),
        ]

        bucket_counts: dict[str, int] = {b[0]: 0 for b in _BUCKETS}
        values: list[float] = []
        evidence: list[dict] = []

        for t in tenders:
            attrs = t["attributes"]
            val = _safe_float(attrs.get("value") or attrs.get("contract_value") or attrs.get("estimated_value"))
            if val is None:
                continue
            values.append(val)
            for name, lo, hi in _BUCKETS:
                if lo <= val < hi:
                    bucket_counts[name] += 1
                    break
            evidence.append(_ev(uid, "issued_tender", t["uid"], entity.name, t["name"]))

        total_with_value = sum(bucket_counts.values())
        buckets = []
        for name, lo, hi in _BUCKETS:
            count = bucket_counts[name]
            buckets.append(
                {
                    "bucket": name,
                    "min_value": lo,
                    "max_value": hi if hi != float("inf") else None,
                    "count": count,
                    "share": round(count / total_with_value, 4) if total_with_value else 0.0,
                }
            )

        preferred = max(buckets, key=lambda x: x["count"]) if buckets else None

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": len(tenders),
            "tenders_with_value": total_with_value,
            "avg_value": round(sum(values) / len(values), 2) if values else None,
            "min_value": min(values) if values else None,
            "max_value": max(values) if values else None,
            "preferred_bucket": preferred["bucket"] if preferred else None,
            "buckets": buckets,
            "evidence": evidence[:10],
            "confidence": _confidence(total_with_value),
        }

    # ── Average procurement value ─────────────────────────────────────────────

    def avg_procurement_value(self, uid: str) -> dict:
        """
        Average, median, min, and max tender/contract value,
        plus total aggregate procurement value.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        values: list[float] = []
        evidence: list[dict] = []

        for t in tenders:
            attrs = t["attributes"]
            val = _safe_float(attrs.get("value") or attrs.get("contract_value") or attrs.get("estimated_value"))
            if val is not None:
                values.append(val)
                evidence.append(_ev(uid, "issued_tender_value", t["uid"], entity.name, t["name"]))

        if not values:
            return {
                "uid": uid,
                "name": entity.name,
                "tenders_with_value": 0,
                "avg_value": None,
                "median_value": None,
                "min_value": None,
                "max_value": None,
                "total_value": None,
                "evidence": [],
                "confidence": 0.3,
            }

        values.sort()
        n = len(values)
        median = values[n // 2] if n % 2 else (values[n // 2 - 1] + values[n // 2]) / 2

        return {
            "uid": uid,
            "name": entity.name,
            "tenders_with_value": n,
            "avg_value": round(sum(values) / n, 2),
            "median_value": round(median, 2),
            "min_value": values[0],
            "max_value": values[-1],
            "total_value": round(sum(values), 2),
            "evidence": evidence[:10],
            "confidence": _confidence(n),
        }

    # ── Average bidder count ──────────────────────────────────────────────────

    def avg_bidder_count(self, uid: str) -> dict:
        """
        Average, min, and max number of bidders per tender.
        Low counts → low competition; high counts → open competitive market.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        counts: list[int] = []
        evidence: list[dict] = []

        for t in tenders:
            parts = _tender_participants(self._repo, t["uid"])
            counts.append(len(parts))
            evidence.append(_ev(uid, "issued", t["uid"], entity.name, t["name"]))

        if not counts:
            return {
                "uid": uid,
                "name": entity.name,
                "total_tenders": 0,
                "avg_bidder_count": 0.0,
                "min_bidder_count": 0,
                "max_bidder_count": 0,
                "single_bidder_tenders": 0,
                "evidence": [],
                "confidence": 0.3,
            }

        avg = round(sum(counts) / len(counts), 2)
        single = sum(1 for c in counts if c <= 1)

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": len(counts),
            "avg_bidder_count": avg,
            "min_bidder_count": min(counts),
            "max_bidder_count": max(counts),
            "single_bidder_tenders": single,
            "single_bidder_rate": round(single / len(counts), 4),
            "evidence": evidence[:10],
            "confidence": _confidence(len(counts)),
        }

    # ── Award concentration ───────────────────────────────────────────────────

    def award_concentration(self, uid: str) -> dict:
        """
        HHI of awards to suppliers.
        High HHI → awards concentrated on few suppliers.
        Low HHI  → diverse supplier base.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        win_counts: dict[str, int] = defaultdict(int)
        win_names: dict[str, str] = {}
        evidence: list[dict] = []

        for t in tenders:
            w = _tender_winner(self._repo, t["uid"])
            if w:
                win_counts[w["uid"]] += 1
                win_names[w["uid"]] = w["name"]
                evidence.append(_ev(uid, "awarded_to", w["uid"], entity.name, w["name"]))

        hhi = _hhi(list(win_counts.values()))
        total_awards = sum(win_counts.values())

        top_suppliers = sorted(
            [
                {
                    "uid": k,
                    "name": win_names[k],
                    "awards": v,
                    "share": round(v / total_awards, 4) if total_awards else 0.0,
                }
                for k, v in win_counts.items()
            ],
            key=lambda x: x["awards"],
            reverse=True,
        )[:10]

        return {
            "uid": uid,
            "name": entity.name,
            "total_awards": total_awards,
            "unique_suppliers": len(win_counts),
            "hhi": hhi,
            "concentration_level": (
                "highly_concentrated" if hhi >= 0.25 else "moderately_concentrated" if hhi >= 0.15 else "competitive"
            ),
            "top_suppliers": top_suppliers,
            "evidence": evidence[:20],
            "confidence": _confidence(total_awards),
        }

    # ── Buyer competitiveness score ───────────────────────────────────────────

    def buyer_competitiveness(self, uid: str) -> dict:
        """
        How competitive is this buyer's procurement process?

        competitiveness = 0.40 × avg_bidder_score
                        + 0.30 × diversity_score
                        + 0.30 × (1 − single_bidder_rate)

        avg_bidder_score = min(1.0, avg_bidders / 5)
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)

        # Sub-components
        bidder_r = self.avg_bidder_count(uid)
        diversity_r = self.supplier_diversity(uid)

        avg_bidders = bidder_r.get("avg_bidder_count", 0) or 0
        single_bidder_rate = bidder_r.get("single_bidder_rate", 0) or 0
        diversity_score = diversity_r.get("diversity_score", 0) or 0

        avg_bidder_score = min(1.0, avg_bidders / 5.0)
        score = round(
            0.40 * avg_bidder_score + 0.30 * diversity_score + 0.30 * (1.0 - single_bidder_rate),
            4,
        )

        level = (
            "highly_competitive" if score >= 0.65 else "moderately_competitive" if score >= 0.40 else "low_competition"
        )

        evidence = [
            _ev(uid, "competitiveness_component", uid, entity.name, f"avg_bidders={avg_bidders}"),
            _ev(uid, "competitiveness_component", uid, entity.name, f"diversity={diversity_score}"),
            _ev(
                uid,
                "competitiveness_component",
                uid,
                entity.name,
                f"single_bidder_rate={single_bidder_rate}",
            ),
        ]

        return {
            "uid": uid,
            "name": entity.name,
            "competitiveness_score": score,
            "competitiveness_level": level,
            "components": {
                "avg_bidder_score": round(avg_bidder_score, 4),
                "diversity_score": round(diversity_score, 4),
                "open_tender_score": round(1.0 - single_bidder_rate, 4),
            },
            "evidence": evidence,
            "confidence": _confidence(bidder_r.get("total_tenders", 0)),
        }

    # ── Buyer timeline ────────────────────────────────────────────────────────

    def buyer_timeline(self, uid: str) -> dict:
        """
        Year-by-year procurement activity: tenders issued, suppliers used,
        unique winners, total value, and top supplier per year.
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        # Bucket tenders by year
        by_year: dict[int, list[dict]] = defaultdict(list)
        undated: list[dict] = []

        for t in tenders:
            attrs = t["attributes"]
            yr = _parse_year(attrs.get("valid_from") or attrs.get("date"))
            if yr:
                by_year[yr].append(t)
            else:
                undated.append(t)

        timeline = []
        for yr in sorted(by_year):
            yr_tenders = by_year[yr]
            yr_suppliers: set[str] = set()
            yr_winners: dict[str, int] = defaultdict(int)
            yr_winner_names: dict[str, str] = {}
            yr_value = 0.0
            yr_value_count = 0

            for t in yr_tenders:
                attrs = t["attributes"]
                val = _safe_float(attrs.get("value") or attrs.get("contract_value") or attrs.get("estimated_value"))
                if val is not None:
                    yr_value += val
                    yr_value_count += 1

                parts = _tender_participants(self._repo, t["uid"])
                for p in parts:
                    yr_suppliers.add(p["uid"])
                    if p["role"] == BizRelationKind.AWARDED_TO.value:
                        yr_winners[p["uid"]] += 1
                        yr_winner_names[p["uid"]] = p["name"]

            top_winner = None
            if yr_winners:
                tw_uid = max(yr_winners, key=lambda k: yr_winners[k])
                top_winner = {
                    "uid": tw_uid,
                    "name": yr_winner_names[tw_uid],
                    "award_count": yr_winners[tw_uid],
                }

            timeline.append(
                {
                    "year": yr,
                    "tender_count": len(yr_tenders),
                    "unique_suppliers": len(yr_suppliers),
                    "unique_winners": len(yr_winners),
                    "total_value": round(yr_value, 2) if yr_value_count else None,
                    "avg_value": round(yr_value / yr_value_count, 2) if yr_value_count else None,
                    "top_winner": top_winner,
                }
            )

        # Trend: compare last 2 years
        trend = "insufficient_data"
        if len(timeline) >= 2:
            last = timeline[-1]["tender_count"]
            prior = timeline[-2]["tender_count"]
            if last > prior * 1.2:
                trend = "growing"
            elif last < prior * 0.8:
                trend = "declining"
            else:
                trend = "stable"

        return {
            "uid": uid,
            "name": entity.name,
            "years_active": len(by_year),
            "total_tenders": len(tenders),
            "undated_tenders": len(undated),
            "trend": trend,
            "timeline": timeline,
            "confidence": _confidence(len(tenders)),
        }

    # ── Tender forecast ───────────────────────────────────────────────────────

    def tender_forecast(self, uid: str) -> dict:
        """
        Estimate probability and likely timing of future tenders.

        Method:
        - Cadence: average months between past tenders.
        - Recency: months since the last tender.
        - Probability = min(1.0, recency / cadence) if cadence > 0 else 0.5 (base rate)
        - Likely next month: last_tender_date + cadence
        """
        err = _require_entity(self._repo, uid, _BUYER_ENTITY_KINDS)
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _buyer_tenders(self._repo, uid)

        # Collect dated tenders
        dated: list[tuple[int, int]] = []  # (year, month)
        for t in tenders:
            attrs = t["attributes"]
            ds = attrs.get("valid_from") or attrs.get("date")
            yr = _parse_year(ds)
            mo = _parse_month(ds)
            if yr and mo:
                dated.append((yr, mo))

        today = datetime.date.today()
        today_ym = today.year * 12 + today.month

        if not dated:
            return {
                "uid": uid,
                "name": entity.name,
                "total_tenders": len(tenders),
                "tenders_with_dates": 0,
                "forecast_probability": 0.5,
                "forecast_basis": "no_dated_tenders",
                "cadence_months": None,
                "last_tender_year": None,
                "last_tender_month": None,
                "months_since_last": None,
                "estimated_next_year": None,
                "estimated_next_month": None,
                "evidence": [],
                "confidence": 0.3,
            }

        dated.sort()
        last_yr, last_mo = dated[-1]
        months_since = today_ym - (last_yr * 12 + last_mo)

        # cadence: average gap between consecutive tenders
        if len(dated) >= 2:
            gaps = []
            for i in range(1, len(dated)):
                prev_ym = dated[i - 1][0] * 12 + dated[i - 1][1]
                curr_ym = dated[i][0] * 12 + dated[i][1]
                gap = curr_ym - prev_ym
                if gap > 0:
                    gaps.append(gap)
            cadence = round(sum(gaps) / len(gaps), 1) if gaps else None
        else:
            cadence = None

        # Probability
        if cadence and cadence > 0:
            prob = round(min(1.0, months_since / cadence), 4)
        else:
            prob = 0.5

        # Estimated next: last + cadence
        next_yr = next_mo = None
        if cadence:
            est_ym = last_yr * 12 + last_mo + round(cadence)
            next_yr = est_ym // 12
            next_mo = est_ym % 12 or 12
            if next_mo == 0:
                next_yr -= 1
                next_mo = 12

        evidence = [_ev(uid, "issued", t["uid"], entity.name, t["name"]) for t in tenders[:10]]

        return {
            "uid": uid,
            "name": entity.name,
            "total_tenders": len(tenders),
            "tenders_with_dates": len(dated),
            "forecast_probability": prob,
            "forecast_basis": "cadence_model" if cadence else "base_rate",
            "cadence_months": cadence,
            "last_tender_year": last_yr,
            "last_tender_month": last_mo,
            "last_tender_month_name": _MONTH_NAMES[last_mo],
            "months_since_last": months_since,
            "estimated_next_year": next_yr,
            "estimated_next_month": next_mo,
            "estimated_next_month_name": _MONTH_NAMES[next_mo] if next_mo else None,
            "evidence": evidence,
            "confidence": _confidence(len(dated)),
        }
