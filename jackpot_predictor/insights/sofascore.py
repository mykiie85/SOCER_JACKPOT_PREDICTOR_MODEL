"""SofaScore pre-match insight: form, standings position, fan votes, H2H.

Transport rides on EdgeBot's ``data_sources.sofascore.SofaScoreClient``
(curl_cffi Chrome impersonation, optional residential proxy pool, scrape.do
fallback). SofaScore retired the bulk scheduled-events route, so fixtures are
located per team instead:

    search/all?q=<home team>       -> team id
    team/<id>/events/next/0        -> upcoming event vs the expected opponent
    event/<id>/pregame-form        -> last-5 form, standings position, points
    event/<id>/votes               -> fan 1X2 vote counts
    event/<id>/h2h                 -> head-to-head record

An event only counts as "found" when the opponent's name clears the fuzzy
threshold AND kickoff is within 36 h of SportPesa's — same conservatism as
the Forebet matcher. Results are cached per jackpot so re-runs cost nothing.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.insights.forebet import _fold, _side_score
from jackpot_predictor.predictor.edgebot_bridge import ensure_edgebot_on_path
from jackpot_predictor.scraper.cache import load_cached, save_cache

log = logging.getLogger(__name__)

_KICKOFF_TOLERANCE_S = 36 * 3600


def _client():
    ensure_edgebot_on_path()
    from data_sources.sofascore import SofaScoreClient
    return SofaScoreClient(min_interval=0.8)


def _kickoff_ts(fixture: dict) -> float | None:
    iso = fixture.get("kickoff_utc")
    if not iso:
        return None
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).timestamp()


def _search_team(client, name: str, threshold: float) -> dict | None:
    """Best football-team search hit for a raw SportPesa name."""
    for query in dict.fromkeys([name, _fold(name)]):
        try:
            res = client._get(f"search/all?q={query}")
        except RuntimeError as e:
            log.warning("SofaScore search blocked: %s", e)
            return None
        best, best_s = None, 0.0
        for item in (res.get("results") or []):
            ent = item.get("entity") or {}
            if item.get("type") != "team" or ent.get("sport", {}).get(
                    "slug", "football") != "football":
                continue
            s = _side_score(name, ent.get("name", ""))
            if s > best_s:
                best, best_s = ent, s
        if best is not None and best_s >= threshold:
            return best
    return None


def _find_event(client, team_id: int, fixture: dict,
                threshold: float) -> dict | None:
    """The team's upcoming event that matches opponent + kickoff window."""
    try:
        events = client._get(f"team/{team_id}/events/next/0").get("events") or []
    except RuntimeError as e:
        log.warning("SofaScore events blocked: %s", e)
        return None
    want_ts = _kickoff_ts(fixture)
    for ev in events:
        home = (ev.get("homeTeam") or {}).get("name", "")
        away = (ev.get("awayTeam") or {}).get("name", "")
        s = min(_side_score(fixture["home_team_raw"], home),
                _side_score(fixture["away_team_raw"], away))
        if s < threshold:
            continue
        ts = ev.get("startTimestamp")
        if want_ts and ts and abs(ts - want_ts) > _KICKOFF_TOLERANCE_S:
            continue
        return ev
    return None


def _votes_percent(votes: dict) -> dict | None:
    v = (votes or {}).get("vote") or {}
    counts = [v.get("vote1", 0), v.get("voteX", 0), v.get("vote2", 0)]
    total = sum(counts)
    if total <= 0:
        return None
    return {"home": counts[0] / total, "draw": counts[1] / total,
            "away": counts[2] / total, "n": total}


def _event_insight(client, event_id: int) -> dict:
    out: dict = {}
    try:
        form = client._get(f"event/{event_id}/pregame-form")
        for side in ("homeTeam", "awayTeam"):
            blk = form.get(side) or {}
            key = "home" if side == "homeTeam" else "away"
            out[f"form_{key}"] = "".join(blk.get("form") or []) or None
            out[f"position_{key}"] = blk.get("position")
            out[f"rating_{key}"] = blk.get("avgRating")
    except RuntimeError:
        pass
    try:
        out["votes"] = _votes_percent(client._get(f"event/{event_id}/votes"))
    except RuntimeError:
        out["votes"] = None
    try:
        duel = client._get(f"event/{event_id}/h2h").get("teamDuel") or {}
        if duel:
            out["h2h"] = {"home_wins": duel.get("homeWins", 0),
                          "draws": duel.get("draws", 0),
                          "away_wins": duel.get("awayWins", 0)}
    except RuntimeError:
        pass
    return out


def insights_for(fixtures: list[dict], jackpot_id: str = "") -> dict[str, dict]:
    """{fixture event_id: sofascore insight}. Failures degrade to fewer keys."""
    icfg = jackpot_config().get("insights", {}).get("sofascore", {})
    if not icfg.get("enabled", True):
        return {}
    threshold = float(icfg.get("min_match_score", 65))

    cache_key = f"sofascore_insights_{jackpot_id or 'adhoc'}"
    cached = load_cached(cache_key, ttl_hours=12)
    if cached is not None:
        return cached["insights"]

    try:
        client = _client()
    except Exception as e:  # noqa: BLE001 - insight layer must never kill a run
        log.warning("SofaScore client unavailable: %s", e)
        return {}

    out: dict[str, dict] = {}
    for fx in fixtures:
        try:
            team = _search_team(client, fx["home_team_raw"], threshold)
            if team is None:
                log.info("SofaScore: no team hit for %r", fx["home_team_raw"])
                continue
            ev = _find_event(client, team["id"], fx, threshold)
            if ev is None:
                log.info("SofaScore: no event match for %s vs %s",
                         fx["home_team_raw"], fx["away_team_raw"])
                continue
            insight = _event_insight(client, ev["id"])
            if insight:
                insight["sofascore_event_id"] = ev["id"]
                insight["matched_names"] = (
                    f"{ev['homeTeam']['name']} v {ev['awayTeam']['name']}")
                out[fx["event_id"]] = insight
        except Exception as e:  # noqa: BLE001 - keep going per fixture
            log.warning("SofaScore insight failed for match %s: %s",
                        fx.get("match_number"), e)

    save_cache(cache_key, {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "insights": out})
    log.info("SofaScore matched %d/%d fixtures", len(out), len(fixtures))
    return out
