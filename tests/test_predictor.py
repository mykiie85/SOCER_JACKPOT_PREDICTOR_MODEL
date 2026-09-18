from jackpot_predictor.predictor.confidence_tier import classify_prediction
from jackpot_predictor.predictor.odds_fallback import implied_probabilities


def test_tiers():
    # The threshold logic still runs and is recorded; only the *published*
    # label is suspended (see test_every_published_tier_is_uncertain).
    assert classify_prediction(0.60, 0.25, 0.15)["uncapped_confidence_tier"] == "HIGH"
    assert classify_prediction(0.48, 0.30, 0.22)["uncapped_confidence_tier"] == "MEDIUM"
    assert classify_prediction(0.40, 0.35, 0.25)["uncapped_confidence_tier"] == "UNCERTAIN"
    assert classify_prediction(0.42, 0.30, 0.28)["uncapped_confidence_tier"] == "LOW"


def test_every_published_tier_is_uncertain():
    """Graded results showed the labels anti-correlated with accuracy (HIGH
    hit 33%, LOW hit 71%), so no pick may advertise confidence until an audit
    clears them."""
    for probs in ((0.60, 0.25, 0.15), (0.48, 0.30, 0.22), (0.40, 0.35, 0.25),
                  (0.42, 0.30, 0.28), (0.90, 0.06, 0.04)):
        assert classify_prediction(*probs)["confidence_tier"] == "UNCERTAIN"


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
    original_sharp = _engine.apply_sharp_odds
    _engine._collect_insights = lambda fixtures, jackpot_id: ({}, {})
    _engine.apply_sharp_odds = lambda fixtures: 0      # no Odds API in tests
    try:
        return _engine.predict_jackpot(
            [fixture], bridge=_StubBridge([model_result]))[0]
    finally:
        _engine._collect_insights = original
        _engine.apply_sharp_odds = original_sharp


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
    assert row["uncapped_confidence_tier"] == "LOW"      # capped, not HIGH
    assert row["confidence_tier"] == "UNCERTAIN"         # tiers suspended


def test_ungated_tier2_model_is_still_used():
    row = _predict(_tier2_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier2_gated": False,
                    "unknown_team": None})
    assert row["source"] == "model"
    assert abs(row["prob_home"] - 0.60) < 1e-9
    assert row["uncapped_confidence_tier"] == "HIGH"


# ── Tier-1 quality gate ──────────────────────────────────────────────────────
# Tier-1 was exempt from the rule above only because nothing read
# data_cache/tier1_gates.json, and ~80% of jackpot fixtures are tier-1. The
# 2026-09-05 walk-forward found 0 of 22 tier-1 leagues beat the closing line,
# so the same rule now applies to both tiers.

import json as _json

from jackpot_predictor.predictor import gates as _gates


def _tier1_fixture(**over):
    fx = {"event_id": "e2", "match_number": 2,
          "home_team_raw": "Arsenal", "away_team_raw": "Everton",
          "tournament": "Premier League", "country": "England",
          "kickoff_utc": "2026-08-30T15:00:00Z",
          "league_code": "E0", "tier": 1, "resolved": True,
          "home_team_canonical": "Arsenal", "away_team_canonical": "Everton",
          "odds_home": 2.0, "odds_draw": 3.5, "odds_away": 4.0}
    fx.update(over)
    return fx


def _with_gates(gates, fn):
    """Run fn() with load_tier1_gates() stubbed to `gates`."""
    original = _gates.load_tier1_gates
    _gates.load_tier1_gates = lambda: gates
    try:
        return fn()
    finally:
        _gates.load_tier1_gates = original


def test_failing_tier1_league_is_gated_out():
    assert _with_gates({"E0": {"pass": False}},
                       lambda: _gates.tier1_gate_passed("E0")) is False


def test_passing_tier1_league_clears_the_gate():
    assert _with_gates({"E0": {"pass": True}},
                       lambda: _gates.tier1_gate_passed("E0")) is True


def test_unknown_tier1_league_fails_closed():
    # No gate record is not evidence of an edge — and the measured pass rate
    # across the whole tier is zero — so silence must read as failure.
    assert _with_gates({}, lambda: _gates.tier1_gate_passed("XX1")) is False
    assert _gates.tier1_gate_passed(None) is False


def test_gated_tier1_model_never_displaces_the_market():
    row = _predict(_tier1_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier1_gated": True,
                    "tier2_gated": False, "gated": True, "unknown_team": None})
    odds = implied_probabilities(2.0, 3.5, 4.0)
    assert row["source"] == "odds_gated"
    assert abs(row["prob_home"] - odds["home"]) < 1e-9
    assert any("gated out" in n for n in row["resolve_notes"])


def test_gated_tier1_still_records_both_probabilities():
    # The pick comes from the market, but the grader needs the model's number
    # too — model-vs-market on graded slates is the only evidence that can
    # ever reopen the gate.
    row = _predict(_tier1_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier1_gated": True,
                    "tier2_gated": False, "gated": True, "unknown_team": None})
    assert abs(row["model_prob_home"] - 0.60) < 1e-9
    assert abs(row["market_prob_home"] - row["prob_home"]) < 1e-9
    assert row["model_gated"] is True


def test_gated_tier1_falls_back_to_the_model_when_no_odds_published():
    row = _predict(_tier1_fixture(odds_home=None, odds_draw=None,
                                  odds_away=None),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier1_gated": True,
                    "tier2_gated": False, "gated": True, "unknown_team": None})
    assert row["source"] == "model"
    assert row["uncapped_confidence_tier"] == "LOW"      # capped, not HIGH
    assert row["confidence_tier"] == "UNCERTAIN"         # tiers suspended


def test_ungated_tier1_model_is_still_used():
    row = _predict(_tier1_fixture(),
                   {"home": 0.60, "draw": 0.25, "away": 0.15,
                    "low_confidence": False, "tier1_gated": False,
                    "tier2_gated": False, "gated": False, "unknown_team": None})
    assert row["source"] == "model"
    assert abs(row["prob_home"] - 0.60) < 1e-9


def test_gates_file_shape_matches_what_edgebot_writes():
    """The real file must still be readable in the shape the gate expects."""
    path = _gates.gates_path()
    if not path.exists():
        return
    gates = _json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(gates, dict) and gates
    assert all("pass" in g for g in gates.values())
