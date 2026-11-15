"""Render the 13/13 coupon (predictor/coupon13.py) for Telegram, HTML and JSON.

Kept apart from formatter.py so the weekly message gains one block without
the formatter learning the coupon rules.

Probabilities: the rule thresholds (favourite <= 45%, second >= 28% ...) are
defined on margin-free odds, so this module de-vigs the price again with
temperature 1.0. The ``prob_*`` fields on a prediction are sharpened by
``odds_calibration.temperature`` and would trip the thresholds wrongly.

The 13 matches are the ones the message already stars (formatter._ranked),
so the starred list and the coupon can never disagree.
"""
from __future__ import annotations

from html import escape

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.confidence_tier import PICK_DISPLAY
from jackpot_predictor.predictor.coupon13 import N_PICK, build_coupon
from jackpot_predictor.predictor.odds_fallback import implied_probabilities

_DOUBLE_DISPLAY = {frozenset("HD"): "1X", frozenset("HA"): "12",
                   frozenset("DA"): "X2"}


def raw_probs(p: dict) -> dict | None:
    """Margin-free {H, D, A} for one prediction row, without sharpening."""
    for keys in (("sharp_odds_home", "sharp_odds_draw", "sharp_odds_away"),
                 ("odds_home", "odds_draw", "odds_away")):
        ip = implied_probabilities(*(p.get(k) for k in keys), temperature=1.0)
        if ip:
            return {"H": ip["home"], "D": ip["draw"], "A": ip["away"]}
    if p.get("prob_home") is not None:          # model-only fixture, no price
        return {"H": p["prob_home"], "D": p["prob_draw"], "A": p["prob_away"]}
    return None


def _cover(leg: dict) -> str:
    if leg["double"]:
        return _DOUBLE_DISPLAY[frozenset(leg["picks"])]
    return PICK_DISPLAY[leg["picks"][0]]


def coupon13(jackpot: dict, top: list[dict]) -> dict | None:
    """The coupon for the starred top-13 predictions, or None when it is off
    or cannot be built (short slate, an unpriced match in the 13)."""
    cfg = jackpot_config().get("coupon13") or {}
    n_dbl = int(cfg.get("doubles") or 0)
    if (not cfg.get("enabled", True) or jackpot["number_of_events"] <= N_PICK
            or len(top) != N_PICK):
        return None
    matches = [{"match_number": p["match_number"], "probs": raw_probs(p)}
               for p in top]
    if not all(m["probs"] for m in matches):
        return None
    c = build_coupon(
        matches, max_doubles=n_dbl,
        fav_max=float(cfg.get("fav_max", 0.45)),
        second_min=float(cfg.get("second_min", 0.28)),
        top2_min=float(cfg.get("top2_min", 0.70)),
        no_double_above=float(cfg.get("no_double_above", 0.55)))
    single = build_coupon(matches, max_doubles=0)
    names = {p["match_number"]: f"{p.get('home_team_raw', '?')} v "
                                f"{p.get('away_team_raw', '?')}" for p in top}
    price = int((jackpot_config().get("coupon") or {})
                .get("stake_per_line_tzs") or 1000)
    return {
        "legs": [{"match_number": g["match_number"],
                  "match": names[g["match_number"]],
                  "base_pick": PICK_DISPLAY[g["base_pick"]],
                  "double": g["double"], "cover": _cover(g),
                  "reason": g["reason"], "note": g["note"],
                  "form_flags": g["form_flags"]} for g in c["legs"]],
        "n_doubles": sum(1 for g in c["legs"] if g["double"]),
        "n_lines": c["n_lines"], "cost_tzs": c["n_lines"] * price,
        "p_10_plus": c["p_at_least"][10], "p_13": c["p_at_least"][13],
        "p_10_plus_single_line": single["p_at_least"][10],
        "backtest_note": str(cfg.get("backtest_note") or "").strip(),
    }


def coupon13_lines(c: dict | None) -> list[str]:
    """Plain-text block (Telegram; the HTML body reuses it)."""
    if not c:
        return []
    doubles = [g for g in c["legs"] if g["double"]]
    out = [
        f"🎟 {N_PICK}/{N_PICK} COUPON, {c['n_doubles']} doubles, in coupon order:",
        "  " + "  ".join(f"{g['match_number']}:{g['cover']}" for g in c["legs"]),
        f"  {c['n_lines']} lines × {c['cost_tzs'] // c['n_lines']:,} TZS = "
        f"{c['cost_tzs']:,} TZS",
    ]
    for g in doubles:
        out.append(f"  Double on {g['match_number']} ({g['cover']}), "
                   f"odds-based — {g['note']}")
    if not doubles:
        out.append("  No doubles: no match was close enough to qualify.")
    out.append("  Every other pick: the favourite, single, no deviation.")
    out.append(f"  Chance of 10+/{N_PICK}: {c['p_10_plus']:.1%} with this coupon, "
               f"{c['p_10_plus_single_line']:.1%} with the single favourite line.")
    if c["backtest_note"]:
        out.append(f"  {c['backtest_note']}")
    return out


def coupon13_html(c: dict | None) -> str:
    """Per-match table for the email: base pick, double, reason, form flags."""
    if not c:
        return ""
    rows = "".join(
        f"<tr><td>{g['match_number']}</td><td>{escape(g['match'])}</td>"
        f"<td style='text-align:center'>{g['base_pick']}</td>"
        f"<td style='text-align:center'>{'yes: ' + g['cover'] if g['double'] else 'no'}</td>"
        f"<td>{escape(g['reason'])}</td><td><small>{escape(g['note']) or '—'}</small></td>"
        f"<td><small>{escape('; '.join(g['form_flags'])) or '—'}</small></td></tr>"
        for g in c["legs"])
    head = coupon13_lines(c)
    return (f"<h3>{escape(head[0])}</h3><p><b>{escape(head[1].strip())}</b><br>"
            + "<br>".join(escape(l.strip()) for l in head[2:]) + "</p>"
            "<table border='1' cellpadding='5' cellspacing='0' "
            "style='border-collapse:collapse;border-color:#d0d7de'>"
            "<tr style='background:#f6f8fa'><th>#</th><th>Match</th>"
            "<th>Base pick</th><th>Double?</th><th>Reason</th><th>Note</th>"
            f"<th>Team-form flags</th></tr>{rows}</table>")
