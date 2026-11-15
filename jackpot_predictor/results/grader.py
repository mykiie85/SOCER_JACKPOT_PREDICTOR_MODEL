"""Grade a jackpot's archived predictions against the real results.

Results source
--------------
SportPesa's jackpot history API (``jackpot-betslip-api .../jackpots/history/
<jackpot_id>/details``, the one ``scraper/archive.py`` syncs) is the only
source that says a result is final: the round carries ``jackpotStatus:
"Finished"`` once SportPesa has settled it (2-3 h after the last kickoff in
practice) and every event its settled ``resultPick``. A round is graded only
then; until it is, nothing is graded and the next run tries again.

The live feed (``/jackpots/active``) is NOT a results source. Its per-event
``score`` fills in while a match is being played and it has no finished flag,
so grading from it marked matches on in-play scores: #240 match 11 was logged
as a draw at 90 minutes and finished 2:4. It is still snapshotted to
``data/jackpots/<type>_<human>_results.json`` for the record.

Matches SportPesa settles without a score (postponed / void) come back as
``score: "PD:<1|X|2>"`` with the drawn ``resultPick``; they stay ``void``,
outside the correct/played count, exactly as the live feed's ``publicDraw``
was treated.
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timezone
from pathlib import Path

from jackpot_predictor.config.settings import JACKPOT_API_BASE, JACKPOTS_DIR
from jackpot_predictor.scraper.sportpesa import _http_get

log = logging.getLogger(__name__)

_OUTCOME_FROM_PUBLIC = {"Home": "H", "Draw": "D", "Away": "A"}


# ─────────────────────────────────────────────────────── prediction archive
def latest_prediction_archive() -> dict | None:
    """Newest ``save_outputs`` JSON archive (``{jackpot, predictions}``)."""
    files = sorted((p for p in JACKPOTS_DIR.glob("*.json")
                    if not p.name.endswith(("_results.json",
                                            "_line_grades.json"))),
                   key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files:
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(d, dict) and d.get("jackpot") and d.get("predictions"):
            d["_archive_path"] = str(f)
            return d
    return None


# ───────────────────────────────────────────────────────────────── results
def _snapshot_path(jackpot: dict) -> Path:
    return JACKPOTS_DIR / (f"{jackpot['jackpot_type']}_{jackpot['human_id']}"
                           "_results.json")


def outcome_of(ev: dict) -> tuple[str | None, tuple[int, int] | None, str]:
    """(outcome H/D/A, (home,away) score, status).

    status is ``played`` (real score), ``void`` (declared, no score) or
    ``pending`` (not settled yet).
    """
    sc = ev.get("score") or None
    if sc and sc.get("home") is not None and sc.get("away") is not None:
        h, a = int(sc["home"]), int(sc["away"])
        return ("H" if h > a else "D" if h == a else "A"), (h, a), "played"
    pub = ev.get("publicDraw")
    if pub in _OUTCOME_FROM_PUBLIC:
        return _OUTCOME_FROM_PUBLIC[pub], None, "void"
    return None, None, "pending"


def results_by_event(payload: dict) -> dict:
    """event_id -> {outcome, score, status} from a raw jackpot payload."""
    out = {}
    for ev in payload.get("events", []):
        o, score, status = outcome_of(ev)
        out[ev.get("id")] = {"outcome": o, "score": score, "status": status}
    return out


def snapshot_live(archive: dict) -> None:
    """Save the live feed for the record while it still serves this jackpot.
    Never graded from: its scores include matches still in play."""
    jackpot = archive["jackpot"]
    try:
        payload = _http_get(f"{JACKPOT_API_BASE}/jackpots/active")
    except Exception as e:  # noqa: BLE001 - a snapshot is best-effort
        log.warning("live feed fetch failed: %s", e)
        return
    if payload is not None and payload.get("id") == jackpot.get("jackpot_id"):
        try:
            _snapshot_path(jackpot).write_text(json.dumps(payload, default=str),
                                               encoding="utf-8")
        except OSError as e:
            log.warning("could not save live snapshot: %s", e)


def _official_path(jackpot: dict) -> Path:
    return JACKPOTS_DIR / (f"{jackpot['jackpot_type']}_{jackpot['human_id']}"
                           "_official_results.json")


def official_results(details: dict, predictions: list[dict]) -> dict:
    """event_id -> {outcome, score, status} from a history-API details payload.

    Only a round SportPesa marks Finished yields results; anything else is {}.
    History events are numbered like the slate, so they join to the archived
    predictions on match number."""
    if (details or {}).get("jackpotStatus") != "Finished":
        return {}
    by_number = {p.get("match_number"): p for p in predictions}
    out = {}
    for ev in details.get("events", []):
        p = by_number.get(ev.get("eventNumber"))
        if p is None:
            continue
        if ev.get("kickoffTime") and p.get("kickoff_utc") and \
                ev["kickoffTime"][:16] != str(p["kickoff_utc"])[:16]:
            log.warning("match %s: history kickoff %s vs slate %s",
                        ev.get("eventNumber"), ev["kickoffTime"], p["kickoff_utc"])
        outcome = _OUTCOME_FROM_PUBLIC.get(ev.get("resultPick"))
        score = str(ev.get("score") or "")
        if outcome is None:
            r = {"outcome": None, "score": None, "status": "pending"}
        elif score.startswith("PD:"):
            r = {"outcome": outcome, "score": None, "status": "void"}
        else:
            try:
                h, a = (int(x) for x in score.split(":"))
            except ValueError:
                h = a = None
            if h is not None and ("H" if h > a else "D" if h == a else "A") \
                    != outcome:
                log.warning("match %s: score %s disagrees with resultPick %s — "
                            "using resultPick", ev.get("eventNumber"), score,
                            ev.get("resultPick"))
            r = {"outcome": outcome,
                 "score": (h, a) if h is not None else None, "status": "played"}
        out[p.get("event_id")] = r
    return out


def load_results(archive: dict, use_live: bool = True) -> tuple[dict, str]:
    """Return (results_by_event, source); source is "official" or "none".

    Results exist only once SportPesa's history marks the round Finished. The
    details are saved beside the archive so a later re-grade needs no network.
    ``use_live`` also refreshes the live-feed snapshot (record only)."""
    jackpot = archive["jackpot"]
    if use_live:
        snapshot_live(archive)
    from jackpot_predictor.scraper.archive import fetch_details
    path = _official_path(jackpot)
    details = None
    try:
        details = fetch_details(jackpot["jackpot_id"])
    except Exception as e:  # noqa: BLE001 - retry on the next run
        log.warning("history fetch failed: %s", e)
    if details is None and path.exists():
        try:
            details = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("saved official results unreadable: %s", e)
    if details and details.get("jackpotId") not in (None, jackpot.get("jackpot_id")):
        log.warning("history details are for jackpot %s, not %s — ignoring",
                    details.get("jackpotId"), jackpot.get("jackpot_id"))
        details = None
    if not details or details.get("jackpotStatus") != "Finished":
        log.info("jackpot #%s not marked Finished yet (status %s) — not grading",
                 jackpot.get("human_id"), (details or {}).get("jackpotStatus"))
        return {}, "none"
    try:
        path.write_text(json.dumps(details, default=str), encoding="utf-8")
    except OSError as e:
        log.warning("could not save official results: %s", e)
    results = official_results(details, archive["predictions"])
    return results, ("official" if results else "none")


# ───────────────────────────────────────────────────────────────── grading
def grade(archive: dict, results: dict) -> dict:
    """Grade every archived prediction; void matches sit outside the headline
    count (standard push convention), pending ones simply aren't in yet."""
    rows, correct, played, void, pending = [], 0, 0, 0, 0
    for p in archive["predictions"]:
        r = results.get(p.get("event_id")) or {"outcome": None, "score": None,
                                                "status": "pending"}
        pick, status = p.get("primary_pick"), r["status"]
        is_correct = None
        if status == "played" and pick:
            is_correct = (pick == r["outcome"])
            played += 1
            correct += int(is_correct)
        elif status == "void":
            void += 1
        else:
            pending += 1
        rows.append({**p, "actual_outcome": r["outcome"],
                     "actual_score": r["score"], "result_status": status,
                     "is_correct": is_correct})
    return {"rows": rows, "correct": correct, "played": played, "void": void,
            "pending": pending, "total": len(archive["predictions"])}


