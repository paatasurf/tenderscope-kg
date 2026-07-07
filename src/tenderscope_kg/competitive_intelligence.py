"""
TenderScope Intelligence Engine — Competitive Intelligence Engine (CeI).

Analyses competitive dynamics across the business graph.

Design principles
-----------------
* **Graph-first**: every metric is derived purely from existing graph edges —
  no external data sources, no hard-coded assumptions.
* **Explainable**: every result includes ``evidence`` — the graph triples
  (entity, relation, entity) that justify each figure.
* **Read-only**: no writes, no schema changes.  Runs on top of BizRepository.
* **Composable**: each method returns an independent dict; the combined
  profile is assembled by ``competitor_profile``.
* **Confidence-scored**: numeric confidence accompanies every metric, blending
  evidence volume and relation quality.

Public API
----------
  competitor_profile(uid)            → full competitive profile (all sub-queries)
  direct_competitors(uid)            → companies sharing the same tenders/buyers
  emerging_competitors(uid)          → growing challengers in the same markets
  co_bidders(uid)                    → companies frequently bidding alongside
  common_losers(uid)                 → companies frequently losing to the same winner
  buyer_preferences(buyer_uid)       → which companies a buyer consistently chooses
  market_concentration(scope)        → HHI + dominant suppliers for a market
  market_share(scope_uid, by)        → share breakdown by buyer/city/province/industry/year
  dominant_suppliers(scope_uid, by)  → top-N winners in a market scope
  challenger_companies(scope_uid)    → rising challengers vs incumbents
  win_rate(uid)                      → win / bid / loss rates + bid frequency
  growth_trend(uid)                  → year-over-year activity trend
  competitor_rankings(scope_uid, by) → ranked company list with scores
  competitive_pressure(uid)          → composite pressure score with evidence
"""
from __future__ import annotations

import math
import re
from collections import defaultdict
from typing import Any, Optional

from .domain import BizEntityKind, BizRelationKind
from .repository._base import BizRepository


# ── Constants ─────────────────────────────────────────────────────────────────

# Relation kinds used when gathering tender participation
_BID_KINDS = [
    BizRelationKind.AWARDED_TO,
    BizRelationKind.SUBMITTED_BID,
    BizRelationKind.PARTICIPATED_IN,
]

# Relation kinds pointing to buyer orgs from tenders
_BUYER_KINDS = [BizRelationKind.ISSUED_BY]

# Location relation kinds
_CITY_KINDS     = [BizRelationKind.IN_CITY]
_PROVINCE_KINDS = [BizRelationKind.IN_PROVINCE]
_INDUSTRY_KINDS = [BizRelationKind.IN_INDUSTRY]


# ── Internal helpers ──────────────────────────────────────────────────────────

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


def _buyer_award_stats(
    repo: BizRepository,
    buyer_uid: str,
) -> dict[str, Any]:
    """
    For a given buyer (ORG), collect per-company award frequency and total
    awarded contract value across all tenders issued by that buyer.

    Returns a dict with:
      total_wins        int   — total AWARDED_TO edges in this buyer's tenders
      total_value       float — sum of contract_value across all winning edges
                                (0.0 when no value data is present)
      value_coverage    float — fraction of winning edges that carry a value
                                (α factor; 0.0 → pure-frequency fallback)
      company_wins      dict[company_uid, int]
      company_values    dict[company_uid, float]
    """
    company_wins:   dict[str, int]   = defaultdict(int)
    company_values: dict[str, float] = defaultdict(float)
    total_wins   = 0
    total_value  = 0.0
    valued_edges = 0

    for _rel, t_ent in repo.get_neighbors(
        buyer_uid, direction="in",
        kinds=[BizRelationKind.ISSUED_BY], limit=5000,
    ):
        if t_ent.kind != BizEntityKind.TENDER:
            continue
        for award_rel, c_ent in repo.get_neighbors(
            t_ent.uid, direction="in",
            kinds=[BizRelationKind.AWARDED_TO], limit=50,
        ):
            if c_ent.kind != BizEntityKind.COMPANY:
                continue
            total_wins += 1
            company_wins[c_ent.uid] += 1
            v = _parse_value(
                award_rel.attributes.get("contract_value") or
                t_ent.attributes.get("contract_value") or
                t_ent.attributes.get("estimated_value")
            )
            if v is not None:
                valued_edges += 1
                total_value += v
                company_values[c_ent.uid] += v

    value_coverage = round(valued_edges / total_wins, 4) if total_wins else 0.0

    return {
        "total_wins":     total_wins,
        "total_value":    total_value,
        "value_coverage": value_coverage,
        "company_wins":   dict(company_wins),
        "company_values": dict(company_values),
    }


