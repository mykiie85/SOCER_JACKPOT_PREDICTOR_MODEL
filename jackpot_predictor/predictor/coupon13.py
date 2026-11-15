"""Line generator for the 13/13 game (pick 13 of the slate, prizes from 10/13).

One pure function, ``build_coupon``, used by both the live formatter and
``scripts/backtest_coupon13.py`` so the backtest scores exactly what is sent.
No config, no I/O, stdlib only.

Rules, in order:

1. **Favourite first.** The base pick on every match is the likeliest outcome
   from margin-free odds. A match with no price takes the home team.
2. **Choose 13.** The player chooses which matches to play, so the coupon
   keeps the ``n_pick`` strongest favourites and skips the rest. Unpriced
   matches are used only when fewer than ``n_pick`` are priced.
3. **Odds-based doubles.** Up to ``max_doubles`` matches are covered with
   their top two outcomes. A match qualifies only when it is close:
   favourite <= ``fav_max``, second outcome >= ``second_min``, the two
   together >= ``top2_min``, and nothing priced above ``no_double_above``.
   Among those, the highest second outcome goes first, because the second
   outcome's chance is exactly what a double adds.
4. **Form-based deviations (off in production).** ``form_flags`` can move a
   single pick away from the favourite, at most ``max_form`` per coupon and
   never on a doubled match. The walk-forward backtest decides whether this
   is ever passed in; see scripts/backtest_coupon13.py.

Every leg carries a reason tag (``odds-based`` / ``form-based`` / ``none``),
so no pick leaves the favourite without a logged reason.
"""
from __future__ import annotations

import itertools

OUTCOMES = ("H", "D", "A")
N_PICK = 13
MAX_DOUBLES = 3
PAYING_FROM = 10          # lowest paying tier of the 13/13 game


def _ranked(probs: dict) -> list[tuple[str, float]]:
    """Outcomes by probability, highest first (ties keep H, D, A order)."""
    return sorted(((o, float(probs[o])) for o in OUTCOMES),
                  key=lambda x: -x[1])


def double_eligible(probs: dict, fav_max: float = 0.45,
                    second_min: float = 0.28, top2_min: float = 0.70,
                    no_double_above: float = 0.55) -> bool:
    (_, p1), (_, p2), _ = _ranked(probs)
    return (p1 <= fav_max and p1 <= no_double_above and p2 >= second_min
            and p1 + p2 >= top2_min)


def at_least(qs: list[float], k: int) -> float:
    """P(at least k of the independent events with probabilities qs happen)."""
    dist = [1.0]
    for q in qs:
        dist = [a * (1 - q) + b * q for a, b in zip(dist + [0.0], [0.0] + dist)]
    return sum(dist[k:])


