"""Replace SportPesa's 1X2 prices with a sharper book's before de-vigging.

Why
---
The bot's picks are the market favourite (every model is gated), so pick
quality is exactly price quality. SportPesa's jackpot prices are copied from
bigger books with a wide margin, two days out. Pinnacle's price is the most
accurate public 1X2 number there is, and The Odds API (EdgeBot's keys, one
credit per league per run) carries Pinnacle plus ~a dozen EU books for the
leagues EdgeBot already knows. On the 2026-09 archive audit ~60% of Supa
fixtures fall in those leagues.

What
----
``apply_sharp_odds`` groups the resolved fixtures by their EdgeBot league
code, fetches each league's h2h board once, matches every fixture to an
event by kickoff time + fuzzy team names, and writes

    sharp_odds_home / sharp_odds_draw / sharp_odds_away
    sharp_book       -> "pinnacle" or "median:<n books>"

onto the fixture. Pinnacle is preferred; failing that the *median* across
books (a consensus). The best price across books is never used: the best
price is the softest book, not the sharpest. SportPesa's own prices stay in
``odds_*`` untouched, so the archive keeps both for later comparison.

Anything that fails — no key, quota gone, league not in season, no match —
leaves the fixture as it was. This must never block a run.
"""
from __future__ import annotations

import logging
import statistics
from datetime import datetime, timezone

from rapidfuzz import fuzz

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.edgebot_bridge import ensure_edgebot_on_path
from jackpot_predictor.resolver.fuzzy_matcher import core_tokens

log = logging.getLogger(__name__)

_SPORT_KEYS: dict[str, str] | None = None


def _cfg() -> dict:
    return (jackpot_config().get("predictor", {}) or {}).get("sharp_odds", {}) or {}


def sport_key_for(league_code: str | None) -> str | None:
    """EdgeBot league code -> The Odds API sport key (None if not carried)."""
    global _SPORT_KEYS
    if _SPORT_KEYS is None:
        ensure_edgebot_on_path()
        try:
            import config as ec  # EdgeBot's config.py
            inv = {v: k for k, v in ec.ODDS_API_SPORT_KEYS.items()}
            m = {code: inv[label] for code, label in ec.LEAGUE_CODES.items()
                 if label in inv}
            m.update({code: sk for sk, code in ec.TIER2_ODDS_API_SPORT_KEYS.items()})
            m.update({code: sk for sk, code in ec.TIER1_ODDS_UNLOCK_SPORT_KEYS.items()})
            _SPORT_KEYS = m
        except Exception as e:  # noqa: BLE001
            log.warning("sharp odds: EdgeBot config unavailable: %s", e)
            _SPORT_KEYS = {}
    return _SPORT_KEYS.get(league_code or "")


def _norm(name: str) -> str:
    return " ".join(core_tokens(name))


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc)


def match_event(fixture: dict, events: list[dict],
                tol_hours: float = 3.0, min_score: float = 80.0,
                margin: float = 5.0) -> dict | None:
    """The Odds API event for this fixture, or None.

    Kickoff must agree within ``tol_hours``; both team names must clear
    ``min_score`` on affix-stripped tokens and beat the runner-up event by
    ``margin`` — the same ambiguity rule as the team resolver, because a
    wrong match is far worse than keeping SportPesa's price.
    """
    ko = fixture.get("kickoff_utc")
    if not ko:
        return None
    ko_dt = _parse_iso(ko)
    h = _norm(fixture.get("home_team_raw") or "")
    a = _norm(fixture.get("away_team_raw") or "")
    best, best_s, second = None, 0.0, 0.0
    for ev in events:
        try:
            dt = _parse_iso(ev["commence_time"])
        except (KeyError, ValueError):
            continue
        if abs((dt - ko_dt).total_seconds()) > tol_hours * 3600:
            continue
        s = min(fuzz.WRatio(h, _norm(ev.get("home_team") or "")),
                fuzz.WRatio(a, _norm(ev.get("away_team") or "")))
        if s > best_s:
            second, best_s, best = best_s, s, ev
        elif s > second:
            second = s
    if best is None or best_s < min_score or best_s - second < margin:
        return None
    return best


def book_prices(event: dict, prefer: str = "pinnacle") -> tuple[dict, str] | None:
    """({home, draw, away}, label) from the preferred book, else the median."""
    home, away = event.get("home_team"), event.get("away_team")
    per_book: dict[str, dict] = {}
    for bk in event.get("bookmakers", []):
        for m in bk.get("markets", []):
            if m.get("key") != "h2h":
                continue
            prices = {o["name"]: float(o["price"]) for o in m.get("outcomes", [])}
            if home in prices and away in prices and "Draw" in prices:
                per_book[bk["key"]] = {"home": prices[home], "draw": prices["Draw"],
                                       "away": prices[away]}
    if not per_book:
        return None
    if prefer in per_book:
        return per_book[prefer], prefer
    med = {k: statistics.median(b[k] for b in per_book.values())
           for k in ("home", "draw", "away")}
    return med, f"median:{len(per_book)}"


def apply_sharp_odds(fixtures: list[dict], client=None) -> int:
    """Annotate fixtures with sharp_odds_* in place; return how many got one."""
    cfg = _cfg()
    if not cfg.get("enabled", False):
        return 0
    groups: dict[str, list[dict]] = {}
    for fx in fixtures:
        sk = sport_key_for(fx.get("league_code"))
        if sk:
            groups.setdefault(sk, []).append(fx)
    if not groups:
        log.info("sharp odds: no fixture in an Odds API league")
        return 0
    if client is None:
        try:
            ensure_edgebot_on_path()
            from data.odds_api import OddsApiClient
            client = OddsApiClient(markets="h2h")  # 1 credit per league
        except Exception as e:  # noqa: BLE001
            log.warning("sharp odds: no Odds API client (%s) — keeping SportPesa prices", e)
            return 0
    n = 0
    prefer = str(cfg.get("prefer_book", "pinnacle"))
    tol = float(cfg.get("kickoff_tolerance_hours", 3))
    min_score = float(cfg.get("min_match_score", 80))
    for sk, fxs in groups.items():
        try:
            events = client.fetch_sport(sk)
        except Exception as e:  # noqa: BLE001
            log.warning("sharp odds: %s fetch failed: %s", sk, e)
            continue
        for fx in fxs:
            ev = match_event(fx, events, tol_hours=tol, min_score=min_score)
            if ev is None:
                log.info("sharp odds: no %s event for %s vs %s", sk,
                         fx.get("home_team_raw"), fx.get("away_team_raw"))
                continue
            got = book_prices(ev, prefer)
            if got is None:
                continue
            prices, label = got
            fx.update({"sharp_odds_home": prices["home"],
                       "sharp_odds_draw": prices["draw"],
                       "sharp_odds_away": prices["away"],
                       "sharp_book": label,
                       "sharp_bookmaker_count": len(ev.get("bookmakers", []))})
            n += 1
    remaining = getattr(client, "last_remaining", None)
    log.info("sharp odds: %d/%d fixtures priced from %s (Odds API credits left: %s)",
             n, sum(len(v) for v in groups.values()), prefer, remaining)
    return n
