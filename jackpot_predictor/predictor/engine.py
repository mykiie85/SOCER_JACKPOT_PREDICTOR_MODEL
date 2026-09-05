"""Turn resolved fixtures into ranked 1X2 predictions.

Source per fixture:
- "model"       — EdgeBot ensemble (tier 1) or Tier2Stack (tier 2)
- "blend"       — model blended with odds-implied (predictor.model_weight < 1)
- "odds"        — de-vigged SportPesa prices (outside EdgeBot coverage)
- "odds_gated"  — de-vigged prices used *instead of* an available model
                  because that league failed EdgeBot's quality gate
- None          — nothing could price it (no model, no odds); the pick is skipped

A model prediction that EdgeBot itself flags low_confidence (e.g. a promoted
team missing from recent league history) is capped at the LOW tier — the
probability came from a neutral league prior, not from information.

Gate-failed leagues are handled harder than that. A league is gated when its
rolling walk-forward Brier fails to beat the bookmaker-implied baseline by a
significant margin: Tier2Stack sets ``tier2_gated`` from
data_cache/tier2_gates.json (11 of 12 leagues fail on the 2026-08-29 run; only
RU1 passes, and only just, at 0.956 one-sided confidence), and the bridge sets
``tier1_gated`` from data_cache/tier1_gates.json (**0 of 22 tier-1 leagues
pass** on the 2026-09-05 run, over ~25,000 matches). For those, a displayed-tier
cap is not enough: the model has been *measured* as worse than the price it
would be replacing, so whenever de-vigged odds exist they are used as the
prediction and the source says so. Without odds the gated model is still better
than nothing, and the LOW cap applies.

Tier-1 used to be exempt from this purely because nothing read its gate file,
which meant ~80% of jackpot fixtures were priced by untested models. Applying
one rule to both tiers also makes adding a league safe by construction — a new
league that fails its gate can never displace the market price.

Every row also carries ``model_prob_*`` and ``market_prob_*`` whenever each is
available, gated or not. The pick comes from one of them; the grader logs both,
because model-vs-market on graded fixtures is the only out-of-sample evidence
that can ever reopen a gate.
"""
from __future__ import annotations

import logging

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.confidence_tier import classify_prediction
from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge
from jackpot_predictor.predictor.gates import gate_note
from jackpot_predictor.predictor.odds_fallback import implied_probabilities

log = logging.getLogger(__name__)


def _is_gated(model_p: dict) -> bool:
    """Gate verdict for either tier.

    ``gated`` is the bridge's union of the two per-tier flags; the individual
    keys are still honoured so a caller that sets only ``tier1_gated`` or only
    ``tier2_gated`` (tests, and Tier2Stack's own column) behaves the same.
    """
    return bool(model_p.get("gated") or model_p.get("tier1_gated")
                or model_p.get("tier2_gated"))


def _blend(model_p: dict, odds_p: dict | None, w: float) -> tuple[dict, str]:
    if odds_p is None or w >= 1.0:
        return model_p, "model"
    mixed = {k: w * model_p[k] + (1 - w) * odds_p[k]
             for k in ("home", "draw", "away")}
    s = sum(mixed.values()) or 1.0
    return {k: v / s for k, v in mixed.items()}, "blend"


def _collect_insights(fixtures: list[dict], jackpot_id: str) -> tuple[dict, dict]:
    """(forebet, sofascore) insight maps keyed by fixture event_id.

    Both sources are best-effort second opinions; any failure returns an empty
    map and the prediction run continues on model/odds alone.
    """
    forebet_map: dict = {}
    sofa_map: dict = {}
    try:
        from jackpot_predictor.insights import forebet
        forebet_map = forebet.insights_for(fixtures)
    except Exception as e:  # noqa: BLE001
        log.warning("Forebet insights skipped: %s", e)
    try:
        from jackpot_predictor.insights import sofascore
        sofa_map = sofascore.insights_for(fixtures, jackpot_id)
    except Exception as e:  # noqa: BLE001
        log.warning("SofaScore insights skipped: %s", e)
    return forebet_map, sofa_map


def _consensus(row_pick: str | None, forebet: dict | None) -> str | None:
    """Human-readable agreement note between our pick and Forebet's."""
    if not row_pick or not forebet or not forebet.get("pick"):
        return None
    if forebet["pick"] == row_pick:
        return "Forebet agrees"
    from jackpot_predictor.predictor.confidence_tier import PICK_DISPLAY
    return f"Forebet says {PICK_DISPLAY[forebet['pick']]}"


