"""Fetch SportPesa TZ's published jackpot archive.

The jackpot widget's "Archive" tab reads a second public API:

    GET https://jackpot-betslip-api.sportpesa.co.tz/api/jackpots/history
        ?to=<epoch ms>&pageNum=<0-based>&pageSize=<n>      -> list, Content-Range
    GET https://jackpot-betslip-api.sportpesa.co.tz/api/jackpots/history/<id>/details

Each details payload carries the 17 events with declared outcome
(``resultPick``: Home/Draw/Away) and score, the prize pool of every tier
(17/17 ... 13/13), and per tier the winner counts and per-winner payouts of
every category (Jackpot, then Category1..5 = one to five misses).  The
archive holds no odds — those exist only in our own prediction archives.

Everything is saved verbatim under ``data/jackpots/archive/<human_id>.json``
so the analysis scripts never need the network.
"""
from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timezone
from pathlib import Path

from jackpot_predictor.config.settings import JACKPOTS_DIR
from jackpot_predictor.scraper.sportpesa import ScrapeError, _http_get

log = logging.getLogger(__name__)

BETSLIP_API_BASE = os.getenv(
    "JACKPOT_BETSLIP_API_BASE", "https://jackpot-betslip-api.sportpesa.co.tz/api")
ARCHIVE_DIR = JACKPOTS_DIR / "archive"


def list_history(page_size: int = 50) -> list[dict]:
    """All archived jackpots (newest first): jackpotId, jackpotHumanId, finished."""
    to_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
    out: list[dict] = []
    page = 0
    while True:
        rows = _http_get(f"{BETSLIP_API_BASE}/jackpots/history"
                         f"?to={to_ms}&pageNum={page}&pageSize={page_size}")
        if not isinstance(rows, list) or not rows:
            break
        out.extend(rows)
        if len(rows) < page_size:
            break
        page += 1
    return out


def fetch_details(jackpot_id: str) -> dict:
    return _http_get(f"{BETSLIP_API_BASE}/jackpots/history/{jackpot_id}/details")


def archive_path(human_id: int) -> Path:
    return ARCHIVE_DIR / f"{int(human_id):04d}.json"


def sync_archive(pause_s: float = 0.5, refetch: bool = False) -> int:
    """Download every archived jackpot not yet on disk. Returns count fetched."""
    ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
    rows = list_history()
    log.info("archive lists %d jackpots", len(rows))
    fetched = 0
    for row in rows:
        hid = row.get("jackpotHumanId")
        path = archive_path(hid)
        if path.exists() and not refetch:
            continue
        try:
            det = fetch_details(row["jackpotId"])
        except ScrapeError as e:
            # A few archived ids (cancelled jackpots) 404 on the details
            # endpoint; keep the listing row so they are not retried forever.
            log.warning("jackpot #%s has no details: %s", hid, e)
            det = {**row, "_missing": True}
        det["_fetched_at_utc"] = datetime.now(timezone.utc).isoformat()
        path.write_text(json.dumps(det, ensure_ascii=False), encoding="utf-8")
        fetched += 1
        time.sleep(pause_s)
    log.info("fetched %d new archived jackpots", fetched)
    return fetched


def load_archive() -> list[dict]:
    """All archived jackpots on disk, oldest first."""
    out = []
    for p in sorted(ARCHIVE_DIR.glob("*.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError) as e:
            log.warning("skipping %s: %s", p, e)
            continue
        if not d.get("_missing"):
            out.append(d)
    return out


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    n = sync_archive()
    print(f"fetched {n}; on disk {len(list(ARCHIVE_DIR.glob('*.json')))}")