# ──────────────────────────────────────────── favourite vs lean line grades
def grade_lines(archive: dict, results: dict) -> dict | None:
    """Grade the archived favourite and lean lines exactly as ``grade`` grades
    the published picks. None for archives saved without the lines."""
    from jackpot_predictor.predictor.lean_line import LINE_FIELDS
    if not all(isinstance(archive.get(f), list) for f in LINE_FIELDS):
        return None
    preds = sorted(archive["predictions"], key=lambda p: p["match_number"])
    out = {}
    for field in LINE_FIELDS:
        g = grade({"predictions": [{**p, "primary_pick": pick}
                                   for p, pick in zip(preds, archive[field])]},
                  results)
        out[field] = {"picks": archive[field],
                      "hits": [r["is_correct"] for r in g["rows"]],
                      **{k: g[k] for k in ("correct", "played", "void",
                                           "pending", "total")}}
    return out


def line_grades_path(jackpot: dict) -> Path:
    return JACKPOTS_DIR / (f"{jackpot['jackpot_type']}_{jackpot['human_id']}"
                           "_line_grades.json")


def save_line_grades(archive: dict, line_grades: dict) -> Path:
    """Write (or refresh, as more matches settle) the round's line grades."""
    jackpot = archive["jackpot"]
    path = line_grades_path(jackpot)
    path.write_text(json.dumps({
        "jackpot_id": jackpot.get("jackpot_id"),
        "human_id": jackpot.get("human_id"),
        "archive": Path(archive.get("_archive_path", "")).name,
        "graded_at": datetime.now(timezone.utc).isoformat(),
        "lean_line_settings": archive.get("lean_line_settings"),
        **line_grades,
    }, indent=2, default=str), encoding="utf-8")
    return path