def _bawd_score(
    repo: BizRepository,
    company_uid: str,
    won_tenders: list[tuple[str, str]],
) -> dict[str, Any]:
    """
    Buyer-scoped Award-Weighted Dominance (BAWD) score for a company.

    For each buyer the company has won from, compute:
      freq_share  = company_wins(buyer) / total_wins(buyer)
      val_share   = company_value(buyer) / total_value(buyer)  (0 if no values)
      alpha       = fraction of buyer's winning edges that carry contract_value
      dominance   = alpha * val_share + (1 - alpha) * freq_share

    Final score = weighted mean of per-buyer dominance, weighted by
    total_wins(buyer) so larger procurement markets contribute proportionally.

    Returns a dict suitable for direct use in win_rate() and
    competitive_pressure().
    """
    buyer_uids: set[str] = set()
    for t_uid, _ in won_tenders:
        b = _tender_buyer(repo, t_uid)
        if b:
            buyer_uids.add(b[0])

    if not buyer_uids:
        # No ISSUED_BY links available — fall back to a scope built from all
        # other companies that appear on the same won tenders.  If there are
        # no co-winners either, the company is the sole known supplier and
        # gets dominance = 1.0 (maximum-uncertainty estimate; confidence stays
        # low because evidence is thin).
        total_tw = len(won_tenders)
        if total_tw == 0:
            return {
                "score":          0.0,
                "buyer_count":    0,
                "total_wins":     0,
                "value_coverage": 0.0,
                "per_buyer":      [],
            }
        all_winners: set[str] = {company_uid}
        for t_uid, _ in won_tenders:
            for c_uid, _, _ in _tender_companies(repo, t_uid):
                all_winners.add(c_uid)
        scope_size  = len(all_winners)
        freq_share  = 1.0 / scope_size if scope_size else 1.0
        dominance   = round(freq_share, 4)
        return {
            "score":          dominance,
            "buyer_count":    0,
            "total_wins":     total_tw,
            "value_coverage": 0.0,
            "per_buyer":      [],
        }

    weighted_sum  = 0.0
    weight_total  = 0.0
    total_value_coverage_num = 0.0
    total_wins_all = 0
    per_buyer: list[dict] = []

    for b_uid in buyer_uids:
        stats = _buyer_award_stats(repo, b_uid)
        tw    = stats["total_wins"]
        tv    = stats["total_value"]
        alpha = stats["value_coverage"]

        if tw == 0:
            continue

        c_wins  = stats["company_wins"].get(company_uid, 0)
        c_value = stats["company_values"].get(company_uid, 0.0)

        freq_share = c_wins / tw
        val_share  = (c_value / tv) if tv > 0 else 0.0
        dominance  = alpha * val_share + (1.0 - alpha) * freq_share

        weighted_sum  += dominance * tw
        weight_total  += tw
        total_wins_all += c_wins
        total_value_coverage_num += alpha * tw

        b_ent = repo.get(b_uid)
        per_buyer.append({
            "buyer_uid":    b_uid,
            "buyer_name":   b_ent.name if b_ent else b_uid,
            "company_wins": c_wins,
            "total_wins":   tw,
            "freq_share":   round(freq_share, 4),
            "val_share":    round(val_share, 4),
            "alpha":        round(alpha, 4),
            "dominance":    round(dominance, 4),
        })

    if weight_total == 0:
        return {
            "score":          0.0,
            "buyer_count":    len(buyer_uids),
            "total_wins":     total_wins_all,
            "value_coverage": 0.0,
            "per_buyer":      per_buyer,
        }

    score = round(weighted_sum / weight_total, 4)
    overall_coverage = round(total_value_coverage_num / weight_total, 4)

    return {
        "score":          score,
        "buyer_count":    len(per_buyer),
        "total_wins":     total_wins_all,
        "value_coverage": overall_coverage,
        "per_buyer":      per_buyer,
    }


def _ev(entity_uid: str, rel_kind: str, target_uid: str,
        entity_name: str = "", target_name: str = "") -> dict:
    """Build a single evidence triple."""
    return {
        "entity_uid": entity_uid,
        "entity_name": entity_name,
        "relation": rel_kind,
        "target_uid": target_uid,
        "target_name": target_name,
    }


def _confidence(evidence_count: int, base: float = 0.3,
                scale: float = 0.07, cap_at: int = 10) -> float:
    """Confidence grows with evidence volume, same formula as CIE."""
    return round(min(1.0, base + scale * min(cap_at, evidence_count)), 4)


def _parse_year(date_str: Optional[str]) -> Optional[int]:
    if not date_str:
        return None
    try:
        return int(str(date_str)[:4])
    except (ValueError, TypeError):
        return None


def _hhi(counts: list[float]) -> float:
    """Herfindahl–Hirschman Index (0–1 scale; 1 = pure monopoly)."""
    total = sum(counts)
    if total == 0:
        return 0.0
    shares = [c / total for c in counts]
    return round(sum(s * s for s in shares), 4)


# ── Public: get tender set for a company ─────────────────────────────────────

def _company_tenders(repo: BizRepository, uid: str
                     ) -> list[tuple[str, str, str]]:
    """
    Return (tender_uid, tender_name, relation_kind_str) for all tenders
    the company has bid on, participated in, or been awarded.
    """
    results = []
    for rel, ent in repo.get_neighbors(uid, direction="out", kinds=_BID_KINDS,
                                        limit=2000):
        if ent.kind == BizEntityKind.TENDER:
            results.append((ent.uid, ent.name, rel.kind.value))
    return results


def _tender_companies(repo: BizRepository, tender_uid: str
                      ) -> list[tuple[str, str, str]]:
    """
    Return (company_uid, company_name, relation_kind_str) for all companies
    connected to a tender (from their side — outgoing bid/awarded edges).
    """
    results = []
    for rel, ent in repo.get_neighbors(tender_uid, direction="in",
                                        kinds=_BID_KINDS, limit=500):
        if ent.kind == BizEntityKind.COMPANY:
            results.append((ent.uid, ent.name, rel.kind.value))
    return results


def _tender_winner(repo: BizRepository, tender_uid: str) -> Optional[tuple[str, str]]:
    """Return (winner_uid, winner_name) for a tender, or None."""
    for rel, ent in repo.get_neighbors(tender_uid, direction="in",
                                        kinds=[BizRelationKind.AWARDED_TO],
                                        limit=5):
        if ent.kind == BizEntityKind.COMPANY:
            return (ent.uid, ent.name)
    return None


