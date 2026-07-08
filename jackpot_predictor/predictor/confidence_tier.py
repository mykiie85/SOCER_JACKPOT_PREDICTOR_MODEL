"""Classify 1X2 probabilities into human-readable confidence tiers."""
from __future__ import annotations

from enum import Enum

from jackpot_predictor.config.settings import jackpot_config

PICK_DISPLAY = {"H": "1", "D": "X", "A": "2"}
PICK_LABEL = {"H": "Home", "D": "Draw", "A": "Away"}


class ConfidenceTier(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    UNCERTAIN = "UNCERTAIN"


def classify_prediction(home_prob: float, draw_prob: float,
                        away_prob: float) -> dict:
    """Rank outcomes and attach a tier + one-line reasoning."""
    cfg = jackpot_config()["confidence"]
    probs = {"H": home_prob, "D": draw_prob, "A": away_prob}
    (p1, v1), (p2, v2), _ = sorted(probs.items(), key=lambda x: x[1],
                                   reverse=True)
    margin = v1 - v2

    if margin < cfg["uncertain_margin"]:
        tier = ConfidenceTier.UNCERTAIN
        reasoning = (f"Very close — {PICK_DISPLAY[p1]} only "
                     f"{margin:.1%} ahead of {PICK_DISPLAY[p2]}")
    elif (v1 > cfg["high"]["min_primary_prob"]
          and margin > cfg["high"]["min_margin"]):
        tier = ConfidenceTier.HIGH
        reasoning = f"Strong {PICK_LABEL[p1]} signal at {v1:.1%}"
    elif (v1 > cfg["medium"]["min_primary_prob"]
          and margin > cfg["medium"]["min_margin"]):
        tier = ConfidenceTier.MEDIUM
        reasoning = f"Moderate {PICK_LABEL[p1]} lean at {v1:.1%}"
    else:
        tier = ConfidenceTier.LOW
        reasoning = f"Weak signal — {v1:.1%} for {PICK_LABEL[p1]}"

    return {
        "primary_pick": p1,
        "primary_prob": v1,
        "secondary_pick": p2,
        "secondary_prob": v2,
        "margin": margin,
        "confidence_tier": tier.value,
        "reasoning": reasoning,
    }
