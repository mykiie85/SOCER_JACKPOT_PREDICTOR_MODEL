#!/usr/bin/env python3
"""Is the jackpot bot blind to draws? Audit the whole probability pipeline.

    python scripts/audit_draws.py              # replay archived graded slates
    python scripts/audit_draws.py --ensemble   # also: raw vs calibrated tier-1

The symptom that prompted this: the bot picks a draw on ~2% of jackpot matches
while ~24% of them end level, which looks like the model has been trained or
calibrated into ignoring draws. The audit separates two very different causes:

1. **Probability suppression** — the pipeline genuinely under-states P(draw).
   That would be a real defect, and it is what this script measures:
   ``--ensemble`` captures the tier-1 fusion's 1X2 vector *before* the isotonic
   calibrator runs (by wrapping ``Ensemble._calibrate``) and
   compares it with the calibrated output; the replay compares mean stated
   P(draw) against the realised draw rate on graded fixtures.

2. **Argmax arithmetic** — the probabilities are fine, but a draw is rarely the
   *single most likely* outcome. P(draw) lives in a narrow band around .25-.30
   for almost every fixture while home and away range from .10 to .70, so the
   draw is the maximum only in the tightest matches. A pick rate far below the
   24% base rate is the arithmetic working correctly, not a bug.

The distinction decides whether to change anything, so the script also prices
the proposed remedy. Forcing a draw quota is scored under the objective the
jackpot actually has — P(at least 13 of 17 correct), computed exactly with a
Poisson-binomial convolution over the slate's own pick probabilities. For
independent picks that probability is increasing in every individual pick
probability, so a quota can only pay off if it raises them; picking anything
other than the argmax lowers them by construction.
"""
from __future__ import annotations

import argparse
import glob
import json
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jackpot_predictor.config.settings import JACKPOTS_DIR  # noqa: E402
from jackpot_predictor.results.grader import results_by_event  # noqa: E402

OUTCOMES = ("home", "draw", "away")
PICK_OF = {"H": "prob_home", "D": "prob_draw", "A": "prob_away"}


# ─────────────────────────────────────────────────────────── graded replay
def graded_slates() -> dict[str, list[tuple[dict, str]]]:
    """{human_id: [(prediction, actual_outcome), ...]} for settled fixtures."""
    slates: dict[str, list[tuple[dict, str]]] = defaultdict(list)
    for f in sorted(glob.glob(str(JACKPOTS_DIR / "*_results.json"))):
        hid = Path(f).name.split("_")[1]
        results = results_by_event(json.loads(Path(f).read_text()))
        archives = [a for a in sorted(glob.glob(
            str(JACKPOTS_DIR / f"supa17_{hid}_2*.json")))
            if not a.endswith("_results.json")]
        if not archives:
            continue
        for p in json.loads(Path(archives[-1]).read_text()).get("predictions", []):
            r = results.get(p.get("event_id"))
            if not r or r["status"] != "played" or not p.get("primary_pick"):
                continue
            slates[hid].append((p, r["outcome"]))
    return slates


def poisson_binomial_ge(ps: list[float], k: int) -> float:
    """Exact P(at least k successes) for independent, differently-biased picks."""
    dist = [1.0]
    for p in ps:
        nxt = [0.0] * (len(dist) + 1)
        for i, v in enumerate(dist):
            nxt[i] += v * (1 - p)
            nxt[i + 1] += v * p
        dist = nxt
    return sum(dist[k:])


def _forced_draw_picks(rows: list[tuple[dict, str]], quota: int) -> list[str]:
    """Argmax picks with `quota` of them flipped to a draw.

    The flips are chosen to cost as little probability as possible — the
    fixtures where P(draw) is closest to the leader — which is the most
    favourable version of the proposed remedy.
    """
    picks = [p["primary_pick"] for p, _ in rows]
    order = sorted(range(len(rows)),
                   key=lambda i: rows[i][0]["primary_prob"] - rows[i][0]["prob_draw"])
    for i in order[:quota]:
        picks[i] = "D"
    return picks


