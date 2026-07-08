from jackpot_predictor.predictor.confidence_tier import classify_prediction
from jackpot_predictor.predictor.odds_fallback import implied_probabilities


def test_tiers():
    assert classify_prediction(0.60, 0.25, 0.15)["confidence_tier"] == "HIGH"
    assert classify_prediction(0.48, 0.30, 0.22)["confidence_tier"] == "MEDIUM"
    assert classify_prediction(0.40, 0.35, 0.25)["confidence_tier"] == "UNCERTAIN"
    assert classify_prediction(0.42, 0.30, 0.28)["confidence_tier"] == "LOW"


def test_tier_fields():
    t = classify_prediction(0.60, 0.25, 0.15)
    assert t["primary_pick"] == "H"
    assert t["secondary_pick"] == "D"
    assert abs(t["margin"] - 0.35) < 1e-9


def test_implied_probabilities_devig():
    p = implied_probabilities(2.0, 3.5, 4.0)
    assert p is not None
    total = p["home"] + p["draw"] + p["away"]
    assert abs(total - 1.0) < 1e-9
    assert p["home"] > p["draw"] > p["away"]
    assert p["overround"] > 0


def test_implied_probabilities_missing_odds():
    assert implied_probabilities(None, 3.5, 4.0) is None
    assert implied_probabilities(0, 3.5, 4.0) is None
