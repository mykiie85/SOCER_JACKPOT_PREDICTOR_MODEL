"""Bridge into EdgeBot's trained pipeline — load, never retrain.

EdgeBot (EDGEBOT_PATH) is a separate project with its own top-level packages
(config, models, data, ...). We add its root to sys.path once and reuse:

- ``daily_runner.build_ensemble`` — the exact production build path: cached
  calibrator + cached feature boosters (catboost/LightGBM) are *loaded* from
  data_cache/*.pkl; only the cheap statistical models (Dixon-Coles, Elo, Pi)
  are re-fit on the cached history, same as every EdgeBot daily run.
- ``models.tier2_stack.Tier2Stack`` — the tier-2 league stack (Argentina,
  MLS, J-League, ...), fit on historical_tier2.parquet.

Fixtures are passed as a DataFrame with columns ``league`` (football-data
code), ``home_team``, ``away_team`` — the ensemble resolves the league via
its own ``resolve_league_code`` and canonicalises team spellings internally.
"""
from __future__ import annotations

import logging
import sys
from functools import lru_cache
from pathlib import Path

import pandas as pd

from jackpot_predictor.config.settings import EDGEBOT_PATH

log = logging.getLogger(__name__)

_path_added = False


def ensure_edgebot_on_path() -> None:
    """Put EdgeBot's root first on sys.path (idempotent)."""
    global _path_added
    if _path_added:
        return
    root = Path(EDGEBOT_PATH)
    if not (root / "models" / "orchestrator.py").exists():
        raise FileNotFoundError(
            f"EDGEBOT_PATH={root} does not look like an EdgeBot checkout "
            "(models/orchestrator.py missing)")
    sys.path.insert(0, str(root))
    _path_added = True


@lru_cache(maxsize=1)
def edgebot_league_codes() -> tuple[frozenset, frozenset]:
    """(tier1_codes, tier2_codes) as EdgeBot defines them."""
    ensure_edgebot_on_path()
    from config import LEAGUE_CODES, TIER2_LEAGUE_CODES  # EdgeBot's config.py
    return frozenset(LEAGUE_CODES), frozenset(TIER2_LEAGUE_CODES)


def canon_team(name: str) -> str:
    """EdgeBot's own alias map: 'Manchester United' -> 'Man United' etc."""
    ensure_edgebot_on_path()
    try:
        from data_sources.kaggle_ingest import _canon_team
        return _canon_team(name)
    except ImportError:
        return name


class EdgeBotBridge:
    """Lazy holder for the fitted tier-1 ensemble and tier-2 stack."""

    def __init__(self):
        ensure_edgebot_on_path()
        self._ensemble = None
        self._history: pd.DataFrame | None = None
        self._tier2 = None
        self._tier2_history: pd.DataFrame | None = None

    # ------------------------------------------------------------- history
    @property
    def history(self) -> pd.DataFrame:
        """Tier-1 cached history (data_cache/historical.parquet + xG attach)."""
        if self._history is None:
            from daily_runner import load_history
            self._history = load_history(refresh=False)
        return self._history

    @property
    def tier2_history(self) -> pd.DataFrame | None:
        if self._tier2_history is None:
            try:
                from data.historical_loader import load_tier2
                self._tier2_history = load_tier2()
            except Exception as e:  # noqa: BLE001 - tier-2 data is optional
                log.warning("tier-2 history unavailable: %s", e)
                self._tier2_history = pd.DataFrame()
        return self._tier2_history

    def league_teams(self, league_code: str) -> set[str]:
        """All team spellings EdgeBot's history knows for one league."""
        tier1, tier2 = edgebot_league_codes()
        df = self.history if league_code in tier1 else self.tier2_history
        if df is None or df.empty or "league" not in df.columns:
            return set()
        sub = df[df["league"] == league_code]
        return set(sub["HomeTeam"].dropna()) | set(sub["AwayTeam"].dropna())

    # -------------------------------------------------------------- models
    @property
    def ensemble(self):
        """Fitted tier-1 ensemble, built exactly like EdgeBot's daily run."""
        if self._ensemble is None:
            from daily_runner import build_ensemble
            log.info("Building EdgeBot ensemble (cached boosters are loaded, "
                     "not retrained) ...")
            self._ensemble, self._history, _ = build_ensemble(history=self._history)
        return self._ensemble

    @property
    def tier2(self):
        """Fitted tier-2 stack, or None when its history/model is absent."""
        if self._tier2 is None:
            try:
                from models.config_loader import load_config
                from models.tier2_stack import Tier2Stack
                hist = self.tier2_history
                if hist is None or hist.empty:
                    self._tier2 = False
                else:
                    log.info("Fitting tier-2 stack on %d rows", len(hist))
                    self._tier2 = Tier2Stack(load_config()).fit(hist)
            except Exception as e:  # noqa: BLE001 - tier-2 is best-effort
                log.warning("tier-2 stack unavailable: %s", e)
                self._tier2 = False
        return self._tier2 or None

    # ------------------------------------------------------------- predict
    def predict_batch(self, fixtures: list[dict]) -> list[dict | None]:
        """1X2 probabilities for resolved fixtures.

        Each input dict needs: league_code, home_team_canonical,
        away_team_canonical, tier (1 or 2). Returns per fixture either
        ``{"home": p, "draw": p, "away": p, "low_confidence": bool,
        "unknown_team": str|None}`` or None when no model could price it.
        """
        results: list[dict | None] = [None] * len(fixtures)
        for tier in (1, 2):
            idx = [i for i, f in enumerate(fixtures) if f.get("tier") == tier]
            if not idx:
                continue
            model = self.ensemble if tier == 1 else self.tier2
            if model is None:
                log.warning("tier-%d model unavailable — %d fixtures skipped",
                            tier, len(idx))
                continue
            frame = pd.DataFrame([{
                "league": fixtures[i]["league_code"],
                "home_team": fixtures[i]["home_team_canonical"],
                "away_team": fixtures[i]["away_team_canonical"],
                "event_id": fixtures[i].get("event_id", ""),
            } for i in idx])
            try:
                preds = model.predict(frame)
            except Exception as e:  # noqa: BLE001 - surface as unpriced fixtures
                log.error("tier-%d predict failed: %s", tier, e)
                continue
            for row_pos, i in enumerate(idx):
                r = preds.iloc[row_pos]
                h, d, a = r.get("p_home"), r.get("p_draw"), r.get("p_away")
                if h is None or pd.isna(h):
                    continue
                results[i] = {
                    "home": float(h), "draw": float(d), "away": float(a),
                    "low_confidence": bool(r.get("low_confidence", False)),
                    "unknown_team": r.get("unknown_team") or None,
                }
        return results
