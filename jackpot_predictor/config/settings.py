"""Central configuration: paths, env vars, and jackpot.yaml loading.

Everything reads from environment variables first (populated by a local .env
via python-dotenv when present) so the same code runs on Windows dev boxes and
the Linux VPS without edits.
"""
from __future__ import annotations

import os
from pathlib import Path

import yaml

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:  # pragma: no cover - dotenv is a convenience, not required
    pass

ROOT = Path(__file__).resolve().parent.parent.parent

# Where EdgeBot lives (trained models, historical data, alias maps).
EDGEBOT_PATH = Path(os.getenv(
    "EDGEBOT_PATH", r"C:\Users\mykii\Downloads\Betting algorithim"))

DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
CACHE_DIR = Path(os.getenv("CACHE_DIR", DATA_DIR / "cache"))
JACKPOTS_DIR = Path(os.getenv("JACKPOTS_DIR", DATA_DIR / "jackpots"))
LOGS_DIR = Path(os.getenv("LOGS_DIR", DATA_DIR / "logs"))
for d in (DATA_DIR, CACHE_DIR, JACKPOTS_DIR, LOGS_DIR):
    d.mkdir(parents=True, exist_ok=True)

# SportPesa TZ jackpot widget API (discovered from jackpot-widget.sportpesa.co.tz).
JACKPOT_API_BASE = os.getenv(
    "JACKPOT_API_BASE", "https://jackpot-offer-api.sportpesa.co.tz/api")
JACKPOT_PAGE_URL = os.getenv(
    "JACKPOT_PAGE_URL", "https://sportpesa.co.tz/en/supa-jackpot")

# Delivery credentials (same names EdgeBot uses, so one .env can serve both).
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
EMAIL_SENDER = os.getenv("EMAIL_SENDER", "")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD", "")
EMAIL_RECEIVER = os.getenv("EMAIL_RECEIVER", "") or EMAIL_SENDER

_cfg_cache: dict | None = None


def jackpot_config() -> dict:
    """Load config/jackpot.yaml once."""
    global _cfg_cache
    if _cfg_cache is None:
        with open(Path(__file__).parent / "jackpot.yaml", encoding="utf-8") as f:
            _cfg_cache = yaml.safe_load(f) or {}
    return _cfg_cache
