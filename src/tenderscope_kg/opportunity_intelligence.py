"""
TenderScope Intelligence Engine — Opportunity Intelligence Engine (OIE).

Analyses every tender in the graph from the perspective of a given company
and produces executive-quality recommendations explaining whether the company
should pursue it.

Design principles
-----------------
* **Graph-first**: every dimension derives from existing graph edges —
  no external data, no hard-coded business rules beyond scoring weights.
* **Explainable**: every result includes ``evidence`` (graph triples),
  ``assumptions`` (what was inferred), ``weak_evidence`` (thin data),
  ``missing_information`` (gaps), ``reasoning_chain`` (step-by-step),
  and ``confidence`` (0–1).
* **Read-only**: no writes, no schema changes.
* **Composable**: each method returns an independent dict; ``opportunity_profile``
  assembles all of them.
* **Score range**: Opportunity Score is 0–100 (higher = more attractive).

Public API
----------
  opportunity_profile(company_uid, tender_uid)   → full profile (all sub-queries)
  opportunity_score(company_uid, tender_uid)     → 0–100 score + dimension breakdown
  opportunity_recommendation(company_uid, tender_uid) → label + rationale
  opportunity_explain(company_uid, tender_uid)   → full explainability report
  opportunity_timeline(company_uid, tender_uid)  → preparation, urgency, deadline risk
  opportunity_risk(company_uid, tender_uid)      → risk factors + mitigation hints
  portfolio_impact(company_uid, tender_uid)      → revenue, diversification, strategic value
  similar_opportunities(company_uid, tender_uid) → comparable historical tenders
  best_opportunities(company_uid, limit)         → top scored tenders for a company
  executive_summary(company_uid, limit)          → CEO-dashboard structured summary
"""

from __future__ import annotations

import datetime
from collections import defaultdict
from typing import Any, Optional

from .domain import BizEntityKind, BizRelationKind
from .repository._base import BizRepository

# ── Constants ──────────────────────────────────────────────────────────────────

_COMPANY_KINDS = [BizEntityKind.COMPANY]
_BUYER_KINDS = [BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY]
_TENDER_KIND = BizEntityKind.TENDER
_INDUSTRY_KIND = BizEntityKind.INDUSTRY

_BID_KINDS = [
    BizRelationKind.AWARDED_TO,
    BizRelationKind.SUBMITTED_BID,
    BizRelationKind.PARTICIPATED_IN,
]

# Score weights (must sum to 100)
_W_CAPABILITY = 15
_W_BUYER_HISTORY = 15
_W_INDUSTRY_HISTORY = 10
_W_VALUE_FIT = 10
_W_GEO_FIT = 10
_W_COMPETITION = 10
_W_BUYER_ATTRACT = 10
_W_STRATEGIC = 10
_W_WORKLOAD = 5
_W_WIN_PROBABILITY = 5

assert (
    sum(
        [
            _W_CAPABILITY,
            _W_BUYER_HISTORY,
            _W_INDUSTRY_HISTORY,
            _W_VALUE_FIT,
            _W_GEO_FIT,
            _W_COMPETITION,
            _W_BUYER_ATTRACT,
            _W_STRATEGIC,
            _W_WORKLOAD,
            _W_WIN_PROBABILITY,
        ]
    )
    == 100
)

# Contract value buckets (same as BIE)
_VALUE_BUCKETS = [
    ("micro", 0, 25_000),
    ("small", 25_000, 250_000),
    ("medium", 250_000, 2_500_000),
    ("large", 2_500_000, 25_000_000),
    ("mega", 25_000_000, float("inf")),
]

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


# ── Internal helpers ───────────────────────────────────────────────────────────


def _ev(entity_uid: str, rel_kind: str, target_uid: str, entity_name: str = "", target_name: str = "") -> dict:
    return {
        "entity_uid": entity_uid,
        "entity_name": entity_name,
        "relation": rel_kind,
        "target_uid": target_uid,
        "target_name": target_name,
    }


def _confidence(evidence_count: int, base: float = 0.3, scale: float = 0.07, cap_at: int = 10) -> float:
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


def _safe_float(val: Any) -> Optional[float]:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _value_bucket(val: Optional[float]) -> Optional[str]:
    if val is None:
        return None
    for name, lo, hi in _VALUE_BUCKETS:
        if lo <= val < hi:
            return name
    return "mega"


def _hhi(counts: list[float]) -> float:
    total = sum(counts)
    if total == 0:
        return 0.0
    shares = [c / total for c in counts]
    return round(sum(s * s for s in shares), 4)


def _clamp(val: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, val))


def _require_entity(repo: BizRepository, uid: str, allowed_kinds: Optional[list] = None) -> Optional[dict]:
    entity = repo.get(uid)
    if not entity:
        return {"error": f"Entity not found: {uid}"}
    if allowed_kinds and entity.kind not in allowed_kinds:
        kinds_str = ", ".join(k.value for k in allowed_kinds)
        return {"error": f"{uid} has kind '{entity.kind.value}', expected one of: {kinds_str}"}
    return None


# ── Graph traversal helpers ────────────────────────────────────────────────────


def _company_tenders(repo: BizRepository, company_uid: str) -> list[dict]:
    """All tenders a company was connected to (bids, awards, participations)."""
    results = []
    for rel, ent in repo.get_neighbors(company_uid, direction="out", kinds=_BID_KINDS, limit=5000):
        if ent.kind == _TENDER_KIND:
            results.append(
                {
                    "uid": ent.uid,
                    "name": ent.name,
                    "attributes": ent.attributes,
                    "role": rel.kind.value,
                    "is_win": rel.kind == BizRelationKind.AWARDED_TO,
                }
            )
    return results


def _tender_buyer(repo: BizRepository, tender_uid: str) -> Optional[dict]:
    """Return the buyer (org/company) that issued this tender."""
    for rel, ent in repo.get_neighbors(tender_uid, direction="out", kinds=[BizRelationKind.ISSUED_BY], limit=5):
        if ent.kind in _BUYER_KINDS:
            return {
                "uid": ent.uid,
                "name": ent.name,
                "kind": ent.kind.value,
                "attributes": ent.attributes,
            }
    return None


