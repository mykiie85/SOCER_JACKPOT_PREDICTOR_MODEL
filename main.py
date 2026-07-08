#!/usr/bin/env python3
"""SportPesa Jackpot Predictor — entry point.

    python main.py                 # scheduled mode: only sends on the right day
    python main.py --dry-run       # predict + print, send nothing
    python main.py --force         # ignore the schedule gate and send now
    python main.py --no-cache      # bypass the fixture cache

Runs EdgeBot's already-trained ensemble over the active SportPesa TZ jackpot
and delivers Forebet-style 1X2 picks via Telegram + email.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys

from jackpot_predictor.config.settings import CACHE_DIR, LOGS_DIR

# Windows consoles default to cp1252, which cannot print the tier emoji.
if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(LOGS_DIR / "jackpot.log", encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("jackpot")

_SENT_REGISTRY = CACHE_DIR / "sent.json"


def _already_sent(jackpot_id: str) -> bool:
    try:
        return jackpot_id in json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        return False


def _mark_sent(jackpot_id: str) -> None:
    try:
        sent = json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        sent = []
    sent = (sent + [jackpot_id])[-50:]
    _SENT_REGISTRY.write_text(json.dumps(sent))


def main(dry_run: bool = False, force: bool = False,
         use_cache: bool = True) -> int:
    log.info("=== Jackpot Predictor starting ===")

    # 1. Fetch the active jackpot (API-first; cached).
    from jackpot_predictor.scraper.sportpesa import ScrapeError, fetch_active_jackpot
    try:
        jackpot = fetch_active_jackpot(use_cache=use_cache)
    except ScrapeError as e:
        log.error("scrape failed: %s", e)
        if not dry_run:
            from jackpot_predictor.delivery.telegram_sender import send_admin_alert
            send_admin_alert(f"scrape FAILED — {e}")
        return 1

    # 2. Schedule gate (skipped for --dry-run/--force).
    from jackpot_predictor.scheduler.jackpot_schedule import should_send_today
    if not dry_run and not force:
        if _already_sent(jackpot["jackpot_id"]):
            log.info("jackpot %s already delivered — nothing to do",
                     jackpot["human_id"])
            return 0
        if not should_send_today(jackpot):
            return 0

    # 3. Resolve leagues + team names against EdgeBot's history.
    from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge
    from jackpot_predictor.resolver.name_resolver import resolve_fixtures
    bridge = EdgeBotBridge()
    resolved = resolve_fixtures(jackpot["fixtures"], bridge)

    # 4. Predict (EdgeBot model where covered, market-odds fallback elsewhere).
    from jackpot_predictor.predictor.engine import predict_jackpot
    predictions = predict_jackpot(resolved, bridge)

    # 5. Format + archive.
    from jackpot_predictor.predictor.formatter import save_outputs
    out = save_outputs(jackpot, predictions)

    # 6. Deliver.
    if dry_run:
        print(out["telegram_text"])
        log.info("DRY RUN — nothing sent; outputs archived at %s.*", out["base"])
        return 0

    from jackpot_predictor.config.settings import jackpot_config
    from jackpot_predictor.delivery.email_sender import send_email
    from jackpot_predictor.delivery.telegram_sender import (send_admin_alert,
                                                            send_document,
                                                            send_message)
    dcfg = jackpot_config()["delivery"]
    sent_any = False
    if dcfg["telegram"]["enabled"]:
        sent_any |= send_message(out["telegram_text"])
        send_document(out["csv_path"], caption="Raw probabilities (CSV)")
    if dcfg["email"]["enabled"]:
        from jackpot_predictor.predictor.formatter import JACKPOT_TITLES
        title = JACKPOT_TITLES.get(jackpot["jackpot_type"],
                                   jackpot["jackpot_type"])
        sent_any |= send_email(
            subject=f"SportPesa {title} #{jackpot['human_id']} — Predictions",
            html_body=out["html_body"], csv_attachment=out["csv_path"])

    if sent_any:
        _mark_sent(jackpot["jackpot_id"])
        log.info("delivered jackpot %s", jackpot["human_id"])

    # Operational digest for anything that needs a mappings.json update.
    notes = [n for p in predictions for n in p.get("resolve_notes", [])]
    if notes:
        send_admin_alert("resolver notes:\n" + "\n".join(sorted(set(notes))[:20]))

    log.info("=== Jackpot Predictor complete ===")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="predict and print; send nothing")
    parser.add_argument("--force", action="store_true",
                        help="skip the schedule gate and send now")
    parser.add_argument("--no-cache", action="store_true",
                        help="fetch a fresh fixture feed")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run, force=args.force,
                  use_cache=not args.no_cache))