def build_coupon(matches: list[dict], *, n_pick: int = N_PICK,
                 max_doubles: int = MAX_DOUBLES, fav_max: float = 0.45,
                 second_min: float = 0.28, top2_min: float = 0.70,
                 no_double_above: float = 0.55,
                 form_flags: dict | None = None, max_form: int = 3,
                 unpriced_cover: float = 1 / 3) -> dict:
    """Build the coupon.

    matches     [{"match_number": int, "probs": {"H","D","A"} or None}, ...]
    form_flags  {match_number: {"pick": "H"|"D"|"A", "strength": float,
                                "why": str}} — optional, see module docstring.
    unpriced_cover  hit chance assumed for a home-default pick (only used for
                    the ``p_at_least`` estimates, never for selection).

    Returns {"legs": [...], "skipped": [...], "lines": [...], "n_lines": int,
    "cover": [...], "p_at_least": {10: .., 11: .., 12: .., 13: ..}}. Legs are
    in coupon (match number) order.
    """
    form_flags = form_flags or {}
    priced = [m for m in matches if m.get("probs")]
    unpriced = [m for m in matches if not m.get("probs")]
    priced.sort(key=lambda m: (-_ranked(m["probs"])[0][1], m["match_number"]))
    unpriced.sort(key=lambda m: m["match_number"])
    chosen = (priced + unpriced)[:n_pick]
    skipped = sorted(m["match_number"] for m in (priced + unpriced)[n_pick:])

    legs = {}
    for m in chosen:
        if m.get("probs"):
            (o1, p1), (o2, p2), _ = _ranked(m["probs"])
            legs[m["match_number"]] = {
                "match_number": m["match_number"], "base_pick": o1,
                "picks": [o1], "double": False, "reason": "none",
                "note": "", "form_flags": [], "fav_prob": p1,
                "second_pick": o2, "second_prob": p2, "cover": p1}
        else:
            legs[m["match_number"]] = {
                "match_number": m["match_number"], "base_pick": "H",
                "picks": ["H"], "double": False, "reason": "none",
                "note": "no odds: home team by default", "form_flags": [],
                "fav_prob": None, "second_pick": None, "second_prob": None,
                "cover": unpriced_cover}

    # Rule 3: doubles on the closest matches.
    cands = [leg for mn, leg in legs.items() if leg["fav_prob"] is not None
             and double_eligible(next(m["probs"] for m in chosen
                                      if m["match_number"] == mn),
                                 fav_max, second_min, top2_min,
                                 no_double_above)]
    cands.sort(key=lambda g: (-g["second_prob"], g["fav_prob"] - g["second_prob"],
                              g["match_number"]))
    for leg in cands[:max(0, max_doubles)]:
        leg.update(picks=[leg["base_pick"], leg["second_pick"]], double=True,
                   reason="odds-based", cover=leg["fav_prob"] + leg["second_prob"],
                   note=(f"close match: favourite {leg['fav_prob']:.0%}, "
                         f"second {leg['second_prob']:.0%}"))

    # Rule 2: form-based single-pick moves, never on a doubled match.
    moves = [(f, legs[mn]) for mn, f in form_flags.items()
             if mn in legs and not legs[mn]["double"]]
    for leg in legs.values():
        f = form_flags.get(leg["match_number"])
        if f:
            leg["form_flags"].append(f["why"])
    moves = [(f, leg) for f, leg in moves if f["pick"] != leg["base_pick"]]
    moves.sort(key=lambda x: (-x[0]["strength"], x[1]["match_number"]))
    for f, leg in moves[:max(0, max_form)]:
        leg.update(picks=[f["pick"]], reason="form-based", note=f["why"])
        probs = next(m.get("probs") for m in chosen
                     if m["match_number"] == leg["match_number"])
        leg["cover"] = float(probs[f["pick"]]) if probs else unpriced_cover

    ordered = [legs[mn] for mn in sorted(legs)]
    lines = ["".join(c) for c in itertools.product(*(g["picks"] for g in ordered))]
    assert len(set(lines)) == len(lines)      # all lines differ by construction
    cover = [g["cover"] for g in ordered]
    return {"legs": ordered, "skipped": skipped, "lines": lines,
            "n_lines": len(lines), "cover": cover,
            "p_at_least": {k: at_least(cover, k)
                           for k in range(PAYING_FROM, len(ordered) + 1)}}


def score_coupon(coupon: dict, results: dict) -> dict:
    """Score a coupon against {match_number: "H"|"D"|"A"}.

    Returns {"best": correct picks on the best line,
             "lines_by_correct": {correct: number of lines}}."""
    singles = sum(1 for g in coupon["legs"] if not g["double"]
                  and results.get(g["match_number"]) == g["picks"][0])
    doubles = [g for g in coupon["legs"] if g["double"]]
    covered = sum(1 for g in doubles if results.get(g["match_number"]) in g["picks"])
    # A covered double is right on half the lines; an uncovered one on none.
    by = {}
    free = 2 ** (len(doubles) - covered)
    for j in range(covered + 1):
        ways = 1
        for i in range(j):
            ways = ways * (covered - i) // (i + 1)
        by[singles + j] = ways * free
    return {"best": singles + covered, "lines_by_correct": by}
