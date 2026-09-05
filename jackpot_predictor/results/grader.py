"""Grade a jackpot's archived predictions against the real results.

Results source
--------------
SportPesa's own jackpot feed (the one the scraper already uses for fixtures)
carries a per-event ``score`` that is filled in once a match finishes, plus a
``publicDraw`` field that holds a declared 1X2 outcome for matches SportPesa
settled without a score (postponed / void). We reuse that feed — the archived
predictions and the live events join on the stable ``event_id``
(``sr:match:...``), so no fuzzy name matching is needed.

Timing caveat
-------------
``/jackpots/active`` only serves the *currently* active jackpot. Results are
therefore fetchable in the window between the last kickoff and the next jackpot
going live. To survive that window we snapshot the raw results payload to
``data/jackpots/<type>_<human>_results.json`` the first time we see them, and
fall back to that snapshot if the active jackpot has already rolled over.
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
                    if not p.name.endswith("_results.json")),
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


def load_results(archive: dict, use_live: bool = True) -> tuple[dict, str]:
    """Return (results_by_event, source). source is live/snapshot/none.

    Prefers a fresh fetch of the active jackpot when it is still the one we
    predicted (snapshotting it for durability); otherwise falls back to a
    previously saved snapshot.
    """
    jackpot = archive["jackpot"]
    snap = _snapshot_path(jackpot)
    if use_live:
        try:
            payload = _http_get(f"{JACKPOT_API_BASE}/jackpots/active")
        except Exception as e:  # noqa: BLE001 - never let a fetch error crash grading
            log.warning("live results fetch failed: %s", e)
            payload = None
        if payload is not None:
            if payload.get("id") == jackpot.get("jackpot_id"):
                try:
                    snap.write_text(json.dumps(payload, default=str),
                                    encoding="utf-8")
                except OSError as e:
                    log.warning("could not save results snapshot: %s", e)
                return results_by_event(payload), "live"
            log.warning("active jackpot is #%s, but we predicted #%s — the feed "
                        "has rolled over; using snapshot if present",
                        payload.get("humanId"), jackpot.get("human_id"))
    if snap.exists():
        try:
            payload = json.loads(snap.read_text(encoding="utf-8"))
            return results_by_event(payload), "snapshot"
        except (OSError, ValueError) as e:
            log.warning("results snapshot unreadable: %s", e)
    return {}, "none"


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


def log_production_results(archive: dict, graded: dict) -> dict:
    """Append one model-vs-market row per newly settled fixture.

    Returns ``{"written": n, "skipped": n, "total": n}``. Only ``played``
    fixtures are logged — void matches have no outcome to score against, and
    pending ones simply have not happened yet.
    """
    jackpot = archive["jackpot"]
    jackpot_id = str(jackpot.get("human_id") or jackpot.get("jackpot_id"))
    seen = _existing_keys()
    graded_at = datetime.now(timezone.utc).isoformat()
    written = skipped = 0
    lines = []
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
        lines.append(json.dumps(entry, default=str))
        written += 1

    if lines:
        PRODUCTION_LOG.parent.mkdir(parents=True, exist_ok=True)
        with PRODUCTION_LOG.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines) + "\n")
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
