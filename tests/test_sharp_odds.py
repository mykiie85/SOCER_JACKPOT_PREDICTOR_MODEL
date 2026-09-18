from jackpot_predictor.predictor import sharp_odds as so


def _event(home="Lazio", away="AC Milan", when="2026-09-12T16:00:00Z", books=()):
    return {"home_team": home, "away_team": away, "commence_time": when,
            "bookmakers": [
                {"key": key, "markets": [{"key": "h2h", "outcomes": [
                    {"name": home, "price": h}, {"name": "Draw", "price": d},
                    {"name": away, "price": a}]}]}
                for key, h, d, a in books]}


FX = {"home_team_raw": "Lazio Rome", "away_team_raw": "AC Milan",
      "kickoff_utc": "2026-09-12T16:00:00Z", "league_code": "I1"}


def test_match_by_kickoff_and_names():
    evs = [_event(), _event(home="Lazio", away="AC Milan", when="2026-09-13T16:00:00Z"),
           _event(home="Inter", away="AC Milan")]
    assert so.match_event(FX, evs) is evs[0]


def test_no_match_when_names_disagree_or_kickoff_far():
    assert so.match_event(FX, [_event(home="Roma")]) is None
    assert so.match_event(FX, [_event(when="2026-09-12T22:00:00Z")]) is None


def test_ambiguous_match_is_rejected():
    twins = [_event(), _event()]
    assert so.match_event(FX, twins) is None


def test_pinnacle_preferred_else_median():
    ev = _event(books=[("bet365", 2.0, 3.4, 3.8), ("pinnacle", 2.1, 3.5, 3.9),
                       ("unibet", 2.2, 3.6, 4.0)])
    prices, label = so.book_prices(ev)
    assert label == "pinnacle" and prices["home"] == 2.1
    ev = _event(books=[("bet365", 2.0, 3.4, 3.8), ("unibet", 2.2, 3.6, 4.0),
                       ("betsson", 2.4, 3.3, 3.5)])
    prices, label = so.book_prices(ev)
    assert label == "median:3" and prices["home"] == 2.2 and prices["draw"] == 3.4


def test_apply_writes_sharp_prices_and_keeps_sportpesa():
    class _Client:
        last_remaining = 99
        def fetch_sport(self, sk):
            assert sk == "soccer_italy_serie_a"
            return [_event(books=[("pinnacle", 2.1, 3.5, 3.9)])]
    so.jackpot_config = lambda: {"predictor": {"sharp_odds": {"enabled": True}}}
    so._SPORT_KEYS = {"I1": "soccer_italy_serie_a"}
    fx = {**FX, "odds_home": 2.8, "odds_draw": 3.55, "odds_away": 2.55}
    assert so.apply_sharp_odds([fx], client=_Client()) == 1
    assert fx["sharp_odds_home"] == 2.1 and fx["sharp_book"] == "pinnacle"
    assert fx["odds_home"] == 2.8


def test_disabled_config_is_a_noop():
    so.jackpot_config = lambda: {"predictor": {"sharp_odds": {"enabled": False}}}
    fx = dict(FX)
    assert so.apply_sharp_odds([fx], client=None) == 0
    assert "sharp_odds_home" not in fx
