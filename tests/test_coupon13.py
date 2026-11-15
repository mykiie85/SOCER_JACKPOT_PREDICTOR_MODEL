"""13/13 coupon rules (predictor/coupon13.py) and their message block."""
from itertools import product

from jackpot_predictor.predictor.coupon13 import (build_coupon, double_eligible,
                                                  score_coupon)


def _m(n, h, d, a):
    return {"match_number": n, "probs": {"H": h, "D": d, "A": a}}


def _slate():
    """17 matches: 1-4 are the weakest favourites, 5-7 the closest of the rest."""
    ms = [_m(n, 0.36, 0.33, 0.31) for n in range(1, 5)]
    ms += [_m(5, 0.40, 0.33, 0.27), _m(6, 0.41, 0.25, 0.34), _m(7, 0.42, 0.32, 0.26)]
    ms += [_m(n, 0.50 + n / 1000, 0.27, 0.23 - n / 1000) for n in range(8, 18)]
    return ms


def test_base_pick_is_the_favourite_and_13_strongest_are_played():
    c = build_coupon(_slate(), max_doubles=0)
    assert c["skipped"] == [1, 2, 3, 4]
    assert [g["match_number"] for g in c["legs"]] == list(range(5, 18))
    assert all(g["picks"] == [g["base_pick"]] == ["H"] for g in c["legs"])
    assert all(g["reason"] == "none" for g in c["legs"])
    assert c["n_lines"] == 1


def test_doubles_go_to_the_closest_matches_and_carry_a_reason():
    c = build_coupon(_slate())
    dbl = {g["match_number"]: g for g in c["legs"] if g["double"]}
    assert sorted(dbl) == [5, 6, 7]
    assert dbl[5]["picks"] == ["H", "D"] and dbl[6]["picks"] == ["H", "A"]
    assert all(g["reason"] == "odds-based" and g["note"] for g in dbl.values())
    assert c["n_lines"] == 8 and len(set(c["lines"])) == 8     # Rule 4
    # The doubled matches are the only place the lines differ.
    for i, g in enumerate(c["legs"]):
        assert len({line[i] for line in c["lines"]}) == (2 if g["double"] else 1)


def test_no_double_on_a_clear_favourite_or_a_weak_second():
    assert double_eligible({"H": 0.42, "D": 0.30, "A": 0.28})
    assert not double_eligible({"H": 0.56, "D": 0.24, "A": 0.20})   # above 55%
    assert not double_eligible({"H": 0.46, "D": 0.30, "A": 0.24})   # above 45%
    assert not double_eligible({"H": 0.44, "D": 0.27, "A": 0.29 - 0.02})
    assert not double_eligible({"H": 0.36, "D": 0.33, "A": 0.31})   # top two < 70%
    strong = [_m(n, 0.60, 0.22, 0.18) for n in range(1, 14)]
    c = build_coupon(strong)
    assert c["n_lines"] == 1 and not any(g["double"] for g in c["legs"])


def test_unpriced_match_takes_the_home_team_only_when_needed():
    ms = _slate()[:12] + [{"match_number": 13, "probs": None},
                          {"match_number": 14, "probs": None}]
    c = build_coupon(ms, max_doubles=0)
    leg = next(g for g in c["legs"] if g["match_number"] == 13)
    assert leg["picks"] == ["H"] and "home team by default" in leg["note"]
    assert c["skipped"] == [14]
    # With 13 priced matches available, the unpriced one is left out.
    c = build_coupon(_slate() + [{"match_number": 18, "probs": None}])
    assert 18 in c["skipped"]


def test_form_deviation_is_capped_logged_and_never_on_a_double():
    flags = {n: {"pick": "D", "strength": 2.0 + n / 10, "why": f"team {n} draws"}
             for n in (5, 9, 10, 11, 12)}
    c = build_coupon(_slate(), form_flags=flags, max_form=3)
    legs = {g["match_number"]: g for g in c["legs"]}
    assert legs[5]["double"] and legs[5]["reason"] == "odds-based"
    moved = [n for n, g in legs.items() if g["reason"] == "form-based"]
    assert sorted(moved) == [10, 11, 12]                 # strongest three
    assert legs[12]["picks"] == ["D"] and legs[12]["note"] == "team 12 draws"
    assert legs[9]["picks"] == ["H"] and legs[9]["form_flags"] == ["team 9 draws"]
    # No pick leaves the favourite without a reason.
    assert all(g["reason"] != "none" for g in c["legs"]
               if g["picks"] != [g["base_pick"]])


def test_score_matches_a_brute_force_count_of_the_lines():
    c = build_coupon(_slate())
    order = [g["match_number"] for g in c["legs"]]
    for res in (dict.fromkeys(order, "H"), dict.fromkeys(order, "D"),
                {n: "HDA"[n % 3] for n in order}):
        brute = {}
        for line in c["lines"]:
            k = sum(1 for n, pick in zip(order, line) if res[n] == pick)
            brute[k] = brute.get(k, 0) + 1
        sc = score_coupon(c, res)
        assert {k: v for k, v in sc["lines_by_correct"].items() if v} == brute
        assert sc["best"] == max(brute)


def test_hit_chance_matches_brute_force():
    c = build_coupon(_slate())
    brute = 0.0
    for hits in product([0, 1], repeat=13):
        if sum(hits) >= 10:
            pr = 1.0
            for h, q in zip(hits, c["cover"]):
                pr *= q if h else 1 - q
            brute += pr
    assert abs(c["p_at_least"][10] - brute) < 1e-12


def test_message_block_uses_margin_free_odds_and_the_starred_13():
    from jackpot_predictor.predictor import formatter as F
    preds = []
    for n in range(1, 18):
        close = n in (5, 6, 7)
        odds = (2.45, 2.95, 3.60) if close else (1.85, 3.55, 4.40)
        preds.append({"match_number": n, "home_team_raw": f"H{n}",
                      "away_team_raw": f"A{n}", "primary_pick": "H",
                      "primary_prob": 0.30 + n / 100, "secondary_pick": "D",
                      "secondary_prob": 0.30, "odds_home": odds[0],
                      "odds_draw": odds[1], "odds_away": odds[2]})
    jackpot = {"number_of_events": 17}
    c = F._coupon13(jackpot, preds)
    assert [g["match_number"] for g in c["legs"]] == list(range(5, 18))
    assert [g["cover"] for g in c["legs"][:4]] == ["1X", "1X", "1X", "1"]
    assert c["n_lines"] == 8 and c["cost_tzs"] == 8000
    assert c["p_10_plus"] > c["p_10_plus_single_line"]
    text = "\n".join(F.coupon13_lines(c))
    assert "5:1X" in text and "8 lines" in text and "odds-based" in text
    assert "<table" in F.coupon13_html(c)
    # Team names come from SportPesa's feed: they must not reach the email raw.
    preds[4]["home_team_raw"] = "<script>x</script>"
    html = F.coupon13_html(F._coupon13(jackpot, preds))
    assert "<script>" not in html and "&lt;script&gt;" in html
    # One unpriced match is simply left out of the 13.
    preds[16] = {"match_number": 17, "primary_pick": None}
    assert [g["match_number"] for g in F._coupon13(jackpot, preds)["legs"]] \
        == list(range(4, 17))
    # A 13-match slate, or fewer than 13 priced matches, sends no coupon.
    assert F._coupon13({"number_of_events": 13}, preds[:13]) is None
    for n in range(5):
        preds[n] = {"match_number": n + 1, "primary_pick": None}
    assert F._coupon13(jackpot, preds) is None