def _tender_buyer(repo: BizRepository, tender_uid: str
                  ) -> Optional[tuple[str, str]]:
    """Return (buyer_uid, buyer_name) via tender→issued_by→org."""
    for rel, ent in repo.get_neighbors(tender_uid, direction="out",
                                        kinds=_BUYER_KINDS, limit=5):
        if ent.kind in (BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY):
            return (ent.uid, ent.name)
    return None


def _require_entity(repo: BizRepository, uid: str,
                    allowed_kinds: Optional[list[BizEntityKind]] = None) -> dict | None:
    """Return error dict if uid not found / wrong kind, else None."""
    entity = repo.get(uid)
    if not entity:
        return {"error": f"Entity not found: {uid}"}
    if allowed_kinds and entity.kind not in allowed_kinds:
        kinds_str = ", ".join(k.value for k in allowed_kinds)
        return {"error": f"{uid} has kind '{entity.kind.value}', expected one of: {kinds_str}"}
    return None


# ═════════════════════════════════════════════════════════════════════════════
# Engine
# ═════════════════════════════════════════════════════════════════════════════

class CompetitiveIntelligenceEngine:
    """
    Read-only competitive analysis layer on top of BizRepository.

    All methods return plain dicts (JSON-serialisable).
    All methods include an ``evidence`` list for explainability.
    """

    def __init__(self, repo: BizRepository) -> None:
        self._repo = repo

    # ── Full profile ──────────────────────────────────────────────────────────

    def competitor_profile(self, uid: str) -> dict:
        """
        Assemble every competitive dimension into one response.
        Equivalent to calling all sub-methods and merging results.
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        return {
            "uid": uid,
            "name": entity.name,
            "win_rate":             self.win_rate(uid),
            "growth_trend":         self.growth_trend(uid),
            "direct_competitors":   self.direct_competitors(uid),
            "emerging_competitors": self.emerging_competitors(uid),
            "co_bidders":           self.co_bidders(uid),
            "common_losers":        self.common_losers(uid),
            "competitive_pressure": self.competitive_pressure(uid),
        }

    # ── Direct competitors ────────────────────────────────────────────────────

    def direct_competitors(self, uid: str, limit: int = 50) -> dict:
        """
        Companies competing directly: appear on the same tenders or share
        the same buyer organisations.  Ranked by co-occurrence frequency.
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        # co-occurrence counter: competitor_uid → {tender_uids}
        co_tenders: dict[str, set] = defaultdict(set)
        evidence: list[dict] = []

        for t_uid, t_name, _ in tenders:
            for c_uid, c_name, c_rel in _tender_companies(self._repo, t_uid):
                if c_uid == uid:
                    continue
                co_tenders[c_uid].add(t_uid)
                evidence.append(_ev(uid, "shared_tender", t_uid, entity.name, t_name))

        # shared buyers
        co_buyers: dict[str, set] = defaultdict(set)
        for t_uid, t_name, _ in tenders:
            buyer = _tender_buyer(self._repo, t_uid)
            if not buyer:
                continue
            b_uid, b_name = buyer
            # other companies that won from this buyer
            for rel2, ent2 in self._repo.get_neighbors(
                b_uid, direction="in",
                kinds=[BizRelationKind.ISSUED_BY], limit=500,
            ):
                if ent2.kind != BizEntityKind.TENDER:
                    continue
                for c_uid, c_name, _ in _tender_companies(self._repo, ent2.uid):
                    if c_uid == uid:
                        continue
                    co_buyers[c_uid].add(b_uid)

        all_uids = set(co_tenders) | set(co_buyers)
        competitors = []
        for c_uid in all_uids:
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            shared_t = len(co_tenders.get(c_uid, set()))
            shared_b = len(co_buyers.get(c_uid, set()))
            score = shared_t * 1.0 + shared_b * 0.5
            competitors.append({
                "uid": c_uid,
                "name": c_ent.name,
                "shared_tenders": shared_t,
                "shared_buyers": shared_b,
                "competition_score": round(score, 2),
                "confidence": _confidence(shared_t + shared_b),
                "evidence": [
                    _ev(uid, "co_appeared_on_tender", t, entity.name)
                    for t in co_tenders.get(c_uid, set())
                ][:5],
            })

        competitors.sort(key=lambda x: x["competition_score"], reverse=True)
        return {
            "uid": uid,
            "name": entity.name,
            "competitor_count": len(competitors),
            "competitors": competitors[:limit],
            "evidence_count": len(evidence),
        }

    # ── Emerging competitors ──────────────────────────────────────────────────

    def emerging_competitors(self, uid: str, lookback_years: int = 2,
                              limit: int = 30) -> dict:
        """
        Companies that have recently started appearing on the same markets
        (buyers or industries) with growing bid frequency.

        «Emerging» = a direct competitor whose tender activity has increased
        over the last ``lookback_years`` relative to their prior period.
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        # collect candidate competitors from shared tenders
        candidates: dict[str, list[tuple[str, Optional[int]]]] = defaultdict(list)
        for t_uid, t_name, _ in tenders:
            t_ent = self._repo.get(t_uid)
            year = _parse_year(t_ent.attributes.get("valid_from") or
                               t_ent.attributes.get("date") if t_ent else None)
            for c_uid, c_name, _ in _tender_companies(self._repo, t_uid):
                if c_uid == uid:
                    continue
                candidates[c_uid].append((t_uid, year))

        import datetime
        current_year = datetime.date.today().year
        cutoff = current_year - lookback_years

        emerging = []
        for c_uid, appearances in candidates.items():
            recent = [a for a in appearances if a[1] and a[1] >= cutoff]
            older  = [a for a in appearances if a[1] and a[1] < cutoff]
            if not recent:
                continue
            # growth = recent appearances vs older; new entrant if no older history
            is_new_entrant = len(older) == 0
            growth_ratio = (len(recent) / max(len(older), 1))
            if not (is_new_entrant or growth_ratio > 1.0):
                continue
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            emerging.append({
                "uid": c_uid,
                "name": c_ent.name,
                "recent_appearances": len(recent),
                "older_appearances": len(older),
                "is_new_entrant": is_new_entrant,
                "growth_ratio": round(growth_ratio, 2),
                "confidence": _confidence(len(recent)),
                "evidence": [
                    _ev(uid, "co_tender_recent", t, entity.name)
                    for t, _ in recent[:5]
                ],
            })

        emerging.sort(key=lambda x: x["growth_ratio"], reverse=True)
        return {
            "uid": uid,
            "name": entity.name,
            "lookback_years": lookback_years,
            "emerging_count": len(emerging),
            "emerging_competitors": emerging[:limit],
        }

    # ── Co-bidders ────────────────────────────────────────────────────────────

    def co_bidders(self, uid: str, min_count: int = 1, limit: int = 50) -> dict:
        """
        Companies that frequently bid alongside this company
        (appear on the same tenders in any role).
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        co_count: dict[str, list[str]] = defaultdict(list)
        for t_uid, t_name, _ in tenders:
            for c_uid, c_name, _ in _tender_companies(self._repo, t_uid):
                if c_uid == uid:
                    continue
                co_count[c_uid].append(t_uid)

        bidders = []
        for c_uid, t_list in co_count.items():
            if len(t_list) < min_count:
                continue
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            bidders.append({
                "uid": c_uid,
                "name": c_ent.name,
                "co_bid_count": len(t_list),
                "confidence": _confidence(len(t_list)),
                "evidence": [
                    _ev(uid, "co_bid_on_tender", t, entity.name) for t in t_list[:5]
                ],
            })

        bidders.sort(key=lambda x: x["co_bid_count"], reverse=True)
        return {
            "uid": uid,
            "name": entity.name,
            "co_bidder_count": len(bidders),
            "co_bidders": bidders[:limit],
        }

    # ── Common losers ─────────────────────────────────────────────────────────

    def common_losers(self, uid: str, limit: int = 30) -> dict:
        """
        Companies that have repeatedly lost to this company on the same tenders.
        (Only meaningful when ``uid`` is a frequent winner.)
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        # Get tenders this company won
        won_tenders = [
            (t_uid, t_name)
            for t_uid, t_name, rel_kind in _company_tenders(self._repo, uid)
            if rel_kind == BizRelationKind.AWARDED_TO.value
        ]

        loser_count: dict[str, list[str]] = defaultdict(list)
        for t_uid, t_name in won_tenders:
            for c_uid, c_name, c_rel in _tender_companies(self._repo, t_uid):
                if c_uid == uid:
                    continue
                if c_rel == BizRelationKind.AWARDED_TO.value:
                    continue  # skip co-winners
                loser_count[c_uid].append(t_uid)

        losers = []
        for c_uid, t_list in loser_count.items():
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            losers.append({
                "uid": c_uid,
                "name": c_ent.name,
                "times_lost_to_winner": len(t_list),
                "confidence": _confidence(len(t_list)),
                "evidence": [
                    _ev(c_uid, "lost_tender_to", uid, c_ent.name, entity.name)
                    for t in t_list[:5]
                ],
            })

        losers.sort(key=lambda x: x["times_lost_to_winner"], reverse=True)
        return {
            "uid": uid,
            "name": entity.name,
            "common_loser_count": len(losers),
            "common_losers": losers[:limit],
        }

    # ── Buyer preferences ─────────────────────────────────────────────────────

    def buyer_preferences(self, buyer_uid: str, limit: int = 30) -> dict:
        """
        Which companies does a buyer (ORG or CMP) consistently choose?
        Returns companies ranked by award count, with win-rate context.
        """
        entity = self._repo.get(buyer_uid)
        if not entity:
            return {"error": f"Entity not found: {buyer_uid}"}
        if entity.kind not in (BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY):
            return {"error": f"{buyer_uid} is not an organisation or company"}

        # Tenders issued by this buyer
        issued_tenders: list[tuple[str, str]] = []
        for rel, ent in self._repo.get_neighbors(
            buyer_uid, direction="in",
            kinds=[BizRelationKind.ISSUED_BY], limit=2000,
        ):
            if ent.kind == BizEntityKind.TENDER:
                issued_tenders.append((ent.uid, ent.name))

        award_count: dict[str, list[str]] = defaultdict(list)
        bid_count:   dict[str, list[str]] = defaultdict(list)
        evidence: list[dict] = []

        for t_uid, t_name in issued_tenders:
            for c_uid, c_name, c_rel in _tender_companies(self._repo, t_uid):
                if c_rel == BizRelationKind.AWARDED_TO.value:
                    award_count[c_uid].append(t_uid)
                    evidence.append(_ev(buyer_uid, "awarded", t_uid, entity.name, t_name))
                else:
                    bid_count[c_uid].append(t_uid)

        all_companies = set(award_count) | set(bid_count)
        prefs = []
        for c_uid in all_companies:
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            awards = len(award_count.get(c_uid, []))
            bids   = len(bid_count.get(c_uid, []))
            total  = awards + bids
            wr     = round(awards / total, 4) if total > 0 else 0.0
            prefs.append({
                "uid": c_uid,
                "name": c_ent.name,
                "award_count": awards,
                "bid_count": bids,
                "win_rate": wr,
                "confidence": _confidence(awards),
                "evidence": [
                    _ev(buyer_uid, "awarded_contract_to", t, entity.name)
                    for t in award_count.get(c_uid, [])[:5]
                ],
            })

        prefs.sort(key=lambda x: x["award_count"], reverse=True)
        return {
            "buyer_uid": buyer_uid,
            "buyer_name": entity.name,
            "tender_count": len(issued_tenders),
            "preferred_supplier_count": len(prefs),
            "preferred_suppliers": prefs[:limit],
            "evidence_count": len(evidence),
        }

    # ── Market concentration ──────────────────────────────────────────────────

    def market_concentration(self, scope_uid: str) -> dict:
        """
        Herfindahl–Hirschman Index (HHI) and dominant-supplier analysis for
        a market defined by a buyer (ORG), industry (IND), city (CTY), or
        province (PRV) UID.
        """
        entity = self._repo.get(scope_uid)
        if not entity:
            return {"error": f"Entity not found: {scope_uid}"}

        winners = self._collect_scope_winners(scope_uid, entity)

        if not winners:
            return {
                "scope_uid": scope_uid,
                "scope_name": entity.name,
                "scope_kind": entity.kind.value,
                "hhi": 0.0,
                "concentration_level": "none",
                "dominant_supplier_count": 0,
                "dominant_suppliers": [],
                "evidence_count": 0,
            }

        win_counts = list(winners.values())
        hhi = _hhi(win_counts)
        total = sum(win_counts)

        if hhi >= 0.25:
            level = "highly_concentrated"
        elif hhi >= 0.15:
            level = "moderately_concentrated"
        else:
            level = "competitive"

        suppliers = []
        for c_uid, count in sorted(winners.items(), key=lambda x: x[1], reverse=True)[:20]:
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            share = round(count / total, 4)
            suppliers.append({
                "uid": c_uid,
                "name": c_ent.name,
                "win_count": count,
                "market_share": share,
                "confidence": _confidence(count),
            })

        return {
            "scope_uid": scope_uid,
            "scope_name": entity.name,
            "scope_kind": entity.kind.value,
            "hhi": hhi,
            "concentration_level": level,
            "total_awards": total,
            "dominant_supplier_count": len(suppliers),
            "dominant_suppliers": suppliers,
            "evidence_count": total,
        }

    def _collect_scope_winners(self, scope_uid: str, entity: Any) -> dict[str, int]:
        """Return {company_uid: award_count} for a given market scope."""
        winners: dict[str, int] = defaultdict(int)

        if entity.kind == BizEntityKind.ORGANIZATION:
            # Buyer scope: tenders issued by this org
            for rel, t_ent in self._repo.get_neighbors(
                scope_uid, direction="in",
                kinds=[BizRelationKind.ISSUED_BY], limit=2000,
            ):
                if t_ent.kind != BizEntityKind.TENDER:
                    continue
                w = _tender_winner(self._repo, t_ent.uid)
                if w:
                    winners[w[0]] += 1

        elif entity.kind in (BizEntityKind.CITY, BizEntityKind.PROVINCE):
            # Geographic scope: companies in this location
            loc_kind = (BizRelationKind.IN_CITY if entity.kind == BizEntityKind.CITY
                        else BizRelationKind.IN_PROVINCE)
            for rel, c_ent in self._repo.get_neighbors(
                scope_uid, direction="in", kinds=[loc_kind], limit=2000,
            ):
                if c_ent.kind != BizEntityKind.COMPANY:
                    continue
                # count their wins
                for t_uid, _, t_rel in _company_tenders(self._repo, c_ent.uid):
                    if t_rel == BizRelationKind.AWARDED_TO.value:
                        winners[c_ent.uid] += 1

        elif entity.kind == BizEntityKind.INDUSTRY:
            # Industry scope
            for rel, c_ent in self._repo.get_neighbors(
                scope_uid, direction="in",
                kinds=[BizRelationKind.IN_INDUSTRY], limit=2000,
            ):
                if c_ent.kind != BizEntityKind.COMPANY:
                    continue
                for t_uid, _, t_rel in _company_tenders(self._repo, c_ent.uid):
                    if t_rel == BizRelationKind.AWARDED_TO.value:
                        winners[c_ent.uid] += 1

        return dict(winners)

    # ── Market share ──────────────────────────────────────────────────────────

    def market_share(self, scope_uid: str, by: str = "company",
                     limit: int = 50) -> dict:
        """
        Percentage market share breakdown.

        ``scope_uid`` — buyer org, industry, city, or province UID.
        ``by``         — "company" | "year" | "buyer" | "city" | "province" | "industry"
        """
        entity = self._repo.get(scope_uid)
        if not entity:
            return {"error": f"Entity not found: {scope_uid}"}

        tenders = self._collect_scope_tenders(scope_uid, entity)
        if not tenders:
            return {
                "scope_uid": scope_uid,
                "scope_name": entity.name,
                "by": by,
                "total_tenders": 0,
                "shares": [],
            }

        bucket_count: dict[str, int] = defaultdict(int)
        for t_uid in tenders:
            key = self._bucket_key(t_uid, by)
            if key:
                bucket_count[key] += 1

        total = sum(bucket_count.values())
        shares = [
            {"label": k, "count": v, "share": round(v / total, 4)}
            for k, v in sorted(bucket_count.items(), key=lambda x: x[1], reverse=True)
        ]

        return {
            "scope_uid": scope_uid,
            "scope_name": entity.name,
            "by": by,
            "total_tenders": len(tenders),
            "total_in_breakdown": total,
            "shares": shares[:limit],
        }

    def _collect_scope_tenders(self, scope_uid: str, entity: Any) -> list[str]:
        """Return list of tender UIDs for a market scope."""
        tenders: list[str] = []

        if entity.kind == BizEntityKind.ORGANIZATION:
            for rel, t_ent in self._repo.get_neighbors(
                scope_uid, direction="in",
                kinds=[BizRelationKind.ISSUED_BY], limit=5000,
            ):
                if t_ent.kind == BizEntityKind.TENDER:
                    tenders.append(t_ent.uid)

        elif entity.kind in (BizEntityKind.CITY, BizEntityKind.PROVINCE):
            loc_kind = (BizRelationKind.IN_CITY if entity.kind == BizEntityKind.CITY
                        else BizRelationKind.IN_PROVINCE)
            seen: set[str] = set()
            for _, c_ent in self._repo.get_neighbors(
                scope_uid, direction="in", kinds=[loc_kind], limit=2000,
            ):
                if c_ent.kind != BizEntityKind.COMPANY:
                    continue
                for t_uid, _, _ in _company_tenders(self._repo, c_ent.uid):
                    if t_uid not in seen:
                        seen.add(t_uid)
                        tenders.append(t_uid)

        elif entity.kind == BizEntityKind.INDUSTRY:
            seen = set()
            for _, c_ent in self._repo.get_neighbors(
                scope_uid, direction="in",
                kinds=[BizRelationKind.IN_INDUSTRY], limit=2000,
            ):
                if c_ent.kind != BizEntityKind.COMPANY:
                    continue
                for t_uid, _, _ in _company_tenders(self._repo, c_ent.uid):
                    if t_uid not in seen:
                        seen.add(t_uid)
                        tenders.append(t_uid)

        elif entity.kind == BizEntityKind.COMPANY:
            tenders = [t for t, _, _ in _company_tenders(self._repo, scope_uid)]

        return tenders

    def _bucket_key(self, tender_uid: str, by: str) -> Optional[str]:
        """Return the grouping key for a tender given the ``by`` dimension."""
        t_ent = self._repo.get(tender_uid)
        if not t_ent:
            return None

        if by == "company":
            w = _tender_winner(self._repo, tender_uid)
            return w[1] if w else None

        if by == "year":
            year = _parse_year(
                t_ent.attributes.get("valid_from") or
                t_ent.attributes.get("date") or
                t_ent.attributes.get("award_date")
            )
            return str(year) if year else None

        if by == "buyer":
            b = _tender_buyer(self._repo, tender_uid)
            return b[1] if b else None

        if by == "city":
            # Try to look up the winner's city
            w = _tender_winner(self._repo, tender_uid)
            if not w:
                return None
            for _, city_ent in self._repo.get_neighbors(
                w[0], direction="out", kinds=_CITY_KINDS, limit=5,
            ):
                return city_ent.name
            return None

        if by == "province":
            w = _tender_winner(self._repo, tender_uid)
            if not w:
                return None
            for _, prov_ent in self._repo.get_neighbors(
                w[0], direction="out", kinds=_PROVINCE_KINDS, limit=5,
            ):
                return prov_ent.name
            return None

        if by == "industry":
            w = _tender_winner(self._repo, tender_uid)
            if not w:
                return None
            for _, ind_ent in self._repo.get_neighbors(
                w[0], direction="out", kinds=_INDUSTRY_KINDS, limit=5,
            ):
                return ind_ent.name
            return None

        return None

    # ── Dominant suppliers ────────────────────────────────────────────────────

    def dominant_suppliers(self, scope_uid: str, limit: int = 20) -> dict:
        """
        Top-N winning companies in a market scope (buyer org, industry,
        city, or province), with win count and market share.
        """
        entity = self._repo.get(scope_uid)
        if not entity:
            return {"error": f"Entity not found: {scope_uid}"}

        winners = self._collect_scope_winners(scope_uid, entity)
        if not winners:
            return {
                "scope_uid": scope_uid,
                "scope_name": entity.name,
                "dominant_supplier_count": 0,
                "dominant_suppliers": [],
            }

        total = sum(winners.values())
        suppliers = []
        for c_uid, count in sorted(winners.items(), key=lambda x: x[1], reverse=True)[:limit]:
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            suppliers.append({
                "uid": c_uid,
                "name": c_ent.name,
                "win_count": count,
                "market_share": round(count / total, 4) if total else 0.0,
                "confidence": _confidence(count),
                "evidence": [
                    _ev(c_uid, "awarded_in_scope", scope_uid,
                        c_ent.name, entity.name)
                ],
            })

        return {
            "scope_uid": scope_uid,
            "scope_name": entity.name,
            "scope_kind": entity.kind.value,
            "total_awards": total,
            "dominant_supplier_count": len(suppliers),
            "dominant_suppliers": suppliers,
        }

    # ── Challenger companies ──────────────────────────────────────────────────

    def challenger_companies(self, scope_uid: str, lookback_years: int = 2,
                              limit: int = 20) -> dict:
        """
        Identify rising challengers vs entrenched incumbents in a market scope.

        «Challenger» = company whose award count in the last ``lookback_years``
        is growing relative to its prior history, and it is NOT the top-ranked
        incumbent.
        """
        entity = self._repo.get(scope_uid)
        if not entity:
            return {"error": f"Entity not found: {scope_uid}"}

        import datetime
        current_year = datetime.date.today().year
        cutoff = current_year - lookback_years

        tenders = self._collect_scope_tenders(scope_uid, entity)

        # awards per company per period
        recent_wins: dict[str, int] = defaultdict(int)
        older_wins:  dict[str, int] = defaultdict(int)

        for t_uid in tenders:
            t_ent = self._repo.get(t_uid)
            year = _parse_year(
                t_ent.attributes.get("valid_from") or
                t_ent.attributes.get("date") if t_ent else None
            )
            w = _tender_winner(self._repo, t_uid)
            if not w:
                continue
            if year and year >= cutoff:
                recent_wins[w[0]] += 1
            else:
                older_wins[w[0]] += 1

        all_companies = set(recent_wins) | set(older_wins)
        total_recent = sum(recent_wins.values()) or 1

        # identify incumbent = highest total historical wins
        incumbents = sorted(
            older_wins.items(), key=lambda x: x[1], reverse=True
        )
        incumbent_uids = {uid for uid, _ in incumbents[:3]}

        challengers = []
        for c_uid in all_companies:
            if c_uid in incumbent_uids:
                continue
            rw = recent_wins.get(c_uid, 0)
            ow = older_wins.get(c_uid, 0)
            if rw == 0:
                continue
            growth = rw / max(ow, 1)
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            challengers.append({
                "uid": c_uid,
                "name": c_ent.name,
                "recent_wins": rw,
                "older_wins": ow,
                "growth_ratio": round(growth, 2),
                "recent_share": round(rw / total_recent, 4),
                "confidence": _confidence(rw),
                "evidence": [
                    _ev(c_uid, "recent_award_in_scope", scope_uid,
                        c_ent.name, entity.name)
                ],
            })

        challengers.sort(key=lambda x: x["growth_ratio"], reverse=True)

        incumbents_out = []
        for c_uid, count in incumbents[:5]:
            c_ent = self._repo.get(c_uid)
            if c_ent:
                incumbents_out.append({
                    "uid": c_uid,
                    "name": c_ent.name,
                    "historical_wins": count,
                })

        return {
            "scope_uid": scope_uid,
            "scope_name": entity.name,
            "lookback_years": lookback_years,
            "incumbent_count": len(incumbents_out),
            "incumbents": incumbents_out,
            "challenger_count": len(challengers),
            "challengers": challengers[:limit],
        }

    # ── Win rate ──────────────────────────────────────────────────────────────

    def win_rate(self, uid: str) -> dict:
        """
        Buyer-scoped Award-Weighted Dominance (BAWD) win rate.

        Measures a company's actual dominance within each buyer's procurement
        history using award frequency and, where available, total awarded
        contract value — rather than a raw bid-count ratio that requires
        SUBMITTED_BID edges.

        For each buyer the company has won from:
          freq_share  = company_wins / total_wins_at_buyer
          val_share   = company_awarded_value / total_awarded_value_at_buyer
          alpha       = fraction of that buyer's winning edges with known value
          dominance   = alpha * val_share + (1 - alpha) * freq_share

        Final score = weighted mean of per-buyer dominance, weighted by
        total_wins at each buyer.

        Range: 0.0 (never wins) → 1.0 (sole winner of every buyer).
        Method is declared in ``win_rate_method`` for downstream transparency.
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        won_tenders = [(t, n) for t, n, k in tenders
                       if k == BizRelationKind.AWARDED_TO.value]

        bawd = _bawd_score(self._repo, uid, won_tenders)
        score = bawd["score"]

        evidence = [_ev(uid, "awarded_to", t, entity.name) for t, _ in won_tenders[:10]]

        return {
            "uid":              uid,
            "name":             entity.name,
            "wins":             len(won_tenders),
            "bids":             0,
            "participations":   0,
            "total_tender_events": len(won_tenders),
            "win_rate":         score,
            "loss_rate":        round(1.0 - score, 4),
            "bid_frequency":    len(won_tenders),
            "buyer_count":      bawd["buyer_count"],
            "value_coverage":   bawd["value_coverage"],
            "win_rate_method":  "bawd",
            "per_buyer_detail": bawd["per_buyer"],
            "confidence":       _confidence(len(won_tenders)),
            "evidence":         evidence,
        }

    # ── Growth trend ──────────────────────────────────────────────────────────

    def growth_trend(self, uid: str) -> dict:
        """
        Year-over-year breakdown of tender activity (wins + bids) with
        a simple trend label (growing / stable / declining / insufficient_data).
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        yearly: dict[int, dict[str, int]] = defaultdict(lambda: {"wins": 0, "bids": 0, "total": 0})

        for t_uid, _, rel_kind in tenders:
            t_ent = self._repo.get(t_uid)
            if not t_ent:
                continue
            year = _parse_year(
                t_ent.attributes.get("valid_from") or
                t_ent.attributes.get("date") or
                t_ent.attributes.get("award_date")
            )
            if not year:
                continue
            yearly[year]["total"] += 1
            if rel_kind == BizRelationKind.AWARDED_TO.value:
                yearly[year]["wins"] += 1
            else:
                yearly[year]["bids"] += 1

        years_sorted = sorted(yearly.keys())
        timeline = [
            {
                "year": y,
                "wins": yearly[y]["wins"],
                "bids": yearly[y]["bids"],
                "total": yearly[y]["total"],
            }
            for y in years_sorted
        ]

        trend = "insufficient_data"
        if len(years_sorted) >= 2:
            totals = [yearly[y]["total"] for y in years_sorted]
            last   = totals[-1]
            prev   = totals[-2]
            if last > prev * 1.1:
                trend = "growing"
            elif last < prev * 0.9:
                trend = "declining"
            else:
                trend = "stable"

        return {
            "uid": uid,
            "name": entity.name,
            "years_active": len(years_sorted),
            "trend": trend,
            "timeline": timeline,
            "confidence": _confidence(len(tenders)),
        }

    # ── Competitor rankings ───────────────────────────────────────────────────

    def competitor_rankings(self, scope_uid: str, by: str = "wins",
                             limit: int = 30) -> dict:
        """
        Ranked company list for a market scope.

        ``by`` = "wins" | "bids" | "win_rate" | "market_share"
        """
        entity = self._repo.get(scope_uid)
        if not entity:
            return {"error": f"Entity not found: {scope_uid}"}

        tenders = self._collect_scope_tenders(scope_uid, entity)

        company_wins: dict[str, int] = defaultdict(int)
        company_bids: dict[str, int] = defaultdict(int)

        for t_uid in tenders:
            for c_uid, _, c_rel in _tender_companies(self._repo, t_uid):
                if c_rel == BizRelationKind.AWARDED_TO.value:
                    company_wins[c_uid] += 1
                else:
                    company_bids[c_uid] += 1

        all_co = set(company_wins) | set(company_bids)
        total_wins = sum(company_wins.values()) or 1

        ranked = []
        for c_uid in all_co:
            c_ent = self._repo.get(c_uid)
            if not c_ent:
                continue
            wins  = company_wins.get(c_uid, 0)
            bids  = company_bids.get(c_uid, 0)
            total = wins + bids
            wr    = round(wins / total, 4) if total else 0.0
            share = round(wins / total_wins, 4)
            ranked.append({
                "uid": c_uid,
                "name": c_ent.name,
                "wins": wins,
                "bids": bids,
                "total_appearances": total,
                "win_rate": wr,
                "market_share": share,
                "confidence": _confidence(total),
            })

        sort_key_map = {
            "wins":         lambda x: x["wins"],
            "bids":         lambda x: x["bids"],
            "win_rate":     lambda x: x["win_rate"],
            "market_share": lambda x: x["market_share"],
        }
        ranked.sort(key=sort_key_map.get(by, sort_key_map["wins"]), reverse=True)

        return {
            "scope_uid": scope_uid,
            "scope_name": entity.name,
            "by": by,
            "total_tenders_in_scope": len(tenders),
            "ranked_count": len(ranked),
            "rankings": ranked[:limit],
        }

    # ── Competitive pressure ──────────────────────────────────────────────────

    def competitive_pressure(self, uid: str) -> dict:
        """
        Composite competitive pressure score (0–1) for a company.

        Components:
        - competitor_density: how many direct competitors share ≥ 1 tender
        - win_rate_component: inverse of win rate (losing more = more pressure)
        - co_bidder_intensity: average co-bidder count per tender
        - market_concentration: HHI of the company's primary buyer(s)
        """
        err = _require_entity(self._repo, uid, [BizEntityKind.COMPANY])
        if err:
            return err

        entity = self._repo.get(uid)
        tenders = _company_tenders(self._repo, uid)

        # 1 — competitor density
        unique_competitors: set[str] = set()
        for t_uid, _, _ in tenders:
            for c_uid, _, _ in _tender_companies(self._repo, t_uid):
                if c_uid != uid:
                    unique_competitors.add(c_uid)
        competitor_density = min(1.0, len(unique_competitors) / 20.0)

        # 2 — win rate component (inverted), using BAWD so it is not 0.0
        #     when SUBMITTED_BID edges are absent
        won_tenders_cp = [(t, "") for t, _, k in tenders
                          if k == BizRelationKind.AWARDED_TO.value]
        bawd_cp   = _bawd_score(self._repo, uid, won_tenders_cp)
        win_rate_v = bawd_cp["score"]
        win_pressure = round(1.0 - win_rate_v, 4)

        # 3 — co-bidder intensity
        if tenders:
            co_bidder_per_tender = [
                len(_tender_companies(self._repo, t_uid)) - 1
                for t_uid, _, _ in tenders
            ]
            avg_co = sum(co_bidder_per_tender) / len(tenders)
            co_intensity = min(1.0, avg_co / 5.0)
        else:
            co_intensity = 0.0

        # 4 — primary buyer market concentration (HHI from buyer's perspective)
        buyer_hhi = 0.0
        buyer_uids: set[str] = set()
        for t_uid, _, _ in tenders:
            b = _tender_buyer(self._repo, t_uid)
            if b:
                buyer_uids.add(b[0])
        if buyer_uids:
            hhi_samples = []
            for b_uid in list(buyer_uids)[:3]:
                b_ent = self._repo.get(b_uid)
                if b_ent and b_ent.kind in (BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY):
                    mc = self.market_concentration(b_uid)
                    hhi_samples.append(mc.get("hhi", 0.0))
            if hhi_samples:
                buyer_hhi = sum(hhi_samples) / len(hhi_samples)

        # Composite: weighted average
        pressure = round(
            0.30 * competitor_density +
            0.35 * win_pressure +
            0.20 * co_intensity +
            0.15 * buyer_hhi,
            4
        )

        return {
            "uid": uid,
            "name": entity.name,
            "competitive_pressure_score": pressure,
            "pressure_level": (
                "high" if pressure >= 0.65 else
                "medium" if pressure >= 0.35 else
                "low"
            ),
            "components": {
                "competitor_density": round(competitor_density, 4),
                "win_pressure": win_pressure,
                "co_bidder_intensity": round(co_intensity, 4),
                "buyer_market_hhi": round(buyer_hhi, 4),
            },
            "unique_competitors": len(unique_competitors),
            "total_tender_events": len(tenders),
            "confidence": _confidence(len(tenders)),
            "evidence": [
                _ev(uid, "bid_event", t, entity.name) for t, _, _ in tenders[:10]
            ],
        }
