#!/usr/bin/env python3
"""SportPesa Jackpot Predictor — entry point.

    python main.py                 # scheduled mode: sends only when a stage is due
    python main.py --dry-run       # predict + print, send nothing
    python main.py --force         # ignore the schedule gate and send now
    python main.py --no-cache      # bypass the fixture cache
    python main.py --stage final   # which stage to label/send (--force/--dry-run)

Two stages per jackpot (config schedule.stages): a *preview* two days out and
a *final* on match day with SportPesa's and the sharp books' prices refreshed.

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


def _sent_stages(jackpot_id: str) -> set[str]:
    """Stages already delivered for this jackpot. Entries are "<id>:<stage>";
    a bare "<id>" is the pre-stage registry format and counts as the preview."""
    try:
        entries = json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        return set()
    out = set()
    for e in entries:
        if e == jackpot_id:
            out.add("preview")
        elif isinstance(e, str) and e.startswith(jackpot_id + ":"):
            out.add(e.split(":", 1)[1])
    return out


def _mark_sent(jackpot_id: str, stage: str) -> None:
    try:
        sent = json.loads(_SENT_REGISTRY.read_text())
    except (OSError, ValueError):
        sent = []
    sent = (sent + [f"{jackpot_id}:{stage}"])[-100:]
    _SENT_REGISTRY.write_text(json.dumps(sent))


def main(dry_run: bool = False, force: bool = False,
         use_cache: bool = True, stage: str | None = None) -> int:
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

    # 2. Schedule gate (skipped for --dry-run/--force, which default to a
    #    "final"-style run: fresh prices, no wait).
    from jackpot_predictor.scheduler.jackpot_schedule import stage_due
    if not dry_run and not force:
        stage = stage_due(jackpot, sent=_sent_stages(jackpot["jackpot_id"]))
        if stage is None:
            return 0
    stage = stage or "final"
    if stage == "final" and use_cache:
        # Match-day prices, not the cached feed from the preview evening.
        try:
            jackpot = fetch_active_jackpot(use_cache=False)
        except ScrapeError as e:
            log.warning("fresh fetch failed (%s) — using cached feed", e)
    log.info("stage: %s", stage)

    # 3. Resolve leagues + team names against EdgeBot's history.
    from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge
    from jackpot_predictor.resolver.name_resolver import resolve_fixtures
    bridge = EdgeBotBridge()
    resolved = resolve_fixtures(jackpot["fixtures"], bridge)

    # 4. Predict (EdgeBot model where covered, market-odds fallback elsewhere).
    from jackpot_predictor.predictor.engine import predict_jackpot
    predictions = predict_jackpot(resolved, bridge,
                                  jackpot_id=jackpot["jackpot_id"])

    # 5. Format + archive.
    from jackpot_predictor.predictor.formatter import save_outputs
    out = save_outputs(jackpot, predictions, stage=stage)

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
            subject=(f"SportPesa {title} #{jackpot['human_id']} — "
                     f"{stage.upper()} predictions"),
            html_body=out["html_body"], csv_attachment=out["csv_path"])

    if sent_any:
        _mark_sent(jackpot["jackpot_id"], stage)
        log.info("delivered jackpot %s (%s)", jackpot["human_id"], stage)

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
    parser.add_argument("--stage", choices=["preview", "final"],
                        help="label the send as this stage (default: from schedule)")
    args = parser.parse_args()
    sys.exit(main(dry_run=args.dry_run, force=args.force,
                  use_cache=not args.no_cache, stage=args.stage))
