"""Odds-implied 1X2 probabilities from SportPesa's own jackpot prices.

This is not a niche path: across the first seven graded jackpots, 93 of 97
settled fixtures were priced here rather than by the model, because SportPesa
builds slates out of leagues (Kazakhstan, Iceland, Serbia, Brazil Serie C,
Argentina Primera Nacional) that no public model has training data for. How
the margin is removed is therefore the single biggest lever on probability
quality in the whole system.

Two steps:

**De-vig.** Proportional de-vigging divides every implied price by the
overround, which assumes the bookmaker loads margin evenly across the three
outcomes. It does not — margin is loaded more heavily on longshots. The power
(odds-ratio) and Shin methods both model that, and both scored better than
proportional on the graded sample.

**Temperature.** ``p ** t`` renormalized, with ``t > 1`` sharpening. Fitted by
leave-one-jackpot-out CV on the graded matches. Because it is monotone it
cannot change which outcome is picked — it only re-scales stated confidence,
which is what drives the tier labels.
"""
from __future__ import annotations

import math

from jackpot_predictor.config.settings import jackpot_config


def _proportional(inv: list[float]) -> list[float]:
    total = sum(inv)
    return [v / total for v in inv]


def _power(inv: list[float]) -> list[float]:
    """Solve sum(inv_i ** k) == 1 for k, then renormalize."""
    lo, hi = 0.3, 3.0
    for _ in range(80):
        k = (lo + hi) / 2.0
        if sum(v ** k for v in inv) > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    p = [v ** k for v in inv]
    total = sum(p) or 1.0
    return [v / total for v in p]


def _shin(inv: list[float]) -> list[float]:
    """Shin (1993): margin as compensation for insider order flow."""
    s = sum(inv)
    lo, hi = 0.0, 0.4
    for _ in range(80):
        z = (lo + hi) / 2.0
        p = [(math.sqrt(z * z + 4 * (1 - z) * v * v / s) - z) / (2 * (1 - z))
             for v in inv]
        if sum(p) > 1.0:
            lo = z
        else:
            hi = z
    z = (lo + hi) / 2.0
    p = [(math.sqrt(z * z + 4 * (1 - z) * v * v / s) - z) / (2 * (1 - z))
         for v in inv]
    total = sum(p) or 1.0
    return [v / total for v in p]


_METHODS = {"proportional": _proportional, "power": _power, "shin": _shin}


def apply_temperature(probs: list[float], t: float) -> list[float]:
    if t == 1.0:
        return probs
    p = [max(v, 1e-12) ** t for v in probs]
    total = sum(p) or 1.0
    return [v / total for v in p]


def implied_probabilities(odds_home: float | None, odds_draw: float | None,
                          odds_away: float | None,
                          method: str | None = None,
                          temperature: float | None = None) -> dict | None:
    """De-vigged, calibrated {home, draw, away}, or None on a bad price."""
    try:
        inv = [1.0 / float(o) for o in (odds_home, odds_draw, odds_away)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(v <= 0 for v in inv):
        return None

    cfg = jackpot_config().get("odds_calibration", {}) or {}
    if method is None:
        method = str(cfg.get("method", "proportional"))
    if temperature is None:
        temperature = float(cfg.get("temperature", 1.0))

    devig = _METHODS.get(method, _proportional)
    raw = devig(inv)
    p = apply_temperature(raw, temperature)
    return {
        "home": p[0], "draw": p[1], "away": p[2],
        "overround": sum(inv) - 1.0,
        "devig_method": method,
        "temperature": temperature,
    }