def _tender_competitors(repo: BizRepository, tender_uid: str, exclude_uid: str) -> list[dict]:
    """All other companies that participated in a tender."""
    results = []
    for rel, ent in repo.get_neighbors(tender_uid, direction="in", kinds=_BID_KINDS, limit=500):
        if ent.kind == BizEntityKind.COMPANY and ent.uid != exclude_uid:
            results.append(
                {
                    "uid": ent.uid,
                    "name": ent.name,
                    "role": rel.kind.value,
                    "is_win": rel.kind == BizRelationKind.AWARDED_TO,
                }
            )
    return results


def _tender_winner(repo: BizRepository, tender_uid: str) -> Optional[dict]:
    for rel, ent in repo.get_neighbors(tender_uid, direction="in", kinds=[BizRelationKind.AWARDED_TO], limit=5):
        if ent.kind == BizEntityKind.COMPANY:
            return {"uid": ent.uid, "name": ent.name}
    return None


def _company_industries(repo: BizRepository, company_uid: str) -> list[dict]:
    """Industries the company is linked to."""
    results = []
    for rel, ent in repo.get_neighbors(company_uid, direction="out", kinds=[BizRelationKind.IN_INDUSTRY], limit=100):
        if ent.kind == _INDUSTRY_KIND:
            results.append({"uid": ent.uid, "name": ent.name})
    return results


def _company_locations(repo: BizRepository, company_uid: str) -> set[str]:
    """UIDs of cities/provinces the company is located in."""
    locs: set[str] = set()
    for rel, ent in repo.get_neighbors(
        company_uid,
        direction="out",
        kinds=[
            BizRelationKind.IN_CITY,
            BizRelationKind.IN_PROVINCE,
            BizRelationKind.LOCATED_AT,
            BizRelationKind.HAS_ADDRESS,
        ],
        limit=100,
    ):
        locs.add(ent.uid)
        locs.add(ent.name.lower())
    return locs


def _tender_industries(repo: BizRepository, tender_uid: str) -> list[dict]:
    """Industries associated with a tender (via winning/bidding companies)."""
    seen: set[str] = set()
    results = []
    winner = _tender_winner(repo, tender_uid)
    subjects = [winner] if winner else []
    for c in _tender_competitors(repo, tender_uid, ""):
        subjects.append(c)
    for subj in subjects[:10]:
        for ind in _company_industries(repo, subj["uid"]):
            if ind["uid"] not in seen:
                seen.add(ind["uid"])
                results.append(ind)
    return results


def _buyer_all_tenders(repo: BizRepository, buyer_uid: str) -> list[dict]:
    """All tenders issued by a buyer."""
    results = []
    for rel, ent in repo.get_neighbors(buyer_uid, direction="in", kinds=[BizRelationKind.ISSUED_BY], limit=5000):
        if ent.kind == _TENDER_KIND:
            results.append(
                {
                    "uid": ent.uid,
                    "name": ent.name,
                    "attributes": ent.attributes,
                }
            )
    return results


def _tender_value(attrs: dict) -> Optional[float]:
    for k in ("value", "contract_value", "estimated_value", "budget", "award_value", "amount"):
        v = _safe_float(attrs.get(k))
        if v is not None:
            return v
    return None


def _tender_date(attrs: dict) -> Optional[str]:
    for k in ("valid_from", "date", "issue_date", "posted_date", "closing_date", "deadline"):
        v = attrs.get(k)
        if v:
            return str(v)
    return None


def _deadline_date(attrs: dict) -> Optional[str]:
    for k in ("closing_date", "deadline", "due_date", "submission_deadline", "valid_to"):
        v = attrs.get(k)
        if v:
            return str(v)
    return None


def _months_until(date_str: Optional[str]) -> Optional[float]:
    if not date_str:
        return None
    try:
        dt = datetime.datetime.fromisoformat(date_str[:10])
        now = datetime.datetime.now()
        delta = (dt - now).days
        return round(delta / 30.44, 1)
    except (ValueError, TypeError):
        return None


def _months_ago(date_str: Optional[str]) -> Optional[float]:
    m = _months_until(date_str)
    return None if m is None else round(-m, 1)


# ═══════════════════════════════════════════════════════════════════════════════
# Engine
# ═══════════════════════════════════════════════════════════════════════════════


