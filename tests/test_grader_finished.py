"""The grader only grades rounds SportPesa marks Finished (never in-play scores)."""
import tempfile
from pathlib import Path

from jackpot_predictor.results import grader as G
from jackpot_predictor.scraper import archive as A

# #240 match 11: level at 90 minutes when the old grader logged it, won 2:4.
PRED = {"match_number": 11, "event_id": "sr:match:72735848", "primary_pick": "A",
        "kickoff_utc": "2026-10-25T16:00:00Z"}
ARCHIVE = {"jackpot": {"jackpot_id": "3843a176", "jackpot_type": "supa17",
                       "human_id": 240},
           "predictions": [PRED]}


def _details(status, result_pick, score):
    return {"jackpotStatus": status,
            "events": [{"eventNumber": 11, "kickoffTime": "2026-10-25T16:00:00Z",
                        "resultPick": result_pick, "score": score}]}


def _load(details):
    orig = (A.fetch_details, G.JACKPOTS_DIR)
    with tempfile.TemporaryDirectory() as d:
        A.fetch_details = lambda _id: details
        G.JACKPOTS_DIR = Path(d)
        try:
            return G.load_results(ARCHIVE, use_live=False)
        finally:
            A.fetch_details, G.JACKPOTS_DIR = orig


def test_match_still_in_play_is_not_graded():
    # The round is not Finished: even a score on the event must not be used.
    results, source = _load(_details("Active", None, "1:1"))
    assert (results, source) == ({}, "none")
    g = G.grade(ARCHIVE, results)
    assert (g["played"], g["correct"], g["pending"]) == (0, 0, 1)


def test_same_match_is_graded_correctly_once_finished():
    results, source = _load(_details("Finished", "Away", "2:4"))
    assert source == "official"
    g = G.grade(ARCHIVE, results)
    assert (g["played"], g["correct"]) == (1, 1)
    assert g["rows"][0]["actual_outcome"] == "A"
    assert g["rows"][0]["actual_score"] == (2, 4)


def test_failed_fetch_leaves_the_round_ungraded():
    def boom(_id):
        raise RuntimeError("503")
    orig = (A.fetch_details, G.JACKPOTS_DIR)
    with tempfile.TemporaryDirectory() as d:
        A.fetch_details, G.JACKPOTS_DIR = boom, Path(d)
        try:
            assert G.load_results(ARCHIVE, use_live=False) == ({}, "none")
        finally:
            A.fetch_details, G.JACKPOTS_DIR = orig


def test_public_draw_postponement_stays_void():
    results, _ = _load(_details("Finished", "Draw", "PD:X"))
    r = results[PRED["event_id"]]
    assert (r["outcome"], r["status"]) == ("D", "void")
    g = G.grade(ARCHIVE, results)
    assert (g["played"], g["void"]) == (0, 1)


def test_live_feed_public_draw_still_parses_as_void():
    # Saved live snapshots (used for re-grades) keep the old publicDraw rule.
    assert G.outcome_of({"score": None, "publicDraw": "Away"}) == ("A", None, "void")


def test_results_for_another_jackpot_are_ignored():
    details = {**_details("Finished", "Away", "2:4"), "jackpotId": "someone-else"}
    assert _load(details) == ({}, "none")
