#!/usr/bin/env python3
"""Seed jackpot_production_log.jsonl from the slates already graded on disk.

    python scripts/backfill_production_log.py [--dry-run]

``results_main.py`` logs model-vs-market for each slate as it settles, but eight
slates were graded before that existed. Their archives and result snapshots are
still in ``data/jackpots/``, so the same rows can be reconstructed rather than
waiting eight more weeks to re-earn them.

What backfilled rows can and cannot say: the market side is complete, because
SportPesa's published prices are stored with every fixture. The model side is
only present where the model actually priced the fixture — most older fixtures
fell back to market odds and carry no model probability at all, so they log a
null model score rather than a fabricated one. That is the honest shape of the
sample: it is why the comparison needs the *forward* log to reach a usable n.

Re-running is safe; rows are keyed on (jackpot_id, fixture_id).
"""
from __future__ import annotations

import argparse
import glob
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jackpot_predictor.config.settings import JACKPOTS_DIR  # noqa: E402
from jackpot_predictor.results.grader import (  # noqa: E402
    PRODUCTION_LOG, format_production_summary, grade, log_production_results,
    production_summary, results_by_event)


def archives_with_results():
    """[(archive, results)] for every slate that has a results snapshot."""
    out = []
    for f in sorted(glob.glob(str(JACKPOTS_DIR / "*_results.json"))):
        name = Path(f).name
        jtype, hid = name.split("_")[0], name.split("_")[1]
        results = results_by_event(json.loads(Path(f).read_text(encoding="utf-8")))
        candidates = [a for a in sorted(glob.glob(
            str(JACKPOTS_DIR / f"{jtype}_{hid}_2*.json")))
            if not a.endswith("_results.json")]
        for path in reversed(candidates):
            try:
                d = json.loads(Path(path).read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(d, dict) and d.get("jackpot") and d.get("predictions"):
                out.append((d, results))
                break
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true",
                    help="report what would be written; write nothing")
    args = ap.parse_args()

    pairs = archives_with_results()
    if not pairs:
        print("No graded slates found in", JACKPOTS_DIR)
        return 1
    total_written = total_skipped = 0
    for archive, results in pairs:
        graded = grade(archive, results)
        hid = archive["jackpot"].get("human_id")
        if args.dry_run:
            playable = sum(1 for r in graded["rows"]
                           if r.get("result_status") == "played")
            print(f"  #{hid}: {playable} settled fixtures would be considered")
            continue
        stats = log_production_results(archive, graded)
        total_written += stats["written"]
        total_skipped += stats["skipped"]
        print(f"  #{hid}: +{stats['written']} rows "
              f"({stats['skipped']} already present or unscoreable)")

    if args.dry_run:
        return 0
    print(f"\n{total_written} rows written, {total_skipped} skipped -> "
          f"{PRODUCTION_LOG}")
    print(format_production_summary(production_summary()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