def predict_jackpot(resolved_fixtures: list[dict],
                    bridge: EdgeBotBridge | None = None,
                    jackpot_id: str = "") -> list[dict]:
    """Return one prediction dict per fixture (same order as input)."""
    pcfg = jackpot_config()["predictor"]
    model_weight = float(pcfg.get("model_weight", 1.0))
    odds_fallback_on = bool(pcfg.get("odds_fallback", True))
    icfg = jackpot_config().get("insights", {}).get("forebet", {})
    forebet_w = float(icfg.get("blend_weight", 0.0)) if icfg.get(
        "enabled", True) else 0.0
    forebet_map, sofa_map = _collect_insights(resolved_fixtures, jackpot_id)

    modelable = [f for f in resolved_fixtures if f.get("resolved")]
    model_results: dict[int, dict | None] = {}
    if modelable:
        if bridge is None:
            bridge = EdgeBotBridge()
        preds = bridge.predict_batch(modelable)
        model_results = {id(f): p for f, p in zip(modelable, preds)}

    out = []
    for fx in resolved_fixtures:
        odds_p = implied_probabilities(
            fx.get("odds_home"), fx.get("odds_draw"), fx.get("odds_away"))
        model_p = model_results.get(id(fx))

        source, probs, low_conf = None, None, False
        if model_p is not None and _is_gated(model_p) \
                and odds_fallback_on and odds_p is not None:
            # League measured as worse than the bookmaker: take the price.
            probs = {k: odds_p[k] for k in ("home", "draw", "away")}
            source = "odds_gated"
            fx.setdefault("resolve_notes", []).append(
                gate_note(fx.get("league_code"), fx.get("tier", 0)))
        elif model_p is not None:
            # Includes the gated-but-unpriced case: better than nothing, but
            # never presented as a confident pick.
            low_conf = bool(model_p.get("low_confidence")) or _is_gated(model_p)
            probs, source = _blend(
                {k: model_p[k] for k in ("home", "draw", "away")},
                odds_p, model_weight)
            if model_p.get("unknown_team"):
                fx.setdefault("resolve_notes", []).append(
                    f"EdgeBot has no recent history for {model_p['unknown_team']}")
        elif odds_fallback_on and odds_p is not None:
            probs = {k: odds_p[k] for k in ("home", "draw", "away")}
            source = "odds"

        forebet_ins = forebet_map.get(fx["event_id"])
        sofa_ins = sofa_map.get(fx["event_id"])

        # Second-opinion blend: fold Forebet's probabilities into the base
        # estimate. Applied before tiering so agreement sharpens confidence
        # and disagreement widens the margin toward UNCERTAIN — exactly the
        # behaviour a consensus should have.
        if probs is not None and forebet_ins and forebet_w > 0:
            fp = forebet_ins["probs"]
            probs = {k: (1 - forebet_w) * probs[k] + forebet_w * fp[k]
                     for k in ("home", "draw", "away")}
            s = sum(probs.values()) or 1.0
            probs = {k: v / s for k, v in probs.items()}
            source = f"{source}+forebet"

        row = {**fx, "source": source,
               "forebet": forebet_ins, "sofascore": sofa_ins,
               "model_gated": bool(model_p and _is_gated(model_p)),
               "tier1_gated": bool(model_p and model_p.get("tier1_gated")),
               "tier2_gated": bool(model_p and model_p.get("tier2_gated"))}
        # Keep both estimates on the row even when only one drives the pick —
        # results/grader.py scores them against each other on settled slates.
        if model_p is not None:
            row.update({f"model_prob_{k}": model_p[k]
                        for k in ("home", "draw", "away")})
        if odds_p is not None:
            row.update({f"market_prob_{k}": odds_p[k]
                        for k in ("home", "draw", "away")})
        if probs is None:
            row.update({"primary_pick": None,
                        "confidence_tier": "UNPRICED",
                        "reasoning": "no model coverage and no odds published"})
            log.warning("fixture %s vs %s could not be priced",
                        fx["home_team_raw"], fx["away_team_raw"])
        else:
            row.update({"prob_home": probs["home"], "prob_draw": probs["draw"],
                        "prob_away": probs["away"]})
            tier_info = classify_prediction(
                probs["home"], probs["draw"], probs["away"])
            if low_conf and tier_info["confidence_tier"] in ("HIGH", "MEDIUM"):
                tier_info["confidence_tier"] = "LOW"
                tier_info["reasoning"] += " (capped: thin team history)"
            row.update(tier_info)
            row["consensus"] = _consensus(row.get("primary_pick"), forebet_ins)
        out.append(row)
    return out
