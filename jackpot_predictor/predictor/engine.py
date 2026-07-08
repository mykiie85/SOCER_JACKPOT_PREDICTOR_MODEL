"""Turn resolved fixtures into ranked 1X2 predictions.

Source per fixture:
- "model"  — EdgeBot ensemble (tier 1) or Tier2Stack (tier 2)
- "blend"  — model blended with odds-implied (config predictor.model_weight < 1)
- "odds"   — de-vigged SportPesa prices (league/teams outside EdgeBot coverage)
- None     — nothing could price it (no model, no odds); the pick is skipped

A model prediction that EdgeBot itself flags low_confidence (e.g. a promoted
team missing from recent league history) is capped at the LOW tier — the
probability came from a neutral league prior, not from information.
"""
from __future__ import annotations

import logging

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.confidence_tier import classify_prediction
from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge
from jackpot_predictor.predictor.odds_fallback import implied_probabilities

log = logging.getLogger(__name__)


def _blend(model_p: dict, odds_p: dict | None, w: float) -> tuple[dict, str]:
    if odds_p is None or w >= 1.0:
        return model_p, "model"
    mixed = {k: w * model_p[k] + (1 - w) * odds_p[k]
             for k in ("home", "draw", "away")}
    s = sum(mixed.values()) or 1.0
    return {k: v / s for k, v in mixed.items()}, "blend"


def predict_jackpot(resolved_fixtures: list[dict],
                    bridge: EdgeBotBridge | None = None) -> list[dict]:
    """Return one prediction dict per fixture (same order as input)."""
    pcfg = jackpot_config()["predictor"]
    model_weight = float(pcfg.get("model_weight", 1.0))
    odds_fallback_on = bool(pcfg.get("odds_fallback", True))

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
        if model_p is not None:
            low_conf = model_p.get("low_confidence", False)
            probs, source = _blend(
                {k: model_p[k] for k in ("home", "draw", "away")},
                odds_p, model_weight)
            if model_p.get("unknown_team"):
                fx.setdefault("resolve_notes", []).append(
                    f"EdgeBot has no recent history for {model_p['unknown_team']}")
        elif odds_fallback_on and odds_p is not None:
            probs = {k: odds_p[k] for k in ("home", "draw", "away")}
            source = "odds"

        row = {**fx, "source": source}
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
        out.append(row)
    return out
