import math

from jackpot_predictor.predictor import lean_line as L
from jackpot_predictor.predictor.odds_fallback import implied_probabilities

SHARES = {"favourite": 0.54, "draw": 0.23, "underdog": 0.30}
CFG = {"strength": 0.5, "crowd_share": SHARES}


def test_lean_stays_on_a_clear_favourite():
    # Home 0.60 scores -0.20; the draw (-0.78) and away (-1.11) are far behind.
    assert L.lean_pick({"H": 0.60, "D": 0.22, "A": 0.18}, 0.5, SHARES) == "H"


def test_lean_moves_to_the_draw():
    # Home -0.61, draw -0.47, away -0.60: the under-picked draw wins.
    assert L.lean_pick({"H": 0.40, "D": 0.30, "A": 0.30}, 0.5, SHARES) == "D"


def test_lean_moves_to_the_underdog():
    # Home -0.56, draw -0.87, away -0.37: the close underdog wins.
    assert L.lean_pick({"H": 0.42, "D": 0.20, "A": 0.38}, 0.5, SHARES) == "A"


def test_no_odds_picks_home_on_both_lines():
    row = {"match_number": 1, "primary_pick": None,
           "odds_home": None, "odds_draw": None, "odds_away": None}
    assert L.match_lines(row, CFG) == ("H", "H")


def test_strength_zero_is_the_favourite_line():
    probs = {"H": 0.40, "D": 0.30, "A": 0.30}
    assert L.lean_pick(probs, 0.0, SHARES) == "H"


def test_line_probs_drop_the_temperature():
    row = {"primary_pick": "H", "source": "odds_gated",
           "odds_home": 2.1, "odds_draw": 3.2, "odds_away": 3.6}
    flat = implied_probabilities(2.1, 3.2, 3.6, temperature=1.0)
    p = L.line_probs(row)
    assert all(math.isclose(p[o], flat[k]) for o, k in
               (("H", "home"), ("D", "draw"), ("A", "away")))
    assert math.isclose(sum(p.values()), 1.0)


def test_line_probs_prefer_the_sharp_price():
    row = {"primary_pick": "A", "source": "odds",
           "odds_home": 2.0, "odds_draw": 3.3, "odds_away": 4.0,
           "sharp_odds_home": 3.0, "sharp_odds_draw": 3.3, "sharp_odds_away": 2.5}
    assert max(L.line_probs(row), key=L.line_probs(row).get) == "A"


def test_lines_block_marks_the_differences():
    from jackpot_predictor.predictor import formatter as F
    block = F._lines_block({"favourite_line": ["H", "A", "H"],
                            "lean_line": ["H", "D", "A"]})
    assert block[1].split() == ["1:1", "2:2", "3:1"]
    assert block[3].split() == ["1:1", "2:X*", "3:2*"]
    assert "(2 of 3)" in block[4]
    assert F._lines_block({}) == []


def test_grade_lines_grades_each_line_like_the_picks():
    from jackpot_predictor.results.grader import grade_lines
    preds = [{"match_number": n, "event_id": f"e{n}", "primary_pick": "H"}
             for n in (1, 2, 3, 4)]
    archive = {"predictions": preds,
               "favourite_line": ["H", "H", "H", "H"],
               "lean_line": ["D", "H", "A", "D"]}
    results = {"e1": {"outcome": "D", "score": (1, 1), "status": "played"},
               "e2": {"outcome": "H", "score": (2, 0), "status": "played"},
               "e3": {"outcome": "H", "score": None, "status": "void"},
               "e4": {"outcome": None, "score": None, "status": "pending"}}
    g = grade_lines(archive, results)
    assert (g["favourite_line"]["correct"], g["favourite_line"]["played"]) == (1, 2)
    assert (g["lean_line"]["correct"], g["lean_line"]["played"]) == (2, 2)
    assert g["lean_line"]["void"] == 1 and g["lean_line"]["pending"] == 1
    assert g["lean_line"]["hits"] == [True, True, None, None]


def test_grade_lines_skips_archives_without_lines():
    from jackpot_predictor.results.grader import grade_lines
    assert grade_lines({"predictions": [{"match_number": 1}]}, {}) is None


def test_feature_is_off_by_default():
    from jackpot_predictor.config.settings import jackpot_config
    assert jackpot_config()["lean_line"]["enabled"] is False


def test_export_refreshes_only_when_data_changed():
    import os
    import tarfile
    import tempfile
    from pathlib import Path
    from jackpot_predictor.results import export as E
    with tempfile.TemporaryDirectory() as d:
        src = Path(d) / "jackpots"
        src.mkdir()
        (src / "supa17_1_results.json").write_text("{}")
        target = Path(d) / "out" / "latest.tar.gz"
        target.parent.mkdir()
        orig = (E.JACKPOTS_DIR, E.jackpot_config)
        E.JACKPOTS_DIR = src
        E.jackpot_config = lambda: {"export": {"enabled": True,
                                               "path": str(target)}}
        try:
            assert E.refresh_export() == target
            with tarfile.open(target) as t:
                assert "jackpots/supa17_1_results.json" in t.getnames()
            assert E.refresh_export() is None  # nothing changed
            later = target.stat().st_mtime + 10
            os.utime(src / "supa17_1_results.json", (later, later))
            assert E.refresh_export() == target
            assert not target.with_name(target.name + ".tmp").exists()
        finally:
            E.JACKPOTS_DIR, E.jackpot_config = orig
