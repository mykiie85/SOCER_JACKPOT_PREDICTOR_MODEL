#!/usr/bin/env python3
"""Rebuild production-log rows for finished rounds from their saved results.

    python scripts/regrade_production_log.py 225 242 --dry-run
    python scripts/regrade_production_log.py 225 242 --drop 243

The grader used to grade from the live feed, whose scores fill in while a
match is still being played, and the log never re-writes a row it already
holds — so an in-play score stuck for good (#240 match 11: logged a draw at
90 minutes, finished 2:4). This re-grades each round in [first, last] from
data/jackpots/<type>_<round>_results.json (check it against SportPesa's
history first: those snapshots are final only once the round is Finished),
using the round's last saved slip, and rewrites its rows with both lines.

Every changed grade is printed. Rows keep their original graded_at and gain
regraded_at. --drop removes a round's rows (an unfinished round, which the
fixed grader logs by itself once SportPesa marks it Finished). Rows for
rounds outside the range are kept as they are. Also refreshes each round's
<type>_<round>_line_grades.json.
"""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jackpot_predictor.config.settings import JACKPOTS_DIR  # noqa: E402
from jackpot_predictor.predictor import lean_line  # noqa: E402
from jackpot_predictor.results import grader as G  # noqa: E402

# Fields that legitimately change when the result does.
_OUTCOME_FIELDS = {"actual_result", "model_correct", "model_logloss", "model_brier",
                   "market_correct", "market_logloss", "market_brier",
                   "favourite_line_correct", "lean_line_correct"}
_NEW_FIELDS = {"favourite_line_pick", "favourite_line_correct", "lean_line_pick",
               "lean_line_correct", "lean_line_strength", "graded_at",
               "regraded_at"}


def _read_log() -> list[dict]:
    if not G.PRODUCTION_LOG.exists():
        return []
    return [json.loads(l) for l in G.PRODUCTION_LOG.read_text(
        encoding="utf-8").splitlines() if l.strip()]


def _last_slip(jtype: str, n: int) -> Path | None:
    slips = sorted(p for p in JACKPOTS_DIR.glob(f"{jtype}_{n}_*.json")
                   if not p.name.endswith(("_results.json", "_line_grades.json")))
    return slips[-1] if slips else None