class OpportunityIntelligenceEngine:
    """
    Read-only opportunity scoring and recommendation engine.

    All methods take (company_uid, tender_uid) and return plain dicts
    (JSON-serialisable) with evidence, confidence, and reasoning.
    """

    def __init__(self, repo: BizRepository) -> None:
        self._repo = repo

    # ═══════════════════════════════════════════════════════════════════════════
    # Core scoring — individual dimensions
    # ═══════════════════════════════════════════════════════════════════════════

    def _score_capability(self, company_uid: str, tender: dict) -> tuple[float, list]:
        """
        0–1 capability fit based on industry overlap between company and tender.
        """
        evidence: list[dict] = []
        company_inds = {i["uid"] for i in _company_industries(self._repo, company_uid)}
        tender_inds = _tender_industries(self._repo, tender["uid"])

        if not company_inds or not tender_inds:
            return 0.5, evidence  # neutral when data missing

        overlap = 0
        for ind in tender_inds:
            if ind["uid"] in company_inds:
                overlap += 1
                evidence.append(_ev(company_uid, "in_industry", ind["uid"], "", ind["name"]))

        score = _clamp(overlap / max(len(tender_inds), 1))
        return round(score, 4), evidence

    def _score_buyer_history(self, company_uid: str, buyer: Optional[dict]) -> tuple[float, list]:
        """
        0–1 based on historical success rate with this specific buyer.
        """
        evidence: list[dict] = []
        if not buyer:
            return 0.3, evidence  # no buyer data → below neutral

        buyer_uid = buyer["uid"]
        buyer_tenders = {t["uid"] for t in _buyer_all_tenders(self._repo, buyer_uid)}
        company_tenders = _company_tenders(self._repo, company_uid)

        bids_with_buyer = [t for t in company_tenders if t["uid"] in buyer_tenders]
        wins_with_buyer = [t for t in bids_with_buyer if t["is_win"]]

        for t in wins_with_buyer[:5]:
            evidence.append(_ev(company_uid, "awarded_to_tender", t["uid"], "", t["name"]))

        if not bids_with_buyer:
            return 0.3, evidence  # never worked with this buyer

        win_rate = len(wins_with_buyer) / len(bids_with_buyer)
        # Boost for volume: more data → more confident
        volume_bonus = _clamp(len(bids_with_buyer) / 10.0) * 0.1
        score = _clamp(win_rate + volume_bonus)
        return round(score, 4), evidence

    def _score_industry_history(self, company_uid: str, tender: dict) -> tuple[float, list]:
        """
        0–1 based on win rate in the tender's industries.
        """
        evidence: list[dict] = []
        tender_inds = {i["uid"] for i in _tender_industries(self._repo, tender["uid"])}
        if not tender_inds:
            return 0.4, evidence

        company_tenders = _company_tenders(self._repo, company_uid)
        # For each company tender, check if its buyer/industry overlaps
        relevant_bids = 0
        relevant_wins = 0
        for ct in company_tenders:
            ct_inds = {i["uid"] for i in _tender_industries(self._repo, ct["uid"])}
            if ct_inds & tender_inds:
                relevant_bids += 1
                if ct["is_win"]:
                    relevant_wins += 1
                    evidence.append(_ev(company_uid, "won_similar_industry_tender", ct["uid"], "", ct["name"]))

        if relevant_bids == 0:
            return 0.3, evidence
        score = _clamp(relevant_wins / relevant_bids)
        return round(score, 4), evidence

    def _score_value_fit(self, company_uid: str, tender_value: Optional[float]) -> tuple[float, list]:
        """
        0–1 based on how well the tender's value matches the company's typical contract size.
        """
        evidence: list[dict] = []
        if tender_value is None:
            return 0.5, evidence  # neutral

        tender_bucket = _value_bucket(tender_value)
        company_tenders = _company_tenders(self._repo, company_uid)
        bucket_counts: dict[str, int] = defaultdict(int)

        for ct in company_tenders:
            val = _tender_value(ct["attributes"])
            b = _value_bucket(val)
            if b:
                bucket_counts[b] += 1

        if not bucket_counts:
            return 0.5, evidence

        total = sum(bucket_counts.values())
        match_count = bucket_counts.get(tender_bucket, 0)

        # Adjacent buckets count half
        order = ["micro", "small", "medium", "large", "mega"]
        if tender_bucket in order:
            idx = order.index(tender_bucket)
            for adj in [idx - 1, idx + 1]:
                if 0 <= adj < len(order):
                    match_count += bucket_counts.get(order[adj], 0) * 0.5

        score = _clamp(match_count / total)
        if match_count > 0:
            evidence.append(
                _ev(
                    company_uid,
                    "value_fit",
                    tender_bucket or "unknown",
                    "",
                    f"{match_count:.0f}/{total} past tenders in similar value range",
                )
            )
        return round(score, 4), evidence

    def _score_geo_fit(self, company_uid: str, tender: dict) -> tuple[float, list]:
        """
        0–1 geographic fit: company location vs tender location.
        """
        evidence: list[dict] = []
        company_locs = _company_locations(self._repo, company_uid)
        if not company_locs:
            return 0.5, evidence

        # Tender location from attributes or buyer location
        t_attrs = tender["attributes"]
        tender_city = str(t_attrs.get("city", "") or "").lower()
        tender_prov = str(t_attrs.get("province", "") or "").lower()
        buyer = _tender_buyer(self._repo, tender["uid"])
        if buyer:
            buyer_attrs = buyer["attributes"]
            tender_city = tender_city or str(buyer_attrs.get("city", "") or "").lower()
            tender_prov = tender_prov or str(buyer_attrs.get("province", "") or "").lower()

        if not tender_city and not tender_prov:
            return 0.5, evidence

        score = 0.2  # baseline: company can work anywhere
        if tender_city and tender_city in company_locs:
            score = 1.0
            evidence.append(_ev(company_uid, "in_city", tender_city, "", f"Company operates in {tender_city}"))
        elif tender_prov and tender_prov in company_locs:
            score = 0.7
            evidence.append(
                _ev(
                    company_uid,
                    "in_province",
                    tender_prov,
                    "",
                    f"Company in same province: {tender_prov}",
                )
            )
        return round(score, 4), evidence

    def _score_competition(self, tender_uid: str) -> tuple[float, list]:
        """
        0–1 competition score: LOWER competition → HIGHER score.
        """
        evidence: list[dict] = []
        competitors = _tender_competitors(self._repo, tender_uid, "")
        n = len(competitors)

        # <3 bidders → high score; >10 → low
        if n == 0:
            score = 0.5  # unknown competition
        elif n <= 2:
            score = 0.9
        elif n <= 5:
            score = 0.7
        elif n <= 10:
            score = 0.4
        else:
            score = max(0.1, 1.0 - (n / 20.0))

        if n > 0:
            evidence.append(_ev(tender_uid, "competitors", str(n), "", f"{n} competitors on this tender"))
        return round(score, 4), evidence

    def _score_buyer_attractiveness(self, buyer: Optional[dict]) -> tuple[float, list]:
        """
        0–1 buyer attractiveness: tender volume + repeat potential.
        """
        evidence: list[dict] = []
        if not buyer:
            return 0.3, evidence

        buyer_uid = buyer["uid"]
        buyer_tenders = _buyer_all_tenders(self._repo, buyer_uid)
        n_tenders = len(buyer_tenders)

        # Score based on volume of tenders (proxy for relationship potential)
        if n_tenders == 0:
            score = 0.2
        elif n_tenders <= 2:
            score = 0.4
        elif n_tenders <= 10:
            score = 0.6
        elif n_tenders <= 30:
            score = 0.8
        else:
            score = 0.95

        evidence.append(
            _ev(
                buyer_uid,
                "issues_tenders",
                str(n_tenders),
                buyer["name"],
                f"{n_tenders} historical tenders",
            )
        )
        return round(score, 4), evidence

    def _score_strategic(self, company_uid: str, buyer: Optional[dict], tender_inds: list[dict]) -> tuple[float, list]:
        """
        0–1 strategic importance: new buyer, new industry, expanding footprint.
        """
        evidence: list[dict] = []
        score = 0.3  # baseline

        # Is buyer new? (company never worked with them)
        if buyer:
            buyer_tenders = {t["uid"] for t in _buyer_all_tenders(self._repo, buyer["uid"])}
            company_tenders = {t["uid"] for t in _company_tenders(self._repo, company_uid)}
            overlap = buyer_tenders & company_tenders
            if not overlap:
                score = min(score + 0.3, 1.0)
                evidence.append(_ev(company_uid, "new_buyer_opportunity", buyer["uid"], "", buyer["name"]))
            else:
                score = min(score + 0.1, 1.0)

        # Are any tender industries new for the company?
        company_inds = {i["uid"] for i in _company_industries(self._repo, company_uid)}
        for ind in tender_inds:
            if ind["uid"] not in company_inds:
                score = min(score + 0.15, 1.0)
                evidence.append(_ev(company_uid, "new_industry_expansion", ind["uid"], "", ind["name"]))

        return round(score, 4), evidence

    def _score_workload(self, company_uid: str) -> tuple[float, list]:
        """
        0–1 workload impact: fewer recent bids → higher capacity → higher score.
        Based on recency of recent tender activity.
        """
        evidence: list[dict] = []
        company_tenders = _company_tenders(self._repo, company_uid)

        # Count tenders in the last 12 months (by valid_from year)
        current_year = datetime.datetime.now().year
        recent = [
            t for t in company_tenders if _parse_year(_tender_date(t["attributes"])) in (current_year, current_year - 1)
        ]

        n = len(recent)
        if n == 0:
            score = 0.9
        elif n <= 3:
            score = 0.8
        elif n <= 8:
            score = 0.6
        elif n <= 15:
            score = 0.4
        else:
            score = max(0.1, 1.0 - n / 30.0)

        evidence.append(_ev(company_uid, "recent_tender_count", str(n), "", f"{n} tenders in last 24 months"))
        return round(score, 4), evidence

    def _score_win_probability(self, company_uid: str, buyer: Optional[dict], tender_uid: str) -> tuple[float, list]:
        """
        0–1 estimated probability of winning based on overall history.
        """
        evidence: list[dict] = []
        company_tenders = _company_tenders(self._repo, company_uid)
        wins = [t for t in company_tenders if t["is_win"]]
        total = len(company_tenders)

        if total == 0:
            return 0.2, evidence

        base_win_rate = len(wins) / total

        # Adjust for buyer familiarity
        if buyer:
            buyer_tenders_set = {t["uid"] for t in _buyer_all_tenders(self._repo, buyer["uid"])}
            buyer_bids = [t for t in company_tenders if t["uid"] in buyer_tenders_set]
            buyer_wins = [t for t in buyer_bids if t["is_win"]]
            if buyer_bids:
                buyer_rate = len(buyer_wins) / len(buyer_bids)
                base_win_rate = (base_win_rate + buyer_rate) / 2
                evidence.append(
                    _ev(
                        company_uid,
                        "buyer_specific_win_rate",
                        buyer["uid"],
                        "",
                        f"{len(buyer_wins)}/{len(buyer_bids)} wins with this buyer",
                    )
                )

        evidence.append(_ev(company_uid, "overall_win_rate", str(total), "", f"{len(wins)}/{total} overall wins"))
        return round(_clamp(base_win_rate), 4), evidence

    # ═══════════════════════════════════════════════════════════════════════════
    # Main scoring entry point
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_score(self, company_uid: str, tender_uid: str) -> dict:
        """
        Calculate a 0–100 Opportunity Score with full dimension breakdown.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err
        err = _require_entity(self._repo, tender_uid, [_TENDER_KIND])
        if err:
            return err

        company = self._repo.get(company_uid)
        tender = self._repo.get(tender_uid)
        t_attrs = tender.attributes
        t_value = _tender_value(t_attrs)
        buyer = _tender_buyer(self._repo, tender_uid)
        t_inds = _tender_industries(self._repo, tender_uid)

        all_evidence: list[dict] = []
        assumptions: list[str] = []
        weak: list[str] = []
        missing: list[str] = []
        reasoning: list[str] = []

        # ── Dimension scores ──────────────────────────────────────────────────
        s_cap, ev_cap = self._score_capability(company_uid, tender.to_full())
        s_bh, ev_bh = self._score_buyer_history(company_uid, buyer)
        s_ih, ev_ih = self._score_industry_history(company_uid, tender.to_full())
        s_val, ev_val = self._score_value_fit(company_uid, t_value)
        s_geo, ev_geo = self._score_geo_fit(company_uid, tender.to_full())
        s_comp, ev_comp = self._score_competition(tender_uid)
        s_ba, ev_ba = self._score_buyer_attractiveness(buyer)
        s_str, ev_str = self._score_strategic(company_uid, buyer, t_inds)
        s_wl, ev_wl = self._score_workload(company_uid)
        s_wp, ev_wp = self._score_win_probability(company_uid, buyer, tender_uid)

        for ev_list in [ev_cap, ev_bh, ev_ih, ev_val, ev_geo, ev_comp, ev_ba, ev_str, ev_wl, ev_wp]:
            all_evidence.extend(ev_list)

        # ── Weighted total ────────────────────────────────────────────────────
        raw = (
            s_cap * _W_CAPABILITY
            + s_bh * _W_BUYER_HISTORY
            + s_ih * _W_INDUSTRY_HISTORY
            + s_val * _W_VALUE_FIT
            + s_geo * _W_GEO_FIT
            + s_comp * _W_COMPETITION
            + s_ba * _W_BUYER_ATTRACT
            + s_str * _W_STRATEGIC
            + s_wl * _W_WORKLOAD
            + s_wp * _W_WIN_PROBABILITY
        )
        total_score = round(raw, 1)

        # ── Reasoning chain ───────────────────────────────────────────────────
        reasoning.append(f"Capability fit: {s_cap:.0%} → {s_cap * _W_CAPABILITY:.1f}/{_W_CAPABILITY} pts")
        reasoning.append(f"Buyer history:  {s_bh:.0%} → {s_bh * _W_BUYER_HISTORY:.1f}/{_W_BUYER_HISTORY} pts")
        reasoning.append(f"Industry hist.: {s_ih:.0%} → {s_ih * _W_INDUSTRY_HISTORY:.1f}/{_W_INDUSTRY_HISTORY} pts")
        reasoning.append(f"Value fit:      {s_val:.0%} → {s_val * _W_VALUE_FIT:.1f}/{_W_VALUE_FIT} pts")
        reasoning.append(f"Geo fit:        {s_geo:.0%} → {s_geo * _W_GEO_FIT:.1f}/{_W_GEO_FIT} pts")
        reasoning.append(f"Competition:    {s_comp:.0%} → {s_comp * _W_COMPETITION:.1f}/{_W_COMPETITION} pts")
        reasoning.append(f"Buyer attract.: {s_ba:.0%} → {s_ba * _W_BUYER_ATTRACT:.1f}/{_W_BUYER_ATTRACT} pts")
        reasoning.append(f"Strategic:      {s_str:.0%} → {s_str * _W_STRATEGIC:.1f}/{_W_STRATEGIC} pts")
        reasoning.append(f"Workload:       {s_wl:.0%} → {s_wl * _W_WORKLOAD:.1f}/{_W_WORKLOAD} pts")
        reasoning.append(f"Win probability:{s_wp:.0%} → {s_wp * _W_WIN_PROBABILITY:.1f}/{_W_WIN_PROBABILITY} pts")
        reasoning.append(f"Total score:    {total_score:.1f}/100")

        # ── Assumptions and missing info ──────────────────────────────────────
        if not t_inds:
            weak.append("No industry data on tender — capability and industry-history scores use neutral values")
        if not buyer:
            missing.append("No buyer/issuing organization found for this tender")
        if t_value is None:
            missing.append("No contract value on tender — value-fit score uses neutral estimate")
        if not ev_geo:
            weak.append("No location data — geographic fit is neutral")
        if not ev_bh:
            assumptions.append("Company has no prior history with this buyer; score uses overall win rate")
        if not _company_tenders(self._repo, company_uid):
            assumptions.append("Company has no tender history; win probability defaults to 0.2")

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "score": total_score,
            "max_score": 100,
            "dimensions": {
                "capability_fit": {
                    "score": round(s_cap * _W_CAPABILITY, 1),
                    "raw": s_cap,
                    "weight": _W_CAPABILITY,
                },
                "buyer_history": {
                    "score": round(s_bh * _W_BUYER_HISTORY, 1),
                    "raw": s_bh,
                    "weight": _W_BUYER_HISTORY,
                },
                "industry_history": {
                    "score": round(s_ih * _W_INDUSTRY_HISTORY, 1),
                    "raw": s_ih,
                    "weight": _W_INDUSTRY_HISTORY,
                },
                "value_fit": {
                    "score": round(s_val * _W_VALUE_FIT, 1),
                    "raw": s_val,
                    "weight": _W_VALUE_FIT,
                },
                "geographic_fit": {
                    "score": round(s_geo * _W_GEO_FIT, 1),
                    "raw": s_geo,
                    "weight": _W_GEO_FIT,
                },
                "competition_level": {
                    "score": round(s_comp * _W_COMPETITION, 1),
                    "raw": s_comp,
                    "weight": _W_COMPETITION,
                },
                "buyer_attractiveness": {
                    "score": round(s_ba * _W_BUYER_ATTRACT, 1),
                    "raw": s_ba,
                    "weight": _W_BUYER_ATTRACT,
                },
                "strategic_importance": {
                    "score": round(s_str * _W_STRATEGIC, 1),
                    "raw": s_str,
                    "weight": _W_STRATEGIC,
                },
                "workload_impact": {
                    "score": round(s_wl * _W_WORKLOAD, 1),
                    "raw": s_wl,
                    "weight": _W_WORKLOAD,
                },
                "win_probability": {
                    "score": round(s_wp * _W_WIN_PROBABILITY, 1),
                    "raw": s_wp,
                    "weight": _W_WIN_PROBABILITY,
                },
            },
            "evidence": all_evidence[:30],
            "assumptions": assumptions,
            "weak_evidence": weak,
            "missing_information": missing,
            "reasoning_chain": reasoning,
            "confidence": _confidence(len(all_evidence)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Recommendation
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_recommendation(self, company_uid: str, tender_uid: str) -> dict:
        """
        Return a recommendation label and rationale.

        Labels:
          Strong Pursue  — score >= 75
          Pursue         — score >= 55
          Strategic Inv. — score >= 40 AND strategic dimension is high
          Monitor        — score >= 35
          Ignore         — score < 35
        """
        scored = self.opportunity_score(company_uid, tender_uid)
        if "error" in scored:
            return scored

        score = scored["score"]
        dims = scored["dimensions"]
        strategic = dims["strategic_importance"]["raw"]
        buyer_h = dims["buyer_history"]["raw"]
        comp = dims["competition_level"]["raw"]

        why_pursue: list[str] = []
        why_ignore: list[str] = []

        if dims["capability_fit"]["raw"] >= 0.6:
            why_pursue.append("Strong capability alignment")
        else:
            why_ignore.append("Limited capability overlap")

        if buyer_h >= 0.5:
            why_pursue.append(f"Proven track record with this buyer ({buyer_h:.0%} win rate)")
        elif buyer_h < 0.25:
            why_ignore.append("No or poor history with this buyer")

        if comp >= 0.7:
            why_pursue.append("Low competition — few rivals on similar tenders")
        elif comp < 0.4:
            why_ignore.append("Highly competitive market — many rivals")

        if strategic >= 0.6:
            why_pursue.append("High strategic value — new buyer/market opportunity")

        if dims["workload_impact"]["raw"] < 0.4:
            why_ignore.append("High current workload — capacity may be strained")

        # Determine label
        if score >= 75:
            label = "Strong Pursue"
        elif score >= 55:
            label = "Pursue"
        elif score >= 40 and strategic >= 0.6:
            label = "Strategic Investment"
        elif score >= 35:
            label = "Monitor"
        else:
            label = "Ignore"

        next_actions: list[str] = []
        if label in ("Strong Pursue", "Pursue"):
            next_actions.append("Assign bid team and begin qualification review")
            next_actions.append("Contact buyer relationship manager for intel")
            deadline = _deadline_date(self._repo.get(tender_uid).attributes)
            if deadline:
                months = _months_until(deadline)
                if months is not None and months <= 1:
                    next_actions.insert(0, f"URGENT: deadline in {months:.1f} months")
        elif label == "Strategic Investment":
            next_actions.append("Evaluate partnership or teaming arrangement")
            next_actions.append("Track buyer relationship development")
        elif label == "Monitor":
            next_actions.append("Set a watch alert for updates")
            next_actions.append("Reassess if a strategic partner becomes available")

        return {
            "company_uid": company_uid,
            "tender_uid": tender_uid,
            "score": score,
            "recommendation": label,
            "why_pursue": why_pursue,
            "why_ignore": why_ignore,
            "next_actions": next_actions,
            "evidence": scored["evidence"][:15],
            "confidence": scored["confidence"],
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Full explainability report
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_explain(self, company_uid: str, tender_uid: str) -> dict:
        """
        Full explainability report: score breakdown, evidence, assumptions,
        weak evidence, missing info, reasoning chain, recommendation.
        """
        scored = self.opportunity_score(company_uid, tender_uid)
        if "error" in scored:
            return scored
        rec = self.opportunity_recommendation(company_uid, tender_uid)
        if "error" in rec:
            return rec

        tender = self._repo.get(tender_uid)
        company = self._repo.get(company_uid)
        buyer = _tender_buyer(self._repo, tender_uid)

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "tender_value": _tender_value(tender.attributes),
            "tender_date": _tender_date(tender.attributes),
            "tender_deadline": _deadline_date(tender.attributes),
            "buyer": buyer,
            "score": scored["score"],
            "dimensions": scored["dimensions"],
            "recommendation": rec["recommendation"],
            "why_pursue": rec["why_pursue"],
            "why_ignore": rec["why_ignore"],
            "next_actions": rec["next_actions"],
            "evidence": scored["evidence"],
            "assumptions": scored["assumptions"],
            "weak_evidence": scored["weak_evidence"],
            "missing_information": scored["missing_information"],
            "reasoning_chain": scored["reasoning_chain"],
            "confidence": scored["confidence"],
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Opportunity timeline
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_timeline(self, company_uid: str, tender_uid: str) -> dict:
        """
        Estimate preparation effort, submission urgency, deadline risk,
        and surface similar historical opportunities with outcomes.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err
        err = _require_entity(self._repo, tender_uid, [_TENDER_KIND])
        if err:
            return err

        company = self._repo.get(company_uid)
        tender = self._repo.get(tender_uid)
        t_attrs = tender.attributes
        t_value = _tender_value(t_attrs)
        deadline_str = _deadline_date(t_attrs)
        issue_date_str = _tender_date(t_attrs)

        months_until_deadline = _months_until(deadline_str)
        months_since_issue = _months_ago(issue_date_str)

        # Urgency
        if months_until_deadline is None:
            urgency = "unknown"
        elif months_until_deadline <= 0:
            urgency = "expired"
        elif months_until_deadline <= 0.5:
            urgency = "critical"
        elif months_until_deadline <= 1:
            urgency = "high"
        elif months_until_deadline <= 3:
            urgency = "medium"
        else:
            urgency = "low"

        # Preparation effort estimate (based on value bucket)
        bucket = _value_bucket(t_value)
        effort_map = {
            "micro": "1–3 days",
            "small": "3–7 days",
            "medium": "1–3 weeks",
            "large": "2–6 weeks",
            "mega": "2–4 months",
            None: "unknown",
        }
        prep_effort = effort_map.get(bucket, "unknown")

        # Deadline risk
        if urgency in ("critical", "expired"):
            deadline_risk = "high"
        elif urgency == "high":
            deadline_risk = "medium"
        else:
            deadline_risk = "low"

        # Similar historical opportunities
        simils = self.similar_opportunities(company_uid, tender_uid, limit=5)
        comparable_wins = [s for s in simils.get("similar", []) if s.get("outcome") == "win"]
        comparable_losses = [s for s in simils.get("similar", []) if s.get("outcome") == "loss"]

        evidence = []
        if deadline_str:
            evidence.append(_ev(tender_uid, "deadline", deadline_str, tender.name, ""))
        if issue_date_str:
            evidence.append(_ev(tender_uid, "issue_date", issue_date_str, tender.name, ""))

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "tender_value": t_value,
            "tender_value_bucket": bucket,
            "issue_date": issue_date_str,
            "deadline": deadline_str,
            "months_until_deadline": months_until_deadline,
            "months_since_issue": months_since_issue,
            "submission_urgency": urgency,
            "preparation_effort": prep_effort,
            "deadline_risk": deadline_risk,
            "comparable_wins": comparable_wins[:5],
            "comparable_losses": comparable_losses[:5],
            "evidence": evidence,
            "confidence": _confidence(len(evidence)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Risk analysis
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_risk(self, company_uid: str, tender_uid: str) -> dict:
        """
        Risk factors: competition, capability gaps, workload, buyer reliability,
        value exposure, geographic risk.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err
        err = _require_entity(self._repo, tender_uid, [_TENDER_KIND])
        if err:
            return err

        company = self._repo.get(company_uid)
        tender = self._repo.get(tender_uid)
        scored = self.opportunity_score(company_uid, tender_uid)
        if "error" in scored:
            return scored

        dims = scored["dimensions"]
        risks: list[dict] = []
        mitigations: list[str] = []

        # Competition risk
        comp_raw = dims["competition_level"]["raw"]
        if comp_raw < 0.4:
            risks.append(
                {
                    "factor": "high_competition",
                    "severity": "high",
                    "detail": "Many competitors on similar tenders",
                }
            )
            mitigations.append("Consider a teaming or consortium approach to differentiate")

        # Capability risk
        cap_raw = dims["capability_fit"]["raw"]
        if cap_raw < 0.3:
            risks.append(
                {
                    "factor": "capability_gap",
                    "severity": "high",
                    "detail": "Limited industry/capability overlap detected",
                }
            )
            mitigations.append("Identify and bridge capability gaps before submission")
        elif cap_raw < 0.5:
            risks.append(
                {
                    "factor": "partial_capability",
                    "severity": "medium",
                    "detail": "Partial capability overlap — some gaps present",
                }
            )

        # Workload risk
        wl_raw = dims["workload_impact"]["raw"]
        if wl_raw < 0.4:
            risks.append(
                {
                    "factor": "high_workload",
                    "severity": "medium",
                    "detail": "Company has high recent bid activity",
                }
            )
            mitigations.append("Review resource capacity before committing")

        # Buyer history risk
        bh_raw = dims["buyer_history"]["raw"]
        if bh_raw < 0.25:
            risks.append(
                {
                    "factor": "no_buyer_history",
                    "severity": "medium",
                    "detail": "No prior wins or bids with this buyer",
                }
            )
            mitigations.append("Invest in pre-bid relationship building with buyer")

        # Value risk
        val_raw = dims["value_fit"]["raw"]
        if val_raw < 0.3:
            risks.append(
                {
                    "factor": "value_mismatch",
                    "severity": "medium",
                    "detail": "Contract value outside company's typical range",
                }
            )
            mitigations.append("Ensure financial capacity and bonding for this contract size")

        # Geo risk
        geo_raw = dims["geographic_fit"]["raw"]
        if geo_raw < 0.4:
            risks.append(
                {
                    "factor": "geographic_stretch",
                    "severity": "low",
                    "detail": "Project may be outside company's primary geography",
                }
            )
            mitigations.append("Plan for mobilization and local sub-contractor partnerships")

        # Deadline risk (from timeline)
        tl = self.opportunity_timeline(company_uid, tender_uid)
        if tl.get("deadline_risk") == "high":
            risks.append(
                {
                    "factor": "tight_deadline",
                    "severity": "high",
                    "detail": f"Submission deadline is {tl.get('months_until_deadline')} months away",
                }
            )
            mitigations.append("Start bid preparation immediately — assign resources today")

        overall_risk = (
            "high"
            if any(r["severity"] == "high" for r in risks)
            else "medium"
            if any(r["severity"] == "medium" for r in risks)
            else "low"
        )

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "overall_risk": overall_risk,
            "risk_factors": risks,
            "mitigations": mitigations,
            "score": scored["score"],
            "evidence": scored["evidence"][:15],
            "confidence": scored["confidence"],
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Portfolio impact
    # ═══════════════════════════════════════════════════════════════════════════

    def portfolio_impact(self, company_uid: str, tender_uid: str) -> dict:
        """
        Estimate revenue contribution, diversification, strategic value,
        client expansion, and future relationship potential.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err
        err = _require_entity(self._repo, tender_uid, [_TENDER_KIND])
        if err:
            return err

        company = self._repo.get(company_uid)
        tender = self._repo.get(tender_uid)
        t_value = _tender_value(tender.attributes)
        buyer = _tender_buyer(self._repo, tender_uid)

        scored = self.opportunity_score(company_uid, tender_uid)
        if "error" in scored:
            return scored

        win_prob = scored["dimensions"]["win_probability"]["raw"]
        strategic = scored["dimensions"]["strategic_importance"]["raw"]
        diversity = scored["dimensions"]["buyer_history"]["raw"]

        expected_revenue = round(t_value * win_prob, 2) if t_value else None
        diversification = "high" if diversity < 0.3 else ("medium" if diversity < 0.6 else "low")
        strategic_value = "high" if strategic >= 0.6 else ("medium" if strategic >= 0.4 else "low")

        # Client expansion: new buyer = high expansion
        buyer_tenders_set = set()
        if buyer:
            buyer_tenders_set = {t["uid"] for t in _buyer_all_tenders(self._repo, buyer["uid"])}
        company_tenders_set = {t["uid"] for t in _company_tenders(self._repo, company_uid)}
        is_new_client = buyer is not None and not (buyer_tenders_set & company_tenders_set)

        # Future relationship potential: how many future tenders does this buyer typically issue?
        buyer_volume = len(buyer_tenders_set) if buyer else 0
        future_potential = "high" if buyer_volume >= 10 else "medium" if buyer_volume >= 3 else "low"

        evidence: list[dict] = []
        if buyer:
            evidence.append(
                _ev(
                    company_uid,
                    "buyer_volume",
                    buyer["uid"],
                    "",
                    f"{buyer_volume} tenders from this buyer",
                )
            )

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "tender_value": t_value,
            "win_probability": win_prob,
            "expected_revenue": expected_revenue,
            "diversification_impact": diversification,
            "strategic_value": strategic_value,
            "is_new_client": is_new_client,
            "client_expansion_value": "high" if is_new_client else "low",
            "future_relationship_potential": future_potential,
            "buyer": buyer,
            "evidence": evidence,
            "confidence": scored["confidence"],
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Similar opportunities
    # ═══════════════════════════════════════════════════════════════════════════

    def similar_opportunities(self, company_uid: str, tender_uid: str, limit: int = 10) -> dict:
        """
        Find historical tenders similar to the target: same buyer, overlapping
        industries, or similar value bucket.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err
        err = _require_entity(self._repo, tender_uid, [_TENDER_KIND])
        if err:
            return err

        company = self._repo.get(company_uid)
        tender = self._repo.get(tender_uid)
        t_value = _tender_value(tender.attributes)
        t_bucket = _value_bucket(t_value)
        t_inds = {i["uid"] for i in _tender_industries(self._repo, tender_uid)}
        buyer = _tender_buyer(self._repo, tender_uid)
        buyer_uid = buyer["uid"] if buyer else None

        company_tenders = _company_tenders(self._repo, company_uid)
        results: list[dict] = []

        for ct in company_tenders:
            if ct["uid"] == tender_uid:
                continue
            ct_attrs = ct["attributes"]
            ct_value = _tender_value(ct_attrs)
            ct_bucket = _value_bucket(ct_value)
            ct_inds = {i["uid"] for i in _tender_industries(self._repo, ct["uid"])}
            ct_buyer = _tender_buyer(self._repo, ct["uid"])

            similarity = 0.0
            reasons: list[str] = []

            if buyer_uid and ct_buyer and ct_buyer["uid"] == buyer_uid:
                similarity += 0.5
                reasons.append("same buyer")
            if t_inds & ct_inds:
                similarity += 0.3
                reasons.append(f"shared industry ({len(t_inds & ct_inds)} overlap)")
            if t_bucket and ct_bucket == t_bucket:
                similarity += 0.2
                reasons.append("same value bucket")

            if similarity > 0:
                results.append(
                    {
                        "uid": ct["uid"],
                        "name": ct["name"],
                        "value": ct_value,
                        "value_bucket": ct_bucket,
                        "outcome": "win" if ct["is_win"] else "loss",
                        "date": _tender_date(ct_attrs),
                        "similarity": round(similarity, 2),
                        "similarity_reasons": reasons,
                        "buyer": ct_buyer,
                    }
                )

        results.sort(key=lambda x: x["similarity"], reverse=True)
        evidence = [_ev(company_uid, "similar_tender", r["uid"], company.name, r["name"]) for r in results[:limit]]

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "tender_uid": tender_uid,
            "tender_name": tender.name,
            "similar_count": len(results),
            "similar": results[:limit],
            "evidence": evidence,
            "confidence": _confidence(len(results)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Best opportunities
    # ═══════════════════════════════════════════════════════════════════════════

    def best_opportunities(self, company_uid: str, limit: int = 10) -> dict:
        """
        Score ALL tenders in the graph and return the top-N for this company.
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err

        company = self._repo.get(company_uid)
        all_tenders = self._repo.find(kind=_TENDER_KIND, limit=5000)
        scored_list: list[dict] = []

        for t in all_tenders:
            s = self.opportunity_score(company_uid, t.uid)
            if "error" in s:
                continue
            rec_label = self.opportunity_recommendation(company_uid, t.uid).get("recommendation", "")
            scored_list.append(
                {
                    "tender_uid": t.uid,
                    "tender_name": t.name,
                    "score": s["score"],
                    "recommendation": rec_label,
                    "value": _tender_value(t.attributes),
                    "buyer": _tender_buyer(self._repo, t.uid),
                    "dimensions": {k: v["score"] for k, v in s["dimensions"].items()},
                }
            )

        scored_list.sort(key=lambda x: x["score"], reverse=True)

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "total_tenders_scored": len(scored_list),
            "top_opportunities": scored_list[:limit],
            "confidence": _confidence(len(scored_list)),
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Full profile
    # ═══════════════════════════════════════════════════════════════════════════

    def opportunity_profile(self, company_uid: str, tender_uid: str) -> dict:
        """
        Assemble every OIE dimension into one comprehensive response.
        """
        explain = self.opportunity_explain(company_uid, tender_uid)
        if "error" in explain:
            return explain
        timeline = self.opportunity_timeline(company_uid, tender_uid)
        risk = self.opportunity_risk(company_uid, tender_uid)
        portfolio = self.portfolio_impact(company_uid, tender_uid)
        similar = self.similar_opportunities(company_uid, tender_uid)

        return {
            "company_uid": company_uid,
            "company_name": explain["company_name"],
            "tender_uid": tender_uid,
            "tender_name": explain["tender_name"],
            "score": explain["score"],
            "recommendation": explain["recommendation"],
            "explain": explain,
            "timeline": timeline,
            "risk": risk,
            "portfolio": portfolio,
            "similar": similar,
        }

    # ═══════════════════════════════════════════════════════════════════════════
    # Executive summary
    # ═══════════════════════════════════════════════════════════════════════════

    def executive_summary(self, company_uid: str, limit: int = 5) -> dict:
        """
        CEO-dashboard structured summary:
        - Top opportunities today
        - Biggest risks
        - Why pursue / why ignore
        - Immediate next actions
        - Opportunity cost
        - Confidence level
        """
        err = _require_entity(self._repo, company_uid, _COMPANY_KINDS)
        if err:
            return err

        company = self._repo.get(company_uid)
        best = self.best_opportunities(company_uid, limit=limit * 2)
        top = best.get("top_opportunities", [])

        pursue_top = [t for t in top if t["recommendation"] in ("Strong Pursue", "Pursue")][:limit]
        ignore_top = sorted([t for t in top if t["recommendation"] == "Ignore"], key=lambda x: x["score"])[:3]

        biggest_risks: list[str] = []
        next_actions: list[str] = []
        why_pursue: list[str] = []
        why_ignore: list[str] = []

        for opp in pursue_top[:3]:
            why_pursue.append(f"{opp['tender_name']} (score {opp['score']:.0f}): {opp['recommendation']}")
            risk = self.opportunity_risk(company_uid, opp["tender_uid"])
            if risk.get("overall_risk") == "high":
                high_factors = ", ".join(r["factor"] for r in risk.get("risk_factors", []) if r["severity"] == "high")
                biggest_risks.append(f"{opp['tender_name']}: {high_factors}")
            tl = self.opportunity_timeline(company_uid, opp["tender_uid"])
            if tl.get("submission_urgency") in ("critical", "high"):
                next_actions.append(
                    f"URGENT: Start bid on '{opp['tender_name']}' ({tl.get('months_until_deadline')} months)"
                )
            else:
                next_actions.append(f"Begin qualification for '{opp['tender_name']}'")

        for opp in ignore_top:
            why_ignore.append(f"{opp['tender_name']} (score {opp['score']:.0f}): low score")

        # Opportunity cost: expected revenue of top ignores
        opp_cost: Optional[float] = None
        for opp in ignore_top:
            pi = self.portfolio_impact(company_uid, opp["tender_uid"])
            rev = pi.get("expected_revenue")
            if rev:
                opp_cost = (opp_cost or 0.0) + rev

        overall_confidence = _confidence(len(top))

        return {
            "company_uid": company_uid,
            "company_name": company.name,
            "total_tenders_scored": best.get("total_tenders_scored", 0),
            "top_opportunities": pursue_top,
            "biggest_risks": biggest_risks,
            "why_pursue": why_pursue,
            "why_ignore": why_ignore,
            "immediate_next_actions": next_actions,
            "opportunity_cost": opp_cost,
            "confidence": overall_confidence,
        }
