"""Offline tests for the Forebet/SofaScore insight matchers."""
from jackpot_predictor.insights.forebet import (_to_insight, match_fixture)
from jackpot_predictor.insights.sofascore import _votes_percent

RECORDS = [
    {"HOST_NAME": "FK Atyrau", "GUEST_NAME": "Yelimay Semey", "code": "kz",
     "Pred_1": "29", "Pred_X": "34", "Pred_2": "37",
     "host_sc_pr": "0", "guest_sc_pr": "2", "goalsavg": "2.2"},
    {"HOST_NAME": "Suva FC", "GUEST_NAME": "Ba FC", "code": "fj",
     "Pred_1": "18", "Pred_X": "34", "Pred_2": "48",
     "host_sc_pr": "0", "guest_sc_pr": "1", "goalsavg": "2.5"},
    {"HOST_NAME": "São Bernardo SP", "GUEST_NAME": "Cuiabá", "code": "br",
     "Pred_1": "29", "Pred_X": "37", "Pred_2": "34",
     "host_sc_pr": "1", "guest_sc_pr": "1", "goalsavg": "1.9"},
]


def _fx(home, away, iso):
    return {"home_team_raw": home, "away_team_raw": away, "country_iso": iso,
            "event_id": "x"}


def test_match_within_country():
    hit = match_fixture(_fx("FK Atyrau", "FC Yelimai", "KAZ"), RECORDS, 70)
    assert hit is not None
    assert hit["pick"] == "A"
    assert abs(sum(hit["probs"].values()) - 1.0) < 1e-9


def test_country_filter_blocks_false_positive():
    # Without the country filter "FC Vaajakoski" fuzzy-hits "Suva FC" (Fiji).
    hit = match_fixture(_fx("FC Vaajakoski", "FF Jaro Akademia", "FIN"),
                        RECORDS, 70)
    assert hit is None


def test_accent_folding_matches():
    hit = match_fixture(_fx("Sao Bernardo FC", "Cuiaba EC MT", "BRA"),
                        RECORDS, 70)
    assert hit is not None
    assert hit["pick"] == "D"
    assert hit["predicted_score"] == "1-1"


def test_to_insight_normalizes():
    ins = _to_insight(RECORDS[0], 90.0)
    assert ins["pick"] == "A"
    assert abs(ins["probs"]["away"] - 0.37) < 0.01


def test_votes_percent():
    v = _votes_percent({"vote": {"vote1": 54, "voteX": 57, "vote2": 58}})
    assert v is not None
    assert abs(v["home"] + v["draw"] + v["away"] - 1.0) < 1e-9
    assert v["n"] == 169
    assert _votes_percent({"vote": {}}) is None
    assert _votes_percent({}) is None
