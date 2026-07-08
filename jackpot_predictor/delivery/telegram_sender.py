"""Telegram delivery via the Bot API (same env vars as EdgeBot)."""
from __future__ import annotations

import logging
import os
from pathlib import Path

import requests

from jackpot_predictor.config.settings import (TELEGRAM_BOT_TOKEN,
                                               TELEGRAM_CHAT_ID)

log = logging.getLogger(__name__)

_API = "https://api.telegram.org/bot{token}/{method}"
_MAX_LEN = 4000  # Telegram hard limit is 4096; leave headroom


def _chat_ids() -> list[str]:
    return [c.strip() for c in TELEGRAM_CHAT_ID.split(",") if c.strip()]


def _chunks(text: str) -> list[str]:
    """Split on blank lines so no message exceeds the Telegram limit."""
    if len(text) <= _MAX_LEN:
        return [text]
    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > _MAX_LEN:
            if current:
                parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)
    return parts


def send_message(text: str) -> bool:
    if not TELEGRAM_BOT_TOKEN or not _chat_ids():
        log.warning("Telegram not configured (TELEGRAM_BOT_TOKEN / "
                    "TELEGRAM_CHAT_ID) — skipping")
        return False
    ok = True
    for chat_id in _chat_ids():
        for chunk in _chunks(text):
            r = requests.post(
                _API.format(token=TELEGRAM_BOT_TOKEN, method="sendMessage"),
                json={"chat_id": chat_id, "text": chunk,
                      "disable_web_page_preview": True},
                timeout=30)
            if not r.ok:
                log.error("Telegram sendMessage failed for %s: %s",
                          chat_id, r.text[:200])
                ok = False
    return ok


def send_document(path: str | Path, caption: str = "") -> bool:
    if not TELEGRAM_BOT_TOKEN or not _chat_ids() or not os.path.exists(path):
        return False
    ok = True
    for chat_id in _chat_ids():
        with open(path, "rb") as f:
            r = requests.post(
                _API.format(token=TELEGRAM_BOT_TOKEN, method="sendDocument"),
                data={"chat_id": chat_id, "caption": caption[:1000]},
                files={"document": f}, timeout=60)
        if not r.ok:
            log.error("Telegram sendDocument failed for %s: %s",
                      chat_id, r.text[:200])
            ok = False
    return ok


def send_admin_alert(text: str) -> None:
    """Short operational alert (scrape failed, unresolved teams, ...)."""
    send_message(f"⚙️ Jackpot bot: {text}"[:_MAX_LEN])
