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


# ── Tier-2 quality gate (Phase 0) ────────────────────────────────────────────
# EdgeBot's own walk-forward found 9 of 10 tier-2 leagues score WORSE than the
# bookmaker, and Tier2Stack marks those fixtures `tier2_gated`. The engine must
# then predict from the de-vigged price, not from the gated model — capping the
# displayed tier is not enough. These tests pin that so a future league
# addition cannot silently start displacing the market again.

from jackpot_predictor.predictor import engine as _engine


class _StubBridge:
    """Stands in for EdgeBotBridge: returns whatever predict_batch is primed with."""

    def __init__(self, results):
        self._results = results

    def predict_batch(self, fixtures):
        return list(self._results)


def _tier2_fixture(**over):
    fx = {"event_id": "e1", "match_number": 1,
          "home_team_raw": "AC Horsens", "away_team_raw": "Vejle",
          "tournament": "Superliga", "country": "Denmark",
          "kickoff_utc": "2026-08-30T15:00:00Z",
          "league_code": "DK1", "tier": 2, "resolved": True,
          "home_team_canonical": "Horsens", "away_team_canonical": "Vejle",
          "odds_home": 2.0, "odds_draw": 3.5, "odds_away": 4.0}
    fx.update(over)
    return fx


def _predict(fixture, model_result, monkeypatched=None):
    """Run predict_jackpot with insights stubbed out (no network in tests)."""
    original = _engine._collect_insights
    _engine._collect_insights = lambda fixtures, jackpot_id: ({}, {})
    try:
        return _engine.predict_jackpot(
            [fixture], bridge=_StubBridge([model_result]))[0]
    finally:
        _engine._collect_insights = original


def test_gated_tier2_model_never_displaces_the_market():
    row = _predict(_tier2_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": True, "tier2_gated": True,
                    "unknown_team": None})
    odds = implied_probabilities(2.0, 3.5, 4.0)
    assert row["source"] == "odds_gated"
    assert abs(row["prob_home"] - odds["home"]) < 1e-9
    assert any("gated out" in n for n in row["resolve_notes"])


def test_gated_tier2_falls_back_to_the_model_when_no_odds_published():
    row = _predict(_tier2_fixture(odds_home=None, odds_draw=None, odds_away=None),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": True, "tier2_gated": True,
                    "unknown_team": None})
    assert row["source"] == "model"
    assert abs(row["prob_home"] - 0.60) < 1e-9
    assert row["confidence_tier"] == "LOW"      # capped, not HIGH


def test_ungated_tier2_model_is_still_used():
    row = _predict(_tier2_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier2_gated": False,
                    "unknown_team": None})
    assert row["source"] == "model"
    assert abs(row["prob_home"] - 0.60) < 1e-9
    assert row["confidence_tier"] == "HIGH"
