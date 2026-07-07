"""
ExecutiveDecisionEngine — v0.8.0

Orchestrates CIE, RIE, CeI, BIE, and OIE into a single executive-quality
decision package.  Zero direct graph reads; all data flows through the five
sub-engine APIs.
"""
from __future__ import annotations

from typing import Any, Optional

from .repository._base import BizRepository
from .domain import BizEntityKind
from .company_intelligence import CompanyIntelligenceEngine
from .relationship_intelligence import RelationshipIntelligenceEngine
from .competitive_intelligence import CompetitiveIntelligenceEngine
from .buyer_intelligence import BuyerIntelligenceEngine
from .opportunity_intelligence import OpportunityIntelligenceEngine

# ── NOTE on sub-engine method/field names ────────────────────────────────────
# CIE:  company_summary(uid)  → {tenders_won, tenders_submitted, industries:[str], confidence_score, ...}
#       company_buyers(uid)   → {top_buyers:[{uid,name,...}], ...}
# CeI:  win_rate(uid)         → {win_rate:float, confidence:float, bid_frequency:int, ...}
#       growth_trend(uid)     → {trend:str, ...}
#       direct_competitors(uid) → {competitors:[...], ...}
#       emerging_competitors(uid) → {emerging:[...], ...}  or {competitors:[...]}
#       competitive_pressure(uid) → {competitive_pressure_score:float, pressure_level:str, ...}
# RIE:  infer_relationships(uid) → {shared_buyer_links:[...], ..., partnership_hints:[...], ...}
#       subcontractor_chains(uid) → {chains:[...], chain_count:int, ...}
#       recurring_partnerships(uid, min_count) → {partnerships:[...], ...}
# OIE:  best_opportunities(uid, limit) → {top_opportunities:[{tender_uid,score,...}],
#                                          total_tenders_scored:int, confidence:float, ...}
# ─────────────────────────────────────────────────────────────────────────────


# ── Helper functions ───────────────────────────────────────────────────────────

