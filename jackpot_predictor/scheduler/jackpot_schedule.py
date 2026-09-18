"""Decide which delivery stage, if any, is due right now.

The systemd timer fires main.py twice a day (11:00 and 20:00 EAT); this gate
makes it a no-op except when a configured stage is due:

    preview  days_before: 2, send_hour_eat: 20  -> the slate, two days out
    final    days_before: 0, send_hour_eat: 11  -> same slate, prices
                                                   refreshed on match day

The final exists because the closing price is measurably better than the
price two days out (lineups, injuries, sharp money) and the bot's picks are
the price. The archive shows the earliest first kickoff ever was 10:00 UTC
(13:00 EAT), most are Saturday 14:00-19:00 UTC, so 11:00 EAT always lands
before the slate closes.

The closing date is not scraped from a countdown — it *is* the earliest
kickoff in the feed, which is authoritative.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jackpot_predictor.config.settings import jackpot_config

log = logging.getLogger(__name__)


def _tz() -> ZoneInfo:
    return ZoneInfo(jackpot_config()["schedule"]["timezone"])


def stages() -> dict[str, dict]:
    """Configured stages, or the legacy single-stage schedule as ``preview``."""
    cfg = jackpot_config()["schedule"]
    if cfg.get("stages"):
        return {name: dict(st) for name, st in cfg["stages"].items()}
    return {"preview": {"days_before": int(cfg.get("days_before", 2)),
                        "send_hour_eat": int(cfg.get("send_hour_eat", 20))}}


def first_kickoff_local(jackpot: dict) -> datetime | None:
    iso = jackpot.get("first_kickoff_utc")
    if not iso:
        return None
    return (datetime.fromisoformat(iso.replace("Z", "+00:00"))
            .astimezone(_tz()))


def days_until_close(jackpot: dict, now: datetime | None = None) -> int | None:
    """Whole calendar days (EAT) between today and the first kickoff."""
    kickoff = first_kickoff_local(jackpot)
    if kickoff is None:
        return None
    now = (now or datetime.now(timezone.utc)).astimezone(_tz())
    return (kickoff.date() - now.date()).days


def stage_due(jackpot: dict, now: datetime | None = None,
              sent: set[str] | frozenset[str] = frozenset()) -> str | None:
    """Name of the stage to deliver now, or None.

    A stage is due on its own day once its hour has passed, and — so a
    missed firing (VPS down that evening) still delivers — on any later day
    that is still before the first kickoff. When two stages are due at once
    (e.g. a preview never went out and it is now match day) the later stage
    wins: a preview on match day is pointless if the final is going.
    """
    days = days_until_close(jackpot, now)
    if days is None:
        log.warning("jackpot has no kickoff time — cannot schedule")
        return None
    if jackpot.get("betting_status") not in (None, "Open"):
        log.info("betting status is %r — not sending", jackpot["betting_status"])
        return None
    now_local = (now or datetime.now(timezone.utc)).astimezone(_tz())
    due = []
    for name, st in stages().items():
        if name in sent:
            continue
        target, hour = int(st["days_before"]), int(st["send_hour_eat"])
        if (days == target and now_local.hour >= hour) or 0 <= days < target:
            due.append((target, name))
    if not due:
        log.info("%d day(s) until first kickoff, %02d:00 local — no stage due",
                 days, now_local.hour)
        return None
    target, name = min(due)
    log.info("%d day(s) until first kickoff — stage %r due", days, name)
    return name


def should_send_today(jackpot: dict, now: datetime | None = None) -> bool:
    """Backwards-compatible boolean form of :func:`stage_due`."""
    return stage_due(jackpot, now) is not None