# ────────────────────────────────────────────────── production-vs-market log
# The deployed tier-1 stack (cached boosters + isotonic calibrator) cannot be
# scored on past matches: it was fit on them, so a backtest leaks the answer and
# manufactures a pass. train_tier1_gate.py works around that by re-fitting an
# honest walk-forward fusion, but that measures the fusion, not production.
#
# The only leak-free measurement of the deployed stack is forward: log what it
# said *before* kickoff, then score it once the result lands. Every graded
# fixture appends one row here with both estimates — the model's and the
# de-vigged market's — so the two are scored on identical fixtures with
# identical outcomes. Accumulate to n >= 150 before drawing conclusions; the
# jackpot slates only carry ~17 fixtures each, and 8 slates were nowhere near
# enough to separate a 0.005 Brier difference from noise.
#
# This is also the evidence that can *reopen* a gate. A tier-1 league routed to
# market odds still logs what the model would have said, so a model that starts
# genuinely beating the price can prove it without ever being trusted first.

PRODUCTION_LOG = JACKPOTS_DIR / "jackpot_production_log.jsonl"

_EPS = 1e-12
_OUTCOME_INDEX = {"H": "home", "D": "draw", "A": "away"}


def _probs_from(row: dict, prefix: str) -> dict | None:
    """{H,D,A} probabilities from ``<prefix>_home/draw/away``, if all present."""
    try:
        p = {k: float(row[f"{prefix}_{o}"]) for k, o in _OUTCOME_INDEX.items()}
    except (KeyError, TypeError, ValueError):
        return None
    return p if abs(sum(p.values()) - 1.0) < 0.05 else None


