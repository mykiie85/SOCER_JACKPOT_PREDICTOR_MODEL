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
