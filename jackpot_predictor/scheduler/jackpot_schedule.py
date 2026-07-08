"""Decide whether predictions should go out today.

The systemd timers fire the bot every day at 20:00 EAT; this gate makes it a
no-op except exactly ``days_before`` days before the jackpot's first kickoff.
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


def should_send_today(jackpot: dict, now: datetime | None = None) -> bool:
    """True exactly ``days_before`` days ahead of the first kickoff.

    Also true anywhere inside (0, days_before) when betting is still open —
    if a run was missed (VPS down on the scheduled evening), the next daily
    firing still delivers rather than silently skipping the jackpot.
    """
    days = days_until_close(jackpot, now)
    if days is None:
        log.warning("jackpot has no kickoff time — cannot schedule")
        return False
    target = int(jackpot_config()["schedule"]["days_before"])
    if jackpot.get("betting_status") not in (None, "Open"):
        log.info("betting status is %r — not sending", jackpot["betting_status"])
        return False
    if 0 <= days <= target:
        log.info("%d day(s) until first kickoff (target %d) — sending",
                 days, target)
        return True
    log.info("%d day(s) until first kickoff (target %d) — not our day",
             days, target)
    return False