def _market_probs(row: dict) -> dict | None:
    """Market probabilities: the recorded ones, else de-vigged from the odds.

    Archives written before the engine started recording ``market_prob_*`` are
    reconstructed from the prices SportPesa published, using the same de-vig
    the live path uses — so old slates score comparably to new ones.
    """
    p = _probs_from(row, "market_prob")
    if p is not None:
        return p
    from jackpot_predictor.predictor.odds_fallback import implied_probabilities
    odds = implied_probabilities(row.get("odds_home"), row.get("odds_draw"),
                                 row.get("odds_away"))
    if odds is None:
        return None
    return {k: odds[o] for k, o in _OUTCOME_INDEX.items()}


def _model_probs(row: dict) -> dict | None:
    """Model probabilities: the recorded ones, else the published ones when
    the pick came from the model (pre-``model_prob_*`` archives)."""
    p = _probs_from(row, "model_prob")
    if p is not None:
        return p
    if (row.get("source") or "").startswith(("model", "blend")):
        return _probs_from(row, "prob")
    return None


def _logloss(probs: dict, actual: str) -> float:
    return -math.log(max(probs.get(actual, 0.0), _EPS))


def _brier(probs: dict, actual: str) -> float:
    """Multiclass (Brier) score: sum of squared errors over all three outcomes."""
    return sum((probs.get(k, 0.0) - (1.0 if k == actual else 0.0)) ** 2
               for k in _OUTCOME_INDEX)


def _argmax(probs: dict) -> str:
    return max(probs, key=probs.get)


def _existing_keys() -> set:
    """(jackpot_id, fixture_id) already logged — the grader re-runs per slate as
    more matches settle, and a duplicated row would silently double-weight a
    fixture in every metric computed from this file."""
    keys = set()
    if not PRODUCTION_LOG.exists():
        return keys
    for line in PRODUCTION_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except ValueError:
            continue
        keys.add((r.get("jackpot_id"), r.get("fixture_id")))
    return keys


def archive_lines(archive: dict) -> dict | None:
    """{match_number: (favourite, lean)} plus the strength used, for an archive.

    Archives saved with the lean line carry both lines; older ones get them
    rebuilt from their own predictions with the current config, so a
    re-grade of past rounds still logs both. None when the feature is off and
    the archive has no lines."""
    from jackpot_predictor.predictor import lean_line
    if all(isinstance(archive.get(f), list) for f in lean_line.LINE_FIELDS):
        lines = archive
        settings = archive.get("lean_line_settings") or {}
    elif lean_line.enabled():
        lines = lean_line.build_lines(archive["predictions"])
        settings = lines["lean_line_settings"]
    else:
        return None
    return {"picks": {i: (f, l) for i, (f, l) in enumerate(
                zip(lines["favourite_line"], lines["lean_line"]), 1)},
            "strength": settings.get("strength")}


