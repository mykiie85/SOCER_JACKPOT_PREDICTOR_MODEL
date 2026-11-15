"""Refresh a single tarball of data/jackpots/ for copying off the VPS (scp).

Called by results_main.py after each grade, so it rides the existing
jackpot-results timer. The archive is built beside the target and renamed
over it, so a copy taken mid-refresh gets the old file, never half a new one.
"""
from __future__ import annotations

import logging
import os
import tarfile
from pathlib import Path

from jackpot_predictor.config.settings import JACKPOTS_DIR, jackpot_config

log = logging.getLogger(__name__)


def _newest_mtime(root: Path) -> float:
    return max((p.stat().st_mtime for p in root.rglob("*") if p.is_file()),
               default=0.0)


def refresh_export() -> Path | None:
    """Rebuild the export when data/jackpots/ has changed since the last one.

    Returns the path when written, None when disabled or already current."""
    cfg = jackpot_config().get("export") or {}
    if not cfg.get("enabled"):
        return None
    target = Path(os.path.expanduser(cfg["path"]))
    if target.exists() and target.stat().st_mtime >= _newest_mtime(JACKPOTS_DIR):
        return None
    tmp = target.with_name(target.name + ".tmp")
    with tarfile.open(tmp, "w:gz") as tar:
        tar.add(JACKPOTS_DIR, arcname=JACKPOTS_DIR.name)
    os.replace(tmp, target)
    log.info("export refreshed: %s (%d bytes)", target, target.stat().st_size)
    return target