def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *value* to [lo, hi]."""
    return max(lo, min(hi, value))


def _blended_confidence(*scores: float) -> float:
    """Mean of scores in [0, 1]; returns 0.3 when no valid score is supplied."""
    valid = [s for s in scores if 0.0 <= s <= 1.0]
    if not valid:
        return 0.3
    return _clamp(sum(valid) / len(valid))


def _safe_get(obj: Any, *keys: str, default: Any = None) -> Any:
    """Walk nested dicts safely; return *default* if any key is missing."""
    if not isinstance(obj, dict):
        return default
    cur: Any = obj
    for k in keys:
        if not isinstance(cur, dict) or k not in cur:
            return default
        cur = cur[k]
    return cur


def _priority_label(score: float) -> str:
    """Map a 0–1 score to a priority string."""
    if score >= 0.75:
        return "critical"
    if score >= 0.5:
        return "high"
    if score >= 0.25:
        return "medium"
    return "low"


def _require_company(repo: BizRepository, uid: str) -> Optional[dict]:
    """Return an error dict if *uid* is not a COMPANY/ORGANIZATION, else None."""
    entity = repo.get(uid)
    if entity is None:
        return {"error": f"Entity not found: {uid}"}
    if entity.kind not in (BizEntityKind.COMPANY, BizEntityKind.ORGANIZATION):
        return {"error": f"Entity {uid} is a {entity.kind.value}, expected company or organization"}
    return None


# ── Engine ─────────────────────────────────────────────────────────────────────

class ExecutiveDecisionEngine:
    """
    Single orchestration layer combining all five intelligence engines.

    Constructor:
        ExecutiveDecisionEngine(repo: BizRepository)

    Public methods (all return dicts):
        company_situation(uid)
        market_position(uid)
        relationship_map(uid)
        opportunity_pipeline(uid, limit=10)
        buyer_landscape(uid)
        strategic_priorities(uid)
        risk_register(uid)
        executive_decision(uid, opportunity_limit=10)
    """

    def __init__(self, repo: BizRepository) -> None:
        self._repo = repo
        self.cie  = CompanyIntelligenceEngine(repo)
        self.rie  = RelationshipIntelligenceEngine(repo)
        self.cei  = CompetitiveIntelligenceEngine(repo)
        self.bie  = BuyerIntelligenceEngine(repo)
        self.oie  = OpportunityIntelligenceEngine(repo)

    # ── company_situation ─────────────────────────────────────────────────────

    def company_situation(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        entity = self._repo.get(uid)

        # company_summary has tenders_won/submitted, industries list, confidence_score
        summary = self.cie.company_summary(uid)

        # win_rate from CeI (BAWD score)
        wr_result = self.cei.win_rate(uid)
        win_rate: Optional[float] = None
        if isinstance(wr_result, dict) and "error" not in wr_result:
            win_rate = wr_result.get("win_rate")

        bid_count: int = 0
        if isinstance(summary, dict) and "error" not in summary:
            bid_count = (
                (summary.get("tenders_won") or 0) +
                (summary.get("tenders_submitted") or 0)
            )

        industries: list = []
        if isinstance(summary, dict) and "error" not in summary:
            raw_ind = summary.get("industries") or []
            industries = list(raw_ind)

        # growth trend label from CeI
        trend_result = self.cei.growth_trend(uid)
        trend_label: Optional[str] = None
        if isinstance(trend_result, dict) and "error" not in trend_result:
            trend_label = trend_result.get("trend")

        # top buyers from CIE company_buyers sub-profile
        buyers_result = self.cie.company_buyers(uid)
        top_buyers: list = []
        if isinstance(buyers_result, dict) and "error" not in buyers_result:
            top_buyers = (buyers_result.get("top_buyers") or [])[:5]

        # health_score: blended from win_rate and normalised bid_count
        wr_score = win_rate if win_rate is not None else 0.0
        bid_score = _clamp(bid_count / 20.0)
        health_score = _clamp(_blended_confidence(wr_score, bid_score))

        confidence_raw = float(
            _safe_get(summary, "confidence_score") or
            _safe_get(wr_result, "confidence") or
            0.3
        )
        confidence = _clamp(confidence_raw)

        return {
            "company_uid":  uid,
            "company_name": entity.name,
            "summary":      f"{entity.name} — {bid_count} bids recorded.",
            "win_rate":     win_rate,
            "bid_count":    bid_count,
            "trend":        trend_label,
            "trend_label":  trend_label,
            "top_buyers":   top_buyers,
            "industries":   industries,
            "health_score": health_score,
            "evidence":     [],
            "confidence":   confidence,
        }

    # ── market_position ───────────────────────────────────────────────────────

    def market_position(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        # Competitive pressure: returns {competitive_pressure_score, pressure_level, ...}
        cp_result = self.cei.competitive_pressure(uid)
        pressure_score: float = 0.0
        pressure_level: str = "low"
        if isinstance(cp_result, dict) and "error" not in cp_result:
            pressure_score = float(cp_result.get("competitive_pressure_score") or 0.0)
            pressure_level = cp_result.get("pressure_level") or _priority_label(pressure_score)

        # Win rate: returns {win_rate:float, confidence:float, bid_frequency:int, ...}
        wr_result = self.cei.win_rate(uid)
        win_rate: Optional[float] = None
        if isinstance(wr_result, dict) and "error" not in wr_result:
            win_rate = wr_result.get("win_rate")

        # Direct competitors: returns {competitors:[...], ...}
        dc_result = self.cei.direct_competitors(uid, limit=5)
        direct: list = []
        if isinstance(dc_result, dict) and "error" not in dc_result:
            direct = (dc_result.get("competitors") or [])[:5]

        # Emerging competitors: returns {emerging:[...] or competitors:[...], ...}
        em_result = self.cei.emerging_competitors(uid, limit=3)
        emerging: list = []
        if isinstance(em_result, dict) and "error" not in em_result:
            emerging = (
                em_result.get("emerging") or
                em_result.get("competitors") or
                []
            )[:3]

        # Growth trend label: returns {trend:str, ...}
        trend_result = self.cei.growth_trend(uid)
        trend_label: Optional[str] = None
        if isinstance(trend_result, dict) and "error" not in trend_result:
            trend_label = trend_result.get("trend")

        confidence = _blended_confidence(
            _clamp(float(_safe_get(wr_result, "confidence") or 0.3)),
            _clamp(float(_safe_get(cp_result, "confidence") or 0.3)),
        )

        # classification based on BAWD win rate
        wr = win_rate or 0.0
        if wr >= 0.5:
            classification = "incumbent"
        elif wr >= 0.2:
            classification = "challenger"
        else:
            classification = "emerging"

        return {
            "company_uid":        uid,
            "pressure_score":     _clamp(pressure_score),
            "pressure_level":     pressure_level,
            "classification":     classification,
            "win_rate":           win_rate,
            "direct_competitors": direct,
            "emerging_threats":   emerging,
            "trend_label":        trend_label,
            "evidence":           [],
            "confidence":         confidence,
        }

    # ── relationship_map ──────────────────────────────────────────────────────

    def relationship_map(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        # Inferred relationships: {shared_buyer_links, shared_competitor_links,
        #   subcontractor_hints, partnership_hints, industry_cluster_peers,
        #   geographic_cluster_peers, ...}
        inferred_result = self.rie.infer_relationships(uid)
        inferred: list = []
        if isinstance(inferred_result, dict) and "error" not in inferred_result:
            for key in ("shared_buyer_links", "shared_competitor_links",
                        "subcontractor_hints", "partnership_hints",
                        "industry_cluster_peers", "geographic_cluster_peers"):
                inferred.extend(inferred_result.get(key) or [])
        inferred = inferred[:10]

        # Subcontractor chains: {chains:[...], chain_count:int, ...}
        chains_result = self.rie.subcontractor_chains(uid)
        chains: list = []
        if isinstance(chains_result, dict) and "error" not in chains_result:
            chains = chains_result.get("chains") or []

        # Recurring partnerships (min_count=1 to include any co-occurrence)
        partners_result = self.rie.recurring_partnerships(uid, min_count=1)
        partnerships: list = []
        if isinstance(partners_result, dict) and "error" not in partners_result:
            partnerships = (partners_result.get("partnerships") or [])[:10]

        confidence = _blended_confidence(0.4)

        return {
            "company_uid":            uid,
            "partnerships":           partnerships,
            "subcontractor_chains":   chains,
            "inferred_relationships": inferred,
            "partner_count":          len(partnerships),
            "evidence":               [],
            "confidence":             confidence,
        }

    # ── opportunity_pipeline ──────────────────────────────────────────────────

    def opportunity_pipeline(self, uid: str, limit: int = 10) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        # best_opportunities returns a dict: {top_opportunities:[...], total_tenders_scored:int, ...}
        best_result = self.oie.best_opportunities(uid, limit=limit)
        top_opps: list = []
        total_scored: int = 0
        oie_confidence: float = 0.2
        if isinstance(best_result, dict) and "error" not in best_result:
            top_opps = best_result.get("top_opportunities") or []
            total_scored = best_result.get("total_tenders_scored") or len(top_opps)
            oie_confidence = float(best_result.get("confidence") or 0.2)

        pursue_threshold = 50  # score >= 50 → pursue
        pursue_count = sum(1 for o in top_opps if (o.get("score") or 0) >= pursue_threshold)

        pipeline_health = _clamp(pursue_count / max(total_scored, 1))

        next_actions: list[str] = []
        if pursue_count == 0:
            next_actions.append("No high-score opportunities found — expand search criteria.")
        else:
            next_actions.append(f"Pursue {pursue_count} scored opportunity(ies) above threshold.")

        biggest_risks: list[str] = []
        if total_scored == 0:
            biggest_risks.append("No open tenders found to score.")

        return {
            "company_uid":       uid,
            "total_scored":      total_scored,
            "pursue_count":      pursue_count,
            "pipeline_health":   pipeline_health,
            "top_opportunities": top_opps,
            "next_actions":      next_actions,
            "biggest_risks":     biggest_risks,
            "evidence":          [{"source": "oie", "count": total_scored}],
            "confidence":        _clamp(oie_confidence),
        }

    # ── buyer_landscape ───────────────────────────────────────────────────────

    def buyer_landscape(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        # Use CIE company_buyers which aggregates buyer info
        # Returns {top_buyers:[{uid, name, ...}], ...}
        buyers_result = self.cie.company_buyers(uid)
        buyers_list: list[dict] = []
        seen_buyers: set[str] = set()

        if isinstance(buyers_result, dict) and "error" not in buyers_result:
            raw_buyers = buyers_result.get("top_buyers") or buyers_result.get("buyers") or []
            for b in raw_buyers:
                if not isinstance(b, dict):
                    continue
                b_uid  = b.get("uid") or b.get("buyer_uid") or ""
                b_name = b.get("name") or b.get("buyer_name") or ""
                if not b_uid or b_uid in seen_buyers:
                    continue
                seen_buyers.add(b_uid)
                in_n = self._repo.get_neighbors(b_uid, direction="in")
                tenders_issued = sum(1 for _r, e in in_n if e.kind == BizEntityKind.TENDER)
                buyers_list.append({
                    "buyer_uid":            b_uid,
                    "buyer_name":           b_name,
                    "tenders_issued":       tenders_issued,
                    "company_is_preferred": True,
                })

        # Fallback: walk award edges → tenders → ISSUED_BY → buyer
        if not buyers_list:
            award_nbrs = self._repo.get_neighbors(uid, direction="out")
            for _rel, tender_ent in award_nbrs:
                if tender_ent.kind != BizEntityKind.TENDER:
                    continue
                t_nbrs = self._repo.get_neighbors(tender_ent.uid, direction="out")
                for _r2, b_ent in t_nbrs:
                    if b_ent.kind not in (BizEntityKind.ORGANIZATION, BizEntityKind.COMPANY):
                        continue
                    if b_ent.uid in seen_buyers:
                        continue
                    seen_buyers.add(b_ent.uid)
                    in_n = self._repo.get_neighbors(b_ent.uid, direction="in")
                    tenders_issued = sum(1 for _r, e in in_n if e.kind == BizEntityKind.TENDER)
                    buyers_list.append({
                        "buyer_uid":            b_ent.uid,
                        "buyer_name":           b_ent.name,
                        "tenders_issued":       tenders_issued,
                        "company_is_preferred": True,
                    })

        confidence = _blended_confidence(0.5 if buyers_list else 0.2)

        return {
            "company_uid":  uid,
            "buyer_count":  len(buyers_list),
            "buyers":       buyers_list,
            "evidence":     [{"source": "cie+graph", "buyer_count": len(buyers_list)}],
            "confidence":   confidence,
        }

    # ── strategic_priorities ──────────────────────────────────────────────────

    def strategic_priorities(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        priorities: list[dict] = []
        seen: set[str] = set()

        def _add(label: str, score: float, reason: str,
                 actions: list[str], extra: dict | None = None) -> None:
            key = f"{label}:{(extra or {}).get('tender_uid', (extra or {}).get('buyer_uid', ''))}"
            if key in seen:
                return
            seen.add(key)
            item: dict[str, Any] = {
                "label":   label,
                "score":   _clamp(score),
                "level":   _priority_label(score),
                "reason":  reason,
                "actions": actions,
            }
            if extra:
                item.update(extra)
            priorities.append(item)

        # Source 1: top opportunities
        pipeline = self.opportunity_pipeline(uid)
        for opp in (pipeline.get("top_opportunities") or [])[:3]:
            sc = _clamp((opp.get("score") or 0) / 100.0)
            t_uid = opp.get("tender_uid", "")
            t_name = opp.get("tender_name", t_uid)
            _add(
                label="pursue_opportunity",
                score=sc,
                reason=f"Tender '{t_name}' scored {opp.get('score', 0):.0f}/100.",
                actions=["Review tender requirements", "Submit a competitive bid"],
                extra={"tender_uid": t_uid},
            )

        # Source 2: preferred buyers (relationship deepening)
        landscape = self.buyer_landscape(uid)
        for buyer in (landscape.get("buyers") or [])[:2]:
            _add(
                label="deepen_buyer_relationship",
                score=0.55,
                reason=f"Buyer '{buyer['buyer_name']}' has issued {buyer['tenders_issued']} tender(s).",
                actions=["Schedule relationship meeting", "Target next procurement cycle"],
                extra={"buyer_uid": buyer["buyer_uid"]},
            )

        # Source 3: market position
        mp = self.market_position(uid)
        if mp.get("classification") == "emerging":
            _add(
                label="build_market_presence",
                score=0.65,
                reason="Company classified as emerging — limited track record.",
                actions=["Target smaller contracts to build record", "Partner with established firms"],
            )

        # Sort by score descending, cap at 10
        priorities.sort(key=lambda p: p["score"], reverse=True)
        priorities = priorities[:10]

        confidence = _blended_confidence(0.5)
        return {
            "company_uid": uid,
            "priorities":  priorities,
            "count":       len(priorities),
            "confidence":  confidence,
        }

    # ── risk_register ─────────────────────────────────────────────────────────

    def risk_register(self, uid: str) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        _SEV_ORDER = {"high": 3, "medium": 2, "low": 1}
        risks: list[dict] = []
        seen_keys: set[str] = set()

        def _add_risk(source: str, factor: str, severity: str,
                      detail: str, mitigation: str) -> None:
            key = f"{source}:{factor}"
            if key in seen_keys:
                return
            seen_keys.add(key)
            risks.append({
                "source":     source,
                "factor":     factor,
                "severity":   severity,
                "detail":     detail,
                "mitigation": mitigation,
            })

        # --- gather data ---
        landscape = self.buyer_landscape(uid)
        buyer_count = landscape.get("buyer_count", 0)
        buyers = landscape.get("buyers", [])

        pipeline = self.opportunity_pipeline(uid)
        total_scored = pipeline.get("total_scored", 0)

        mp = self.market_position(uid)
        pressure = mp.get("pressure_score", 0.0)

        situation = self.company_situation(uid)
        win_rate = situation.get("win_rate")
        bid_count = situation.get("bid_count", 0)

        # Risk: single buyer dependency
        if buyer_count == 1:
            _add_risk(
                source="buyer_landscape",
                factor="single_buyer_dependency",
                severity="high",
                detail="All revenue depends on a single buyer.",
                mitigation="Diversify into other procurement agencies.",
            )

        # Risk: very low win rate (bid_count >= 5 and BAWD-like win_rate < 0.15)
        if win_rate is not None and bid_count >= 5 and win_rate < 0.15:
            _add_risk(
                source="company_situation",
                factor="very_low_win_rate",
                severity="high",
                detail=f"Win rate {win_rate:.1%} is very low across {bid_count} bids.",
                mitigation="Review bid quality and pricing strategy.",
            )

        # Risk: no pipeline
        if total_scored == 0:
            _add_risk(
                source="opportunity_pipeline",
                factor="empty_pipeline",
                severity="medium",
                detail="No scoreable open tenders found.",
                mitigation="Monitor procurement portals and expand search criteria.",
            )

        # Risk: high competitive pressure
        if pressure >= 0.7:
            _add_risk(
                source="market_position",
                factor="high_competitive_pressure",
                severity="medium",
                detail=f"Competitive pressure score is {pressure:.2f}.",
                mitigation="Differentiate on quality, track record, and price.",
            )

        # Risk: competition detected (baseline — fires whenever rivals are present)
        mp_direct = mp.get("direct_competitors") or []
        if mp_direct and "high_competitive_pressure" not in seen_keys.union(
            {r["factor"] for r in risks}
        ):
            _add_risk(
                source="market_position",
                factor="competition_detected",
                severity="low",
                detail=f"{len(mp_direct)} direct competitor(s) identified in the same market.",
                mitigation="Monitor competitor activity and maintain competitive pricing.",
            )

        # Sort by severity descending
        risks.sort(key=lambda r: _SEV_ORDER.get(r["severity"], 0), reverse=True)

        overall_severities = {r["severity"] for r in risks}
        if "high" in overall_severities:
            overall_risk = "high"
        elif "medium" in overall_severities:
            overall_risk = "medium"
        else:
            overall_risk = "low"

        evidence   = [{"source": "engines", "risk_count": len(risks)}]
        confidence = _blended_confidence(0.5)

        return {
            "company_uid":  uid,
            "overall_risk": overall_risk,
            "risk_count":   len(risks),
            "risks":        risks,
            "evidence":     evidence,
            "confidence":   confidence,
        }

    # ── executive_decision ────────────────────────────────────────────────────

    def executive_decision(self, uid: str, opportunity_limit: int = 10) -> dict:
        err = _require_company(self._repo, uid)
        if err:
            return err

        entity = self._repo.get(uid)

        situation   = self.company_situation(uid)
        market      = self.market_position(uid)
        rel_map     = self.relationship_map(uid)
        pipeline    = self.opportunity_pipeline(uid, limit=opportunity_limit)
        landscape   = self.buyer_landscape(uid)
        priorities  = self.strategic_priorities(uid)
        risks       = self.risk_register(uid)

        confidence = _blended_confidence(
            situation.get("confidence", 0.3),
            market.get("confidence", 0.3),
            pipeline.get("confidence", 0.3),
            risks.get("confidence", 0.3),
        )

        # Immediate actions: top 3 priority actions + top risk mitigations
        immediate_actions: list[str] = []
        for p in (priorities.get("priorities") or [])[:2]:
            for action in (p.get("actions") or [])[:1]:
                immediate_actions.append(action)
        for r in (risks.get("risks") or [])[:1]:
            immediate_actions.append(r["mitigation"])

        # Executive narrative
        wr = situation.get("win_rate")
        wr_str = f"{wr:.0%}" if wr is not None else "unknown"
        narrative = [
            f"{entity.name} has a win rate of {wr_str} across "
            f"{situation.get('bid_count', 0)} recorded bids.",
            f"Market classification: {market.get('classification', 'unknown')}. "
            f"Competitive pressure: {market.get('pressure_score', 0.0):.2f}.",
            f"Open pipeline: {pipeline.get('total_scored', 0)} scored tender(s), "
            f"{pipeline.get('pursue_count', 0)} recommended for pursuit.",
            f"Active buyer relationships: {landscape.get('buyer_count', 0)}.",
            f"Overall risk level: {risks.get('overall_risk', 'unknown')}.",
        ]

        all_evidence: list = (
            (situation.get("evidence") or []) +
            (market.get("evidence") or []) +
            (pipeline.get("evidence") or []) +
            (risks.get("evidence") or [])
        )

        return {
            "company_uid":         uid,
            "company_name":        entity.name,
            "decision_version":    "v0.8.0",
            "confidence":          confidence,
            "executive_narrative": narrative,
            "situation":           situation,
            "market_position":     market,
            "relationship_map":    rel_map,
            "opportunity_pipeline": pipeline,
            "buyer_landscape":     landscape,
            "strategic_priorities": priorities,
            "risk_register":       risks,
            "immediate_actions":   immediate_actions,
            "evidence":            all_evidence,
        }
