"""Map a SportPesa (tournamentName, countryName) pair to an EdgeBot league code.

The jackpot feed always carries both fields, so detection is a lookup, not a
guess from team names. Codes are football-data.co.uk style ('E0', 'BR1', ...).
Tier 1 leagues are priced by the main ensemble; tier 2 by the Tier2Stack.
A miss (or an explicit None entry, e.g. Brazil Serie B) means EdgeBot has no
training data for that competition — the caller falls back to odds-implied
probabilities.
"""
from __future__ import annotations

import unicodedata

# country (normalized) -> ordered [substring-of-tournament, code] rules.
# First match wins, so put the more specific pattern first ("serie b" before
# "serie a"). A None code marks a competition we know EdgeBot cannot price.
_RULES: dict[str, list[tuple[str, str | None]]] = {
    # ---- tier 1 -----------------------------------------------------------
    "england": [("premier league", "E0"), ("championship", "E1"),
                ("league one", "E2"), ("league two", "E3")],
    "germany": [("2. bundesliga", "D2"), ("bundesliga 2", "D2"),
                ("bundesliga", "D1")],
    "spain": [("laliga 2", "SP2"), ("segunda", "SP2"), ("hypermotion", "SP2"),
              ("laliga", "SP1"), ("la liga", "SP1"), ("primera division", "SP1")],
    "italy": [("serie b", "I2"), ("serie a", "I1")],
    "france": [("ligue 2", "F2"), ("ligue 1", "F1")],
    "netherlands": [("eerste divisie", None), ("eredivisie", "N1")],
    "portugal": [("liga portugal 2", None), ("segunda", None),
                 ("primeira liga", "P1"), ("liga portugal", "P1")],
    "belgium": [("pro league", "B1"), ("first division a", "B1"),
                ("jupiler", "B1")],
    "turkey": [("super lig", "T1"), ("super league", "T1")],
    "turkiye": [("super lig", "T1"), ("super league", "T1")],
    "greece": [("super league 2", None), ("super league", "G1")],
    "scotland": [("premiership", "SC0")],
    "brazil": [("serie b", None), ("serie c", None), ("serie d", None),
               ("serie a", "BR1"), ("brasileiro", "BR1")],
    "sweden": [("superettan", None), ("allsvenskan", "SW1")],
    "norway": [("obos", None), ("1. divisjon", None), ("eliteserien", "NO1")],
    "finland": [("ykkonen", None), ("kakkonen", None),
                ("veikkausliiga", "FI1")],
    "china": [("super league", "CH1")],
    "ireland": [("first division", None), ("premier division", "IR1"),
                ("premier league", "IR1")],
    "republic of ireland": [("first division", None),
                            ("premier division", "IR1")],
    # ---- tier 2 -----------------------------------------------------------
    "argentina": [("primera nacional", None), ("primera b", None),
                  ("primera division", "AR1"), ("liga profesional", "AR1")],
    "austria": [("2. liga", None), ("bundesliga", "AT1")],
    "denmark": [("1st division", None), ("superliga", "DK1"),
                ("superligaen", "DK1")],
    "japan": [("j2", None), ("j3", None), ("j1", "JP1"), ("j.league", "JP1"),
              ("j league", "JP1")],
    "mexico": [("expansion", None), ("liga mx", "MX1")],
    "poland": [("i liga", None), ("ekstraklasa", "PL1")],
    "switzerland": [("challenge league", None), ("super league", "SZ1")],
    "usa": [("usl", None), ("mls", "US1"), ("major league soccer", "US1")],
    "united states": [("usl", None), ("mls", "US1"),
                      ("major league soccer", "US1")],
}

TIER2_CODES = {"AR1", "AT1", "DK1", "JP1", "MX1", "PL1", "SZ1", "US1", "E2", "E3"}


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


def detect_league(tournament: str, country: str) -> tuple[str | None, int | None]:
    """Return (league_code, tier) or (None, None) when EdgeBot has no coverage."""
    rules = _RULES.get(_norm(country))
    if not rules:
        return None, None
    t = _norm(tournament)
    for pattern, code in rules:
        if pattern in t:
            if code is None:
                return None, None
            return code, 2 if code in TIER2_CODES else 1
    return None, None
