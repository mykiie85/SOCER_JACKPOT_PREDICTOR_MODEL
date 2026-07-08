"""Resolve SportPesa fixtures to EdgeBot's canonical team names.

Three-tier fallback per team, scoped to the detected league's team list so a
fuzzy hit can never land on a same-named club in another country:

1. mappings.json exact override (case-insensitive)
2. EdgeBot's own alias map (data_sources.kaggle_ingest._canon_team)
3. rapidfuzz token_sort_ratio >= threshold against the league's history teams

A fixture whose league is undetected, or where either team stays unresolved,
is returned with resolved=False — the predictor then prices it from
SportPesa's published odds instead of guessing.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge, canon_team
from jackpot_predictor.resolver.fuzzy_matcher import fuzzy_match_team
from jackpot_predictor.resolver.league_detector import detect_league

log = logging.getLogger(__name__)

_MAPPINGS_PATH = Path(__file__).parent / "mappings.json"


def load_mappings() -> dict[str, str]:
    try:
        data = json.loads(_MAPPINGS_PATH.read_text(encoding="utf-8"))
        return {k.lower(): v for k, v in data["sportpesa_to_edgebot"].items()}
    except (OSError, KeyError, ValueError) as e:
        log.warning("mappings.json unavailable: %s", e)
        return {}


def _resolve_team(raw: str, league_teams: set[str], mappings: dict[str, str],
                  threshold: float) -> tuple[str | None, str]:
    """Return (canonical_name, method). method is one of
    mapping/alias/exact/fuzzy/unresolved."""
    mapped = mappings.get(raw.lower())
    if mapped:
        return mapped, "mapping"
    if raw in league_teams:
        return raw, "exact"
    aliased = canon_team(raw)
    if aliased in league_teams:
        return aliased, "alias"
    # Fuzzy against the league's own teams only. Try the alias-folded spelling
    # too — it strips FC/CF suffixes and accents, which helps the scorer.
    for candidate in dict.fromkeys([raw, aliased]):
        hit, score = fuzzy_match_team(candidate, league_teams, threshold)
        if hit:
            log.info("fuzzy resolved %r -> %r (%.0f)", raw, hit, score)
            return hit, "fuzzy"
    return None, "unresolved"


def resolve_fixtures(fixtures: list[dict], bridge: EdgeBotBridge) -> list[dict]:
    """Annotate each fixture with league_code/tier and canonical team names.

    Adds keys: league_code, tier, home_team_canonical, away_team_canonical,
    resolved (bool), resolve_notes (list of str for the admin digest).
    """
    threshold = float(jackpot_config()["predictor"]["fuzzy_threshold"])
    mappings = load_mappings()
    out = []
    for fx in fixtures:
        fx = dict(fx)
        code, tier = detect_league(fx["tournament"], fx["country"])
        fx["league_code"], fx["tier"] = code, tier
        fx["resolved"] = False
        fx["resolve_notes"] = []
        if code is None:
            fx["resolve_notes"].append(
                f"league not covered: {fx['tournament']} ({fx['country']})")
            out.append(fx)
            continue
        teams = bridge.league_teams(code)
        if not teams:
            fx["resolve_notes"].append(f"no history teams for league {code}")
            out.append(fx)
            continue
        home, hm = _resolve_team(fx["home_team_raw"], teams, mappings, threshold)
        away, am = _resolve_team(fx["away_team_raw"], teams, mappings, threshold)
        fx["home_team_canonical"], fx["away_team_canonical"] = home, away
        for raw, name, method in ((fx["home_team_raw"], home, hm),
                                  (fx["away_team_raw"], away, am)):
            if name is None:
                fx["resolve_notes"].append(
                    f"unresolved team {raw!r} in {code} — add to mappings.json")
        fx["resolved"] = home is not None and away is not None
        out.append(fx)
    n_ok = sum(1 for f in out if f["resolved"])
    log.info("resolved %d/%d fixtures to EdgeBot teams", n_ok, len(out))
    return out
