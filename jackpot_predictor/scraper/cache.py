"""Tiny JSON file cache so we do not hammer the SportPesa feed.

Normalized data lives in data/cache/<key>.json; the raw API payload is kept
alongside as <key>_raw.json for auditing and for rebuilding parsers offline.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone

from jackpot_predictor.config.settings import CACHE_DIR

log = logging.getLogger(__name__)


def _path(key: str, raw: bool = False):
    suffix = "_raw" if raw else ""
    return CACHE_DIR / f"{key}{suffix}.json"


def load_cached(key: str, ttl_hours: float) -> dict | None:
    p = _path(key)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        fetched = datetime.fromisoformat(data["fetched_at_utc"])
        if datetime.now(timezone.utc) - fetched < timedelta(hours=ttl_hours):
            return data
    except (ValueError, KeyError, OSError) as e:
        log.warning("cache read failed for %s: %s", key, e)
    return None


def save_cache(key: str, data: dict, raw_payload: dict | None = None) -> None:
    try:
        _path(key).write_text(json.dumps(data, indent=2), encoding="utf-8")
        if raw_payload is not None:
            _path(key, raw=True).write_text(
                json.dumps(raw_payload, indent=2), encoding="utf-8")
    except OSError as e:
        log.warning("cache write failed for %s: %s", key, e)