def log_production_results(archive: dict, graded: dict) -> dict:
    """Append one model-vs-market row per newly settled fixture.

    Returns ``{"written": n, "skipped": n, "total": n}``. Only ``played``
    fixtures are logged — void matches have no outcome to score against, and
    pending ones simply have not happened yet.
    """
    jackpot = archive["jackpot"]
    jackpot_id = str(jackpot.get("human_id") or jackpot.get("jackpot_id"))
    seen = _existing_keys()
    lines = archive_lines(archive)
    graded_at = datetime.now(timezone.utc).isoformat()
    written = skipped = 0
    out_lines = []
    for row in graded["rows"]:
        actual = row.get("actual_outcome")
        if row.get("result_status") != "played" or actual not in _OUTCOME_INDEX:
            continue
        fixture_id = row.get("event_id")
        if (jackpot_id, fixture_id) in seen:
            skipped += 1
            continue
        model_p = _model_probs(row)
        market_p = _market_probs(row)
        if model_p is None and market_p is None:
            skipped += 1
            continue
        seen.add((jackpot_id, fixture_id))
        entry = {
            "jackpot_id": jackpot_id,
            "fixture_id": fixture_id,
            "league": row.get("league_code") or row.get("tournament"),
            "tier": row.get("tier"),
            "kickoff": row.get("kickoff_utc"),
            "home_team": row.get("home_team_raw"),
            "away_team": row.get("away_team_raw"),
            "published_pick": row.get("primary_pick"),
            "actual_result": actual,
            "source": row.get("source"),
            "model_gated": bool(row.get("model_gated")),
            "graded_at": graded_at,
        }
        for label, probs in (("model", model_p), ("market", market_p)):
            if probs is None:
                entry.update({f"{label}_pick": None, f"{label}_prob": None,
                              f"{label}_correct": None,
                              f"{label}_logloss": None, f"{label}_brier": None})
                continue
            pick = _argmax(probs)
            entry.update({
                f"{label}_pick": pick,
                f"{label}_prob": round(probs[pick], 6),
                f"{label}_correct": pick == actual,
                f"{label}_logloss": round(_logloss(probs, actual), 6),
                f"{label}_brier": round(_brier(probs, actual), 6),
            })
        if lines and row.get("match_number") in lines["picks"]:
            fav, lean = lines["picks"][row["match_number"]]
            entry.update({"favourite_line_pick": fav,
                          "favourite_line_correct": fav == actual,
                          "lean_line_pick": lean,
                          "lean_line_correct": lean == actual,
                          "lean_line_strength": lines["strength"]})
        out_lines.append(json.dumps(entry, default=str))
        written += 1

    if out_lines:
        PRODUCTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PRODUCTION_LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(out_lines) + "\n")
        log.info("production log: +%d rows (%s)", written, PRODUCTION_LOG)
    return {"written": written, "skipped": skipped, "total": len(seen)}


def production_summary() -> dict:
    """Model vs market over everything logged so far."""
    if not PRODUCTION_LOG.exists():
        return {"n": 0}
    rows = []
    for line in PRODUCTION_LOG.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    both = [r for r in rows
            if r.get("model_logloss") is not None
            and r.get("market_logloss") is not None]
    if not both:
        return {"n": len(rows), "n_comparable": 0}
    n = len(both)

    def mean(key):
        return sum(r[key] for r in both) / n

    return {
        "n": len(rows),
        "n_comparable": n,
        "model_hit": sum(bool(r["model_correct"]) for r in both) / n,
        "market_hit": sum(bool(r["market_correct"]) for r in both) / n,
        "model_logloss": round(mean("model_logloss"), 4),
        "market_logloss": round(mean("market_logloss"), 4),
        "model_brier": round(mean("model_brier"), 4),
        "market_brier": round(mean("market_brier"), 4),
        # Positive = the model is worse than the price it replaced.
        "d_logloss": round(mean("model_logloss") - mean("market_logloss"), 4),
        "d_brier": round(mean("model_brier") - mean("market_brier"), 4),
    }


def format_production_summary(s: dict) -> str:
    if not s.get("n_comparable"):
        return (f"Production log: {s.get('n', 0)} rows, none with both a model "
                "and a market probability yet.")
    verdict = ("model ahead" if s["d_logloss"] < 0 else "market ahead")
    return (f"Production log ({s['n_comparable']} graded fixtures): "
            f"hit {s['model_hit']:.1%} model vs {s['market_hit']:.1%} market | "
            f"logloss {s['model_logloss']:.4f} vs {s['market_logloss']:.4f} "
            f"(Δ{s['d_logloss']:+.4f}) | brier {s['model_brier']:.4f} vs "
            f"{s['market_brier']:.4f} (Δ{s['d_brier']:+.4f}) — {verdict}"
            + ("" if s["n_comparable"] >= 150 else
               f"  [n < 150: not yet enough to conclude]"))
