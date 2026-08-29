"""Resolve SportPesa fixtures to EdgeBot's canonical team names.

Three-tier fallback per team, scoped to the detected league's team list so a
fuzzy hit can never land on a same-named club in another country:

1. mappings.json exact override (case-insensitive)
2. EdgeBot's own alias map (data_sources.kaggle_ingest._canon_team)
3. the affix-aware matcher in fuzzy_matcher.match_team, scoped to that
   league's roster

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
from jackpot_predictor.resolver.fuzzy_matcher import match_team
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


def _resolve_team(raw: str, league_teams, mappings: dict[str, str],
                  threshold: float, ambiguity_margin: float = 6.0,
                  counts: dict[str, int] | None = None) -> tuple[str | None, str]:
    """Return (canonical_name, method).

    mappings.json wins outright, then EdgeBot's own alias map, then the
    affix-aware matcher. The matcher is tried on both the raw spelling and the
    alias-folded one, and the first hit wins.
    """
    mapped = mappings.get(raw.lower())
    if mapped:
        return mapped, "mapping"
    if raw in league_teams:
        return raw, "exact"
    aliased = canon_team(raw)
    if aliased in league_teams:
        return aliased, "alias"
    last = "unresolved"
    for candidate in dict.fromkeys([raw, aliased]):
        hit, method, score = match_team(candidate, league_teams,
                                        threshold=threshold,
                                        ambiguity_margin=ambiguity_margin,
                                        counts=counts)
        if hit:
            log.info("resolved %r -> %r via %s (%.0f)", raw, hit, method, score)
            return hit, method
        last = method
    return None, last


def resolve_fixtures(fixtures: list[dict], bridge: EdgeBotBridge) -> list[dict]:
    """Annotate each fixture with league_code/tier and canonical team names.

    Adds keys: league_code, tier, home_team_canonical, away_team_canonical,
    resolved (bool), resolve_notes (list of str for the admin digest).
    """
    pcfg = jackpot_config()["predictor"]
    threshold = float(pcfg["fuzzy_threshold"])
    ambiguity_margin = float(pcfg.get("fuzzy_ambiguity_margin", 6.0))
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
        counts = bridge.league_team_counts(code)
        teams = set(counts)
        if not teams:
            fx["resolve_notes"].append(f"no history teams for league {code}")
            out.append(fx)
            continue
        home, hm = _resolve_team(fx["home_team_raw"], teams, mappings,
                                 threshold, ambiguity_margin, counts)
        away, am = _resolve_team(fx["away_team_raw"], teams, mappings,
                                 threshold, ambiguity_margin, counts)
        fx["home_team_canonical"], fx["away_team_canonical"] = home, away
        for raw, name, method in ((fx["home_team_raw"], home, hm),
                                  (fx["away_team_raw"], away, am)):
            if name is None:
                fx["resolve_notes"].append(
                    f"unresolved team {raw!r} in {code} [{method}] "
                    "— add to mappings.json")
        fx["resolved"] = home is not None and away is not None
        out.append(fx)
    n_ok = sum(1 for f in out if f["resolved"])
    log.info("resolved %d/%d fixtures to EdgeBot teams", n_ok, len(out))
    return out
