#!/usr/bin/env python3
"""SportPesa Jackpot Predictor — end-of-jackpot results grader.

    python results_main.py             # scheduled: grade + send once per jackpot
    python results_main.py --dry-run   # grade + print, send nothing
    python results_main.py --force     # re-send even if already reported

Grades the most recent archived prediction set against SportPesa's own settled
1X2 outcomes and delivers a results recap via Telegram + email. A run with no
settled matches yet (or nothing new to report) sends nothing.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from jackpot_predictor.config.settings import CACHE_DIR, LOGS_DIR

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.FileHandler(LOGS_DIR / "jackpot.log", encoding="utf-8"),
              logging.StreamHandler()])
log = logging.getLogger("jackpot.results")

_SENT_REGISTRY = CACHE_DIR / "results_sent.json"


def _already_sent(key: str) -> bool:
    try:
        return key in json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        return False


def _mark_sent(key: str) -> None:
    try:
        sent = json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        sent = []
    _SENT_REGISTRY.write_text(json.dumps((sent + [key])[-50:]))


def main(dry_run: bool = False, force: bool = False) -> int:
    log.info("=== Jackpot Results grader starting ===")

    from jackpot_predictor.results.grader import (grade, latest_prediction_archive,
                                                  load_results)
    archive = latest_prediction_archive()
    if archive is None:
        log.info("no prediction archive found — nothing to grade")
        return 0
    jackpot = archive["jackpot"]

    results, source = load_results(archive, use_live=True)
    if source == "none" or not results:
        log.info("no results available yet for jackpot #%s — nothing to report",
                 jackpot["human_id"])
        return 0

    graded = grade(archive, results)
    if graded["played"] == 0:
        log.info("jackpot #%s has no settled matches yet — nothing to report",
                 jackpot["human_id"])
        return 0

    # Record model-vs-market on every settled fixture before anything else can
    # return early. This is the only leak-free measurement of the deployed
    # stack, so it must not depend on whether a recap happens to be sent.
    from jackpot_predictor.results.grader import (format_production_summary,
                                                  log_production_results,
                                                  production_summary)
    try:
        stats = log_production_results(archive, graded)
        log.info("production log: %d new, %d already recorded, %d total",
                 stats["written"], stats["skipped"], stats["total"])
        log.info("%s", format_production_summary(production_summary()))
    except Exception as e:  # noqa: BLE001 - measurement must never block a recap
        log.warning("production log failed: %s", e)

    from jackpot_predictor.results.formatter import format_html, format_telegram
    telegram_text = format_telegram(graded, jackpot, source)
    html_body = format_html(graded, jackpot, source)

    # Report a jackpot once it is fully settled; re-report only when new matches
    # have settled since last time (key includes the played count).
    key = f"results:{jackpot['jackpot_id']}:{graded['played']}"
    if not dry_run and not force and _already_sent(key):
        log.info("results for jackpot #%s (%d played) already sent",
                 jackpot["human_id"], graded["played"])
        return 0

    if dry_run:
        print(telegram_text)
        log.info("DRY RUN — %d/%d correct (%d void, %d pending); nothing sent",
                 graded["correct"], graded["played"], graded["void"],
                 graded["pending"])
        return 0

    from jackpot_predictor.config.settings import jackpot_config
    from jackpot_predictor.delivery.email_sender import send_email
    from jackpot_predictor.delivery.telegram_sender import send_message
    dcfg = jackpot_config()["delivery"]
    sent_any = False
    if dcfg["telegram"]["enabled"]:
        sent_any |= send_message(telegram_text)
    if dcfg["email"]["enabled"]:
        from jackpot_predictor.predictor.formatter import JACKPOT_TITLES
        title = JACKPOT_TITLES.get(jackpot["jackpot_type"], jackpot["jackpot_type"])
        sent_any |= send_email(
            subject=f"SportPesa {title} #{jackpot['human_id']} — Results "
                    f"({graded['correct']}/{graded['played']})",
            html_body=html_body, csv_attachment=None)

    if sent_any:
        _mark_sent(key)
        log.info("delivered results for jackpot #%s: %d/%d correct",
                 jackpot["human_id"], graded["correct"], graded["played"])
    log.info("=== Jackpot Results grader complete ===")
    return 0 if sent_any else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="grade and print; send nothing")
    parser.add_argument("--force", action="store_true",
                        help="re-send even if already reported")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run, force=args.force))
