"""Fetch SportPesa TZ jackpot fixtures.

The jackpot page embeds a React widget (jackpot-widget.sportpesa.co.tz) that
reads a public JSON API — we call that API directly instead of parsing HTML:

    GET https://jackpot-offer-api.sportpesa.co.tz/api/jackpots/active

The response carries the whole active jackpot: settings.numberOfEvents
(17 = Supa Jackpot, 13 = Midweek), and per event the competitors, UTC kickoff,
tournament/country and SportPesa's own 1X2 prices.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

import requests

from jackpot_predictor.config.settings import JACKPOT_API_BASE, LOGS_DIR, jackpot_config
from jackpot_predictor.scraper.cache import load_cached, save_cache

log = logging.getLogger(__name__)


class ScrapeError(RuntimeError):
    """Raised when the jackpot feed cannot be fetched or parsed."""


def _http_get(url: str) -> dict:
    cfg = jackpot_config()["scraper"]
    headers = {
        "User-Agent": cfg["user_agent"],
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://jackpot-widget.sportpesa.co.tz",
        "Referer": "https://jackpot-widget.sportpesa.co.tz/",
    }
    last_err: Exception | None = None
    for attempt in range(1, cfg["retries"] + 1):
        try:
            r = requests.get(url, headers=headers, timeout=cfg["timeout"])
            r.raise_for_status()
            return r.json()
        except Exception as e:  # noqa: BLE001 - retry any transport/parse error
            last_err = e
            log.warning("fetch attempt %d/%d failed: %s", attempt, cfg["retries"], e)
    # Keep the evidence for debugging when the feed shape/protection changes.
    try:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        (LOGS_DIR / f"scrape_error_{stamp}.txt").write_text(
            f"url={url}\nerror={last_err!r}\n", encoding="utf-8")
    except OSError:
        pass
    raise ScrapeError(f"could not fetch {url}: {last_err}")


def _jackpot_type(n_events: int) -> str:
    if n_events == 17:
        return "supa17"
    if n_events == 13:
        return "midweek13"
    return f"jackpot{n_events}"


def parse_jackpot(payload: dict) -> dict:
    """Normalize the raw API payload into the internal jackpot dict."""
    settings = payload.get("settings") or {}
    events = payload.get("events") or []
    fixtures = []
    for ev in sorted(events, key=lambda e: e.get("order", 0)):
        comp = ev.get("competitors") or []
        home = next((c["competitorName"] for c in comp if c.get("isHome")), None)
        away = next((c["competitorName"] for c in comp if not c.get("isHome")), None)
        if not home or not away:
            log.warning("event %s missing competitors — skipped", ev.get("id"))
            continue
        fixtures.append({
            "match_number": ev.get("order"),
            "event_id": ev.get("id"),
            "home_team_raw": home.strip(),
            "away_team_raw": away.strip(),
            "kickoff_utc": ev.get("utcKickOffTime"),
            "tournament": (ev.get("tournamentName") or "").strip(),
            "country": (ev.get("countryName") or "").strip(),
            "country_iso": ev.get("countryIsoCode"),
            "odds_home": ev.get("home"),
            "odds_draw": ev.get("draw"),
            "odds_away": ev.get("away"),
        })
    n = settings.get("numberOfEvents") or len(fixtures)
    kickoffs = [f["kickoff_utc"] for f in fixtures if f["kickoff_utc"]]
    return {
        "jackpot_id": payload.get("id"),
        "human_id": payload.get("humanId"),
        "jackpot_type": _jackpot_type(n),
        "number_of_events": n,
        "betting_status": payload.get("bettingStatus"),
        "first_kickoff_utc": min(kickoffs) if kickoffs else None,
        "last_kickoff_utc": max(kickoffs) if kickoffs else None,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "fixtures": fixtures,
    }


def fetch_active_jackpot(use_cache: bool = True) -> dict:
    """Return the active jackpot (normalized), using the local cache when fresh."""
    cfg = jackpot_config()["scraper"]
    if use_cache:
        cached = load_cached("active_jackpot", ttl_hours=cfg["cache_ttl_hours"])
        if cached is not None:
            log.info("using cached jackpot feed (%s)", cached.get("fetched_at_utc"))
            return cached

    payload = _http_get(f"{JACKPOT_API_BASE}/jackpots/active")
    if not payload.get("events"):
        raise ScrapeError(f"active jackpot feed has no events: {json.dumps(payload)[:300]}")

    jackpot = parse_jackpot(payload)
    n_found = len(jackpot["fixtures"])
    if n_found < jackpot["number_of_events"]:
        log.warning("incomplete jackpot: %d/%d fixtures parsed",
                    n_found, jackpot["number_of_events"])
        jackpot["incomplete"] = True

    save_cache("active_jackpot", jackpot, raw_payload=payload)
    log.info("fetched jackpot #%s (%s): %d fixtures, closes with first kickoff %s",
             jackpot["human_id"], jackpot["jackpot_type"], n_found,
             jackpot["first_kickoff_utc"])
    return jackpot
