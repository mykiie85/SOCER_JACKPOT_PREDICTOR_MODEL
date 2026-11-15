"""Lean-against-the-crowd line, logged next to the favourite line.

The favourite line (the top pick on every match) is the most accurate line but
a poor one for money: thousands of public tickets hold the same picks, so a
win is shared. This line leans towards the outcomes the public under-picks:

    score(o) = ln p(o) - strength * ln crowd_share(o)

and takes the highest score per match. crowd_share is the share of public
tickets holding that *kind* of outcome — the favourite, the draw when it is
not the favourite, or the underdog (the other non-favourite side). Strength
and shares were fitted on past rounds on another machine, so they live in
config (``lean_line:`` in jackpot.yaml) to be refitted.

Logging only: the line is shown, archived and graded, never recommended,
staked or added to the coupon. The whole feature is off unless
``lean_line.enabled`` is true.
"""
from __future__ import annotations

import math

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.odds_fallback import implied_probabilities

OUTCOMES = ("H", "D", "A")
LINE_FIELDS = ("favourite_line", "lean_line")

_DEFAULT_SHARES = {"favourite": 0.54, "draw": 0.23, "underdog": 0.30}
# The engine's odds-only sources; their published probabilities carry the
# odds_calibration temperature, which the lean rule must not see.
_ODDS_SOURCES = ("odds", "odds_gated")


def settings() -> dict:
    cfg = jackpot_config().get("lean_line") or {}
    shares = {**_DEFAULT_SHARES, **(cfg.get("crowd_share") or {})}
    return {"enabled": bool(cfg.get("enabled", False)),
            "strength": float(cfg.get("strength", 0.5)),
            "crowd_share": {k: float(v) for k, v in shares.items()}}


def enabled() -> bool:
    return settings()["enabled"]


def lean_pick(probs: dict, strength: float, shares: dict) -> str:
    """Highest-scoring outcome for one match. ``probs`` is {H, D, A}, summing
    to 1. Ties go to the favourite, then H, D, A order."""
    fav = max(OUTCOMES, key=lambda o: probs[o])

    def share(o: str) -> float:
        if o == fav:
            return shares["favourite"]
        return shares["draw"] if o == "D" else shares["underdog"]

    def score(o: str) -> float:
        return math.log(max(probs[o], 1e-12)) - strength * math.log(share(o))

    return max(OUTCOMES, key=lambda o: (score(o), o == fav))


def _price(row: dict) -> tuple | None:
    """The 1X2 price the engine priced this match from: sharp, else SportPesa."""
    for prefix in ("sharp_odds", "odds"):
        price = tuple(row.get(f"{prefix}_{k}") for k in ("home", "draw", "away"))
        if all(v is not None for v in price):
            return price
    return None


def line_probs(row: dict) -> dict | None:
    """{H, D, A} behind the favourite pick, with the bookmaker margin removed
    but without the temperature sharpening. None when the match has no odds."""
    price = _price(row)
    if price is None:
        return None
    if row.get("primary_pick") and row.get("source") not in _ODDS_SOURCES:
        # Model-priced: the published probabilities are the pick's own.
        return {"H": row["prob_home"], "D": row["prob_draw"],
                "A": row["prob_away"]}
    p = implied_probabilities(*price, temperature=1.0)
    if p is None:
        return None
    return {"H": p["home"], "D": p["draw"], "A": p["away"]}


def match_lines(row: dict, cfg: dict | None = None) -> tuple[str, str]:
    """(favourite pick, lean pick) for one prediction row. No odds -> home win
    on both lines."""
    cfg = cfg or settings()
    probs = line_probs(row)
    if probs is None:
        return "H", "H"
    fav = max(OUTCOMES, key=lambda o: probs[o])
    return fav, lean_pick(probs, cfg["strength"], cfg["crowd_share"])


def build_lines(predictions: list[dict]) -> dict:
    """The fields saved on the round's JSON: both lines in match order
    (index 0 = match 1) plus the settings that produced them."""
    cfg = settings()
    fav, lean = [], []
    for p in sorted(predictions, key=lambda p: p["match_number"]):
        f, l = match_lines(p, cfg)
        fav.append(f)
        lean.append(l)
    return {"favourite_line": fav, "lean_line": lean,
            "lean_line_settings": {"strength": cfg["strength"],
                                   "crowd_share": cfg["crowd_share"],
                                   "probabilities": "de-vigged, temperature 1.0"}}