def report_replay(slates: dict) -> None:
    rows = [r for v in slates.values() for r in v]
    if not rows:
        print("No graded fixtures found — nothing to audit.")
        return
    n = len(rows)
    actual_draw = sum(1 for _, o in rows if o == "D") / n

    print(f"\n=== Stated P(draw) vs reality — {n} graded fixtures, "
          f"{len(slates)} slates ===")
    print(f"{'source':12} {'n':>4} {'mean p_draw':>12} {'median':>8} {'max':>7} "
          f"{'draw picked':>12} {'draws happened':>15}")
    for src in sorted({(p.get('source') or '?') for p, _ in rows}):
        sub = [(p, o) for p, o in rows if (p.get("source") or "?") == src]
        d = [p["prob_draw"] for p, _ in sub]
        picked = sum(1 for p, _ in sub if p["primary_pick"] == "D")
        happened = sum(1 for _, o in sub if o == "D")
        print(f"{src:12} {len(sub):4} {st.mean(d):12.3f} {st.median(d):8.3f} "
              f"{max(d):7.3f} {picked:>7}/{len(sub):<4} {happened:>10}/{len(sub):<4}")
    alld = [p["prob_draw"] for p, _ in rows]
    picked = sum(1 for p, _ in rows if p["primary_pick"] == "D")
    print(f"{'ALL':12} {n:4} {st.mean(alld):12.3f} {st.median(alld):8.3f} "
          f"{max(alld):7.3f} {picked:>7}/{n:<4} "
          f"{sum(1 for _, o in rows if o == 'D'):>10}/{n:<4}")

    verdict = ("SUPPRESSED — stated P(draw) is far below the realised rate"
               if st.mean(alld) < actual_draw - 0.05 else
               "NOT suppressed — stated P(draw) tracks the realised draw rate")
    print(f"\n  mean stated P(draw) {st.mean(alld):.1%} vs realised "
          f"{actual_draw:.1%}  ->  {verdict}")
    print(f"  draw is the argmax on {picked}/{n} = {picked / n:.1%} of fixtures; "
          "that gap is argmax arithmetic, not suppression.")

    print("\n=== Is P(draw) calibrated? (decile buckets) ===")
    buckets: dict[float, list[int]] = defaultdict(lambda: [0, 0])
    for p, o in rows:
        k = round(p["prob_draw"] * 20) / 20  # 5-point buckets
        buckets[k][0] += 1
        buckets[k][1] += int(o == "D")
    for k in sorted(buckets):
        cnt, hit = buckets[k]
        print(f"  stated {k:.2f}  n={cnt:3}  observed {hit / cnt:6.1%}")

    print("\n=== What a forced draw quota would do ===")
    print("Scored on P(>=13 of 17), the objective that actually pays. Realised")
    print("hit rate over so few slates is noise; the probability is not.")
    print(f"{'strategy':22} {'hit rate':>10} {'mean pick p':>12} {'mean P(>=13)':>14}")
    for label, frac in (("argmax (current)", 0.0), ("force 15% draws", 0.15),
                        ("force 24% draws", 0.24)):
        hits = tot = 0
        pick_ps: list[float] = []
        per_slate: list[float] = []
        for slate in slates.values():
            quota = round(frac * len(slate))
            picks = (_forced_draw_picks(slate, quota) if quota
                     else [p["primary_pick"] for p, _ in slate])
            ps = []
            for (p, out), pick in zip(slate, picks):
                hits += int(pick == out)
                tot += 1
                ps.append(p[PICK_OF[pick]])
            pick_ps += ps
            per_slate.append(poisson_binomial_ge(ps, 13))
        print(f"{label:22} {hits / tot:9.1%} {st.mean(pick_ps):12.3f} "
              f"{st.mean(per_slate):14.2e}")


# ──────────────────────────────────────────────── raw vs calibrated tier-1
def report_ensemble(slates: dict, limit: int) -> None:
    """Capture the tier-1 fusion's 1X2 before and after the isotonic calibrator.

    Nothing in production changes: ``_calibrate`` is wrapped for the duration of
    this call so the pre-calibration vector can be recorded alongside its
    calibrated counterpart.
    """
    import pandas as pd

    from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge

    fixtures = []
    seen = set()
    for slate in slates.values():
        for p, _ in slate:
            key = (p.get("league_code"), p.get("home_team_canonical"),
                   p.get("away_team_canonical"))
            if not p.get("resolved") or p.get("tier") != 1 or key in seen:
                continue
            seen.add(key)
            fixtures.append(p)
    fixtures = fixtures[:limit]
    if not fixtures:
        print("\nNo resolved tier-1 fixtures in the archives — skipping.")
        return

    print(f"\n=== Raw fusion vs calibrated tier-1 output ({len(fixtures)} "
          "fixtures) ===")
    bridge = EdgeBotBridge()
    ensemble = bridge.ensemble

    from models.orchestrator import Ensemble
    original = Ensemble._calibrate
    captured: list[tuple[dict, dict]] = []

    def spy(self, out, code):
        before = {k: out.get(k) for k in ("p_home", "p_draw", "p_away")}
        original(self, out, code)
        captured.append((before, {k: out.get(k)
                                  for k in ("p_home", "p_draw", "p_away")}))

    Ensemble._calibrate = spy
    try:
        ensemble.predict(pd.DataFrame([{
            "league": f["league_code"], "home_team": f["home_team_canonical"],
            "away_team": f["away_team_canonical"], "event_id": f["event_id"],
        } for f in fixtures]))
    finally:
        Ensemble._calibrate = original

    if not captured:
        print("  Calibration never ran (calibration.enabled is off, or every "
              "row used the stacker, which is already a calibrated combiner).")
        return
    raw = [b["p_draw"] for b, _ in captured if b["p_draw"] is not None]
    cal = [a["p_draw"] for _, a in captured if a["p_draw"] is not None]
    print(f"  raw fusion   mean P(draw) {st.mean(raw):.3f}  "
          f"min {min(raw):.3f}  max {max(raw):.3f}")
    print(f"  calibrated   mean P(draw) {st.mean(cal):.3f}  "
          f"min {min(cal):.3f}  max {max(cal):.3f}")
    shift = st.mean(cal) - st.mean(raw)
    print(f"  calibration shifts P(draw) by {shift:+.3f}")
    if st.mean(raw) < 0.15:
        print("  -> PRE-CALIBRATION suppression: the fusion itself under-states "
              "draws. Look at the feature/target construction.")
    elif st.mean(cal) < 0.05:
        print("  -> POST-CALIBRATION suppression: the isotonic map is "
              "collapsing draws. Try Platt or temperature scaling instead.")
    else:
        print("  -> No suppression at either stage.")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--ensemble", action="store_true",
                    help="also build the tier-1 ensemble and compare raw vs "
                         "calibrated draw probabilities (slow: loads boosters)")
    ap.add_argument("--limit", type=int, default=60,
                    help="max fixtures to re-price under --ensemble")
    args = ap.parse_args()

    slates = graded_slates()
    report_replay(slates)
    if args.ensemble:
        report_ensemble(slates, args.limit)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