def regrade_round(jtype: str, n: int) -> tuple[list[dict], dict | None, dict]:
    """(new log rows, line grades, archive) for one round."""
    slip = _last_slip(jtype, n)
    if slip is None:
        raise SystemExit(f"#{n}: no saved slip")
    archive = json.loads(slip.read_text(encoding="utf-8"))
    archive["_archive_path"] = str(slip)
    snap = JACKPOTS_DIR / f"{jtype}_{n}_results.json"
    results = G.results_by_event(json.loads(snap.read_text(encoding="utf-8")))
    graded = G.grade(archive, results)
    if graded["pending"]:
        raise SystemExit(f"#{n}: {graded['pending']} matches still unsettled in "
                         f"{snap.name} — not a finished round")
    orig = G.PRODUCTION_LOG
    with tempfile.TemporaryDirectory() as d:
        G.PRODUCTION_LOG = Path(d) / "log.jsonl"
        try:
            G.log_production_results(archive, graded)
            rows = [json.loads(l) for l in G.PRODUCTION_LOG.read_text(
                encoding="utf-8").splitlines() if l.strip()]
        finally:
            G.PRODUCTION_LOG = orig
    with_lines = archive if isinstance(archive.get("lean_line"), list) else \
        {**archive, **lean_line.build_lines(archive["predictions"])}
    return rows, G.grade_lines(with_lines, results), with_lines


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("first", type=int)
    ap.add_argument("last", type=int)
    ap.add_argument("--type", default="supa17")
    ap.add_argument("--drop", type=int, nargs="*", default=[])
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not lean_line.enabled():
        raise SystemExit("lean_line.enabled is false — both lines cannot be graded")

    old = _read_log()
    rounds = set(range(args.first, args.last + 1))
    touched = {str(n) for n in rounds | set(args.drop)}
    old_by_key = {(r["jackpot_id"], r["fixture_id"]): r for r in old}
    now = datetime.now(timezone.utc).isoformat()

    new_rows, line_grades, changes, odd = [], {}, [], []
    for n in sorted(rounds):
        rows, lg, archive = regrade_round(args.type, n)
        line_grades[n] = (lg, archive)
        match_of = {p["event_id"]: p["match_number"] for p in archive["predictions"]}
        for r in rows:
            key = (r["jackpot_id"], r["fixture_id"])
            prev = old_by_key.get(key)
            r["graded_at"] = prev["graded_at"] if prev else now
            r["regraded_at"] = now
            m = match_of.get(r["fixture_id"])
            if prev is None:
                changes.append((n, m, r["home_team"], r["away_team"], "(not logged)",
                                r["actual_result"], r["published_pick"]))
            elif prev["actual_result"] != r["actual_result"]:
                changes.append((n, m, r["home_team"], r["away_team"],
                                prev["actual_result"], r["actual_result"],
                                r["published_pick"]))
            if prev is not None:
                for k in set(prev) | set(r):
                    if k in _NEW_FIELDS or (k in _OUTCOME_FIELDS and
                                            prev["actual_result"] != r["actual_result"]):
                        continue
                    if prev.get(k) != r.get(k):
                        odd.append((n, m, k, prev.get(k), r.get(k)))
            new_rows.append(r)
        regraded = {(r["jackpot_id"], r["fixture_id"]) for r in rows}
        for key, prev in old_by_key.items():
            if key[0] == str(n) and key not in regraded:
                changes.append((n, match_of.get(key[1]), prev["home_team"],
                                prev["away_team"], prev["actual_result"],
                                "(removed: not played)", prev["published_pick"]))

    kept = [r for r in old if r["jackpot_id"] not in touched]
    dropped = [r for r in old if int(r["jackpot_id"]) in args.drop]

    def correct(rows, field="published_pick"):
        return sum(r.get(field) == r["actual_result"] for r in rows)

    in_range = [r for r in old if int(r["jackpot_id"]) in rounds]
    print(f"rows: {len(old)} before -> {len(kept) + len(new_rows)} after "
          f"({len(dropped)} dropped for rounds {args.drop or '-'})")
    print(f"published picks correct, #{args.first}-#{args.last}: "
          f"{correct(in_range)} before -> {correct(new_rows)} after "
          f"(of {len(new_rows)} played)")
    print(f"favourite line {correct(new_rows, 'favourite_line_pick')}, "
          f"lean line {correct(new_rows, 'lean_line_pick')} "
          f"(strength {lean_line.settings()['strength']})")
    print(f"\nchanged grades ({len(changes)}):")
    print("  round  match  teams                                    old -> new   pick  "
          "correct")
    for n, m, h, a, o, nw, pk in changes:
        teams = f"{h} v {a}"[:40]
        ok = "" if nw not in ("H", "D", "A") else \
            f"{'yes' if o == pk else 'no'} -> {'yes' if nw == pk else 'no'}"
        print(f"  #{n:<5} {m!s:>5}  {teams:<40} {o} -> {nw}   {pk}     {ok}")
    if odd:
        print(f"\nUNEXPECTED field differences ({len(odd)}) — not writing:")
        for o in odd[:30]:
            print("  ", o)
        return 1
    if args.dry_run:
        print("\nDRY RUN — nothing written")
        return 0

    out = sorted(kept + new_rows, key=lambda r: (int(r["jackpot_id"]),
                                                 r.get("kickoff") or "",
                                                 r["fixture_id"]))
    tmp = G.PRODUCTION_LOG.with_name(G.PRODUCTION_LOG.name + ".tmp")
    tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in out),
                   encoding="utf-8")
    tmp.replace(G.PRODUCTION_LOG)
    for n, (lg, archive) in line_grades.items():
        if lg:
            G.save_line_grades(archive, lg)
    print(f"\nwrote {G.PRODUCTION_LOG} ({len(out)} rows) and "
          f"{len(line_grades)} line-grade files")
    return 0


if __name__ == "__main__":
    sys.exit(main())
