#!/usr/bin/env python3
"""The audit that has to pass before confidence tiers can be re-enabled.

    python scripts/audit_confidence.py

``confidence.force_uncertain`` suspends the HIGH/MEDIUM/LOW labels because on
graded fixtures they were anti-correlated with accuracy — a reader who trusted
them did worse than one who ignored them. That is a symptom, and this script
tests the two things that decide whether it was miscalibration or noise:

**1. Is the probability reliability curve monotone?** Bucket every graded pick
by its stated probability and compare with the realised hit rate. A tier label
is only ever a coarsening of the underlying probability, so if the probability
itself is not monotone in reality, no thresholding of it can be.

**2. Do the tiers separate, in the right order?** HIGH must out-hit MEDIUM must
out-hit LOW, by enough to survive the sample size. The script reports a Wilson
interval per tier; overlapping intervals mean the ordering is not established
however tidy the point estimates look.

Both must pass. Neither is close today: n=3 in HIGH cannot establish anything,
and the observed ordering runs backwards. Re-run this after the production log
(``data/jackpots/jackpot_production_log.jsonl``) has accumulated fixtures, and
flip ``confidence.force_uncertain`` to false only when it says PASS.
"""
from __future__ import annotations

import math
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.audit_draws import graded_slates  # noqa: E402

TIER_ORDER = ["HIGH", "MEDIUM", "LOW", "UNCERTAIN"]


def wilson(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval — behaves sanely at the tiny n these tiers have."""
    if n == 0:
        return (0.0, 1.0)
    p = hits / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, centre - half), min(1.0, centre + half))


def reliability(rows, buckets: int = 5):
    """[(lo, hi, n, stated, observed)] over stated-probability buckets."""
    binned = defaultdict(lambda: [0, 0, 0.0])
    for p, out in rows:
        prob = p["primary_prob"]
        b = min(int(prob * buckets), buckets - 1)
        binned[b][0] += 1
        binned[b][1] += int(p["primary_pick"] == out)
        binned[b][2] += prob
    return [(b / buckets, (b + 1) / buckets, n, s / n, h / n)
            for b, (n, h, s) in sorted(binned.items()) if n]


def main() -> int:
    rows = [r for v in graded_slates().values() for r in v]
    if not rows:
        print("No graded fixtures — nothing to audit.")
        return 1
    print(f"=== Confidence audit over {len(rows)} graded picks ===\n")

    print("1. Reliability of the stated probability")
    print(f"   {'bucket':>12} {'n':>4} {'stated':>8} {'observed':>9}")
    curve = reliability(rows)
    for lo, hi, n, stated, observed in curve:
        print(f"   {lo:.0%}-{hi:.0%}".ljust(15)
              + f"{n:4} {stated:8.1%} {observed:9.1%}")
    obs = [c[4] for c in curve]
    monotone = all(b >= a for a, b in zip(obs, obs[1:]))
    print(f"   -> reliability curve is "
          f"{'MONOTONE' if monotone else 'NOT monotone'}\n")

    print("2. Do the tiers separate in the right order?")
    print(f"   {'tier':11} {'n':>4} {'hit':>7} {'stated':>8}  95% interval")
    tiers = defaultdict(lambda: [0, 0, 0.0])
    for p, out in rows:
        # Prefer the uncapped verdict: while force_uncertain is on, the
        # published label is UNCERTAIN for everything and carries no ordering.
        t = p.get("uncapped_confidence_tier") or p.get("confidence_tier")
        tiers[t][0] += 1
        tiers[t][1] += int(p["primary_pick"] == out)
        tiers[t][2] += p["primary_prob"]
    hits = {}
    for t in TIER_ORDER:
        if t not in tiers:
            continue
        n, h, s = tiers[t]
        lo, hi = wilson(h, n)
        hits[t] = (h / n, lo, hi, n)
        print(f"   {t:11} {n:4} {h / n:7.1%} {s / n:8.1%}  "
              f"[{lo:.1%}, {hi:.1%}]")

    ranked = [t for t in ("HIGH", "MEDIUM", "LOW") if t in hits]
    ordered = all(hits[a][0] >= hits[b][0] for a, b in zip(ranked, ranked[1:]))
    separated = all(hits[a][1] > hits[b][2] for a, b in zip(ranked, ranked[1:]))
    print(f"   -> ordering HIGH >= MEDIUM >= LOW: "
          f"{'holds' if ordered else 'BROKEN'}")
    print(f"   -> intervals actually separate: "
          f"{'yes' if separated else 'no (sample too small to claim an ordering)'}\n")

    ok = monotone and ordered and separated
    print("VERDICT:", "PASS — confidence.force_uncertain may be set to false"
          if ok else
          "FAIL — keep confidence.force_uncertain: true")
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
