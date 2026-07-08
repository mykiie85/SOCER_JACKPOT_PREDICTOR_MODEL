"""Odds-implied 1X2 probabilities from SportPesa's own jackpot prices.

The jackpot feed publishes decimal odds per match. Removing the bookmaker
margin (proportional de-vig) turns them into a market-consensus probability —
the right fallback when a fixture is outside EdgeBot's training coverage
(e.g. Belarus Vysshaya Liga or Brazil Serie C in the off-season jackpots).
"""
from __future__ import annotations


def implied_probabilities(odds_home: float | None, odds_draw: float | None,
                          odds_away: float | None) -> dict | None:
    """De-vigged {home, draw, away}, or None if any price is missing/invalid."""
    try:
        inv = [1.0 / float(o) for o in (odds_home, odds_draw, odds_away)]
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    if any(v <= 0 for v in inv):
        return None
    total = sum(inv)
    return {
        "home": inv[0] / total,
        "draw": inv[1] / total,
        "away": inv[2] / total,
        "overround": total - 1.0,
    }
