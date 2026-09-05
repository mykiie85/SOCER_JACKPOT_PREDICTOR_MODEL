"""EdgeBot's walk-forward quality gates, read from its data_cache.

A gate answers one question per league: *did the model beat the closing line
by a significant margin in walk-forward evaluation?* EdgeBot writes two files:

- ``data_cache/tier2_gates.json`` — consumed by ``Tier2Stack`` itself, which
  hands the verdict back through the ``tier2_gated`` prediction column. Nothing
  here needs to read it.
- ``data_cache/tier1_gates.json`` — written by ``train_tier1_gate.py`` and,
  until now, read by nobody. Tier-1 is where ~80% of jackpot fixtures land, so
  that gap meant the vast majority of picks came from models that had never
  been tested against the price they were replacing. The 2026-09-05 run tested
  them: **0 of 22 tier-1 leagues beat the book** over ~25,000 matches, most of
  them failing significantly the wrong way.

This module closes the gap. ``tier1_gate_passed`` is the tier-1 analogue of
``Tier2Stack.league_gate_passed``, so the engine can apply one rule to both
tiers: a model that has been measured as worse than the market never displaces
the market.

**Missing entries fail closed.** A league with no gate record has not been
shown to beat the book, and the measured base rate for tier-1 is zero out of
twenty-two — so "unknown" is treated as "gated out" rather than "trusted".
Set ``predictor.tier1_gate.missing: pass`` to invert that, and
``predictor.tier1_gate.enabled: false`` to switch the gate off entirely.
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

from jackpot_predictor.config.settings import EDGEBOT_PATH, jackpot_config

log = logging.getLogger(__name__)

TIER1_GATES_FILENAME = "tier1_gates.json"


def _gate_config() -> dict:
    pcfg = jackpot_config().get("predictor", {}) or {}
    return pcfg.get("tier1_gate", {}) or {}


def gates_path() -> Path:
    return Path(EDGEBOT_PATH) / "data_cache" / TIER1_GATES_FILENAME


@lru_cache(maxsize=1)
def load_tier1_gates() -> dict:
    """``{league_code: {pass: bool, ...}}`` from EdgeBot, or ``{}``."""
    path = gates_path()
    if not path.exists():
        log.warning("%s not found — tier-1 leagues fall back to the "
                    "`missing` policy (run train_tier1_gate.py --write-gates)",
                    path)
        return {}
    try:
        gates = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        log.warning("tier-1 gates unreadable (%s): %s", path, e)
        return {}
    if not isinstance(gates, dict):
        log.warning("tier-1 gates malformed (%s): expected an object", path)
        return {}
    passed = sorted(c for c, g in gates.items()
                    if isinstance(g, dict) and g.get("pass"))
    log.info("tier-1 gates loaded: %d leagues, %d beat the book (%s)",
             len(gates), len(passed), ", ".join(passed) or "none")
    return gates


def tier1_gate_enabled() -> bool:
    return bool(_gate_config().get("enabled", True))


def tier1_gate_passed(league_code: str | None) -> bool:
    """True only when this tier-1 league is cleared to displace the market."""
    if not tier1_gate_enabled():
        return True
    if not league_code:
        return False
    entry = load_tier1_gates().get(league_code)
    if entry is None:
        return str(_gate_config().get("missing", "fail")).lower() == "pass"
    return bool(entry.get("pass"))


def gated_tier1_leagues() -> list[str]:
    """Tier-1 leagues with a recorded gate that they failed."""
    return sorted(c for c, g in load_tier1_gates().items()
                  if isinstance(g, dict) and not g.get("pass"))


def gate_note(league_code: str, tier: int) -> str:
    """One-line explanation for the resolver-notes digest."""
    entry = load_tier1_gates().get(league_code) if tier == 1 else None
    detail = ""
    if entry:
        detail = (f" (walk-forward Brier {entry.get('brier_model')} vs book "
                  f"{entry.get('brier_book')} over n={entry.get('n')})")
    return (f"{league_code or f'tier-{tier}'} model is gated out "
            f"(loses to the bookmaker in EdgeBot's walk-forward){detail} — "
            "using de-vigged market odds")
