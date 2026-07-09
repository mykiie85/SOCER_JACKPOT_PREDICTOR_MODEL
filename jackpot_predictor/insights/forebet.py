"""Forebet 1X2 predictions as a second opinion.

Forebet's prediction tables are fed by an internal JSON endpoint:

    GET https://www.forebet.com/scripts/getrs.php?ln=en&tp=1x2&in=YYYY-MM-DD&ord=0

Each record carries HOST_NAME/GUEST_NAME, Pred_1/Pred_X/Pred_2 (percent),
a predicted score, average-goals estimate and the country code — enough to
fuzzy-match jackpot fixtures *within their own country* and attach Forebet's
pick next to ours.

Matching is deliberately conservative: candidates are restricted to the
fixture's country and BOTH team names must clear ``min_match_score`` after
accent-folding and suffix-stripping. A jackpot pick built on someone else's
prediction for the wrong match is worse than no insight at all.
"""
from __future__ import annotations

import logging
import re
import unicodedata

import requests

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.scraper.cache import load_cached, save_cache

try:
    from rapidfuzz import fuzz
except ImportError:  # pragma: no cover
    fuzz = None

log = logging.getLogger(__name__)

_ENDPOINT = "https://www.forebet.com/scripts/getrs.php"

# ISO-3166 alpha-3 (SportPesa) -> alpha-2 (Forebet's `code` field).
_ISO3_TO_2 = {
    "ENG": "gb-eng", "SCO": "gb-sct", "WAL": "gb-wls", "NIR": "gb-nir",
    "ESP": "es", "GER": "de", "DEU": "de", "ITA": "it", "FRA": "fr",
    "NED": "nl", "NLD": "nl", "POR": "pt", "PRT": "pt", "BEL": "be",
    "TUR": "tr", "GRE": "gr", "GRC": "gr", "BRA": "br", "SWE": "se",
    "NOR": "no", "FIN": "fi", "CHN": "cn", "IRL": "ie", "ARG": "ar",
    "AUT": "at", "DNK": "dk", "DEN": "dk", "JPN": "jp", "MEX": "mx",
    "POL": "pl", "CHE": "ch", "SUI": "ch", "USA": "us", "KAZ": "kz",
    "URY": "uy", "URU": "uy", "BLR": "by", "ISL": "is", "ECU": "ec",
    "CHL": "cl", "CHI": "cl", "COL": "co", "PER": "pe", "PRY": "py",
    "PAR": "py", "BOL": "bo", "VEN": "ve", "RUS": "ru", "UKR": "ua",
    "CZE": "cz", "SVK": "sk", "HUN": "hu", "ROU": "ro", "ROM": "ro",
    "BGR": "bg", "BUL": "bg", "SRB": "rs", "HRV": "hr", "CRO": "hr",
    "AUS": "au", "KOR": "kr", "SAU": "sa", "KSA": "sa", "EGY": "eg",
    "MAR": "ma", "ZAF": "za", "RSA": "za", "IND": "in", "IDN": "id",
    "EST": "ee", "LVA": "lv", "LAT": "lv", "LTU": "lt", "MDA": "md",
    "CAN": "ca", "NZL": "nz",
}

_SUFFIXES = re.compile(
    r"\b(fc|fk|cf|cs|cd|ca|sc|sd|ec|sk|if|bk|afc|ac|club|de|deportivo|"
    r"atletico|athletic)\b")


def _fold(name: str) -> str:
    s = unicodedata.normalize("NFKD", name or "")
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = _SUFFIXES.sub(" ", s)
    return re.sub(r"\s+", " ", s).strip()


def _side_score(a: str, b: str) -> float:
    if fuzz is None:
        return 100.0 if _fold(a) == _fold(b) else 0.0
    fa, fb = _fold(a), _fold(b)
    return max(fuzz.token_sort_ratio(fa, fb), fuzz.partial_ratio(fa, fb),
               fuzz.token_set_ratio(fa, fb))


def fetch_day(date: str) -> list[dict]:
    """All Forebet 1X2 records for one date (YYYY-MM-DD), cached."""
    key = f"forebet_{date}"
    cfg = jackpot_config()["scraper"]
    cached = load_cached(key, ttl_hours=cfg["cache_ttl_hours"])
    if cached is not None:
        return cached["records"]
    headers = {"User-Agent": cfg["user_agent"],
               "Accept-Language": "en-US,en;q=0.9",
               "Referer": "https://www.forebet.com/en/football-predictions"}
    r = requests.get(_ENDPOINT, params={"ln": "en", "tp": "1x2",
                                        "in": date, "ord": 0},
                     headers=headers, timeout=cfg["timeout"])
    r.raise_for_status()
    payload = r.json()
    records = payload[0] if isinstance(payload, list) and payload else []
    from datetime import datetime, timezone
    save_cache(key, {"fetched_at_utc": datetime.now(timezone.utc).isoformat(),
                     "records": records})
    log.info("Forebet: %d records for %s", len(records), date)
    return records


def _to_insight(rec: dict, match_score: float) -> dict:
    try:
        p1, px, p2 = (float(rec["Pred_1"]) / 100, float(rec["Pred_X"]) / 100,
                      float(rec["Pred_2"]) / 100)
    except (TypeError, ValueError, KeyError):
        return {}
    total = p1 + px + p2 or 1.0
    probs = {"home": p1 / total, "draw": px / total, "away": p2 / total}
    pick = max(probs, key=probs.get)
    return {
        "probs": probs,
        "pick": {"home": "H", "draw": "D", "away": "A"}[pick],
        "predicted_score": f"{rec.get('host_sc_pr', '?')}-{rec.get('guest_sc_pr', '?')}",
        "goals_avg": rec.get("goalsavg"),
        "matched_names": f"{rec.get('HOST_NAME')} v {rec.get('GUEST_NAME')}",
        "match_score": round(match_score, 1),
    }


def match_fixture(fixture: dict, records: list[dict],
                  min_score: float) -> dict | None:
    """Best same-country record where both team names clear min_score."""
    country2 = _ISO3_TO_2.get((fixture.get("country_iso") or "").upper())
    pool = [r for r in records if r.get("code") == country2] if country2 else records
    best, best_min, best_avg = None, 0.0, 0.0
    for rec in pool:
        h = _side_score(fixture["home_team_raw"], rec.get("HOST_NAME", ""))
        a = _side_score(fixture["away_team_raw"], rec.get("GUEST_NAME", ""))
        lo, avg = min(h, a), (h + a) / 2
        if lo > best_min or (lo == best_min and avg > best_avg):
            best, best_min, best_avg = rec, lo, avg
    if best is None or best_min < min_score:
        return None
    return _to_insight(best, best_min) or None


def insights_for(fixtures: list[dict]) -> dict[str, dict]:
    """{fixture event_id: forebet insight} for every matchable fixture."""
    icfg = jackpot_config().get("insights", {}).get("forebet", {})
    if not icfg.get("enabled", True):
        return {}
    min_score = float(icfg.get("min_match_score", 70))
    by_date: dict[str, list[dict]] = {}
    out: dict[str, dict] = {}
    for fx in fixtures:
        date = (fx.get("kickoff_utc") or "")[:10]
        if not date:
            continue
        if date not in by_date:
            try:
                by_date[date] = fetch_day(date)
            except (requests.RequestException, ValueError) as e:
                log.warning("Forebet fetch failed for %s: %s", date, e)
                by_date[date] = []
        hit = match_fixture(fx, by_date[date], min_score)
        if hit:
            out[fx["event_id"]] = hit
    log.info("Forebet matched %d/%d fixtures", len(out), len(fixtures))
    return out
