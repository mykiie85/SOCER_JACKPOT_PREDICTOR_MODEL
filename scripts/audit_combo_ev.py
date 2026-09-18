"""Expected value of a 13-of-17 combination ticket on the 13/13 tier.

Uses the archive joined to Bet365 closing odds (same join as
audit_archive_odds.py). For every slate with all 17 fixtures priced it builds
the best ticket under SportPesa's combination limits:

  * pick the 13 fixtures and assign D doubles / T triples to maximise the
    probability that every line-covered outcome lands (a double covers the
    two likeliest outcomes, a triple covers all three);
  * cost = 1,000 TZS x 2^D x 3^T lines;
  * payout = prize table of the 13/13 tier: the full prize to a sole 13/13
    winner, plus the archive's median per-winner amounts for 12/13 and
    11/13 and 10/13 lines the ticket also holds.

Prints P(13/13), stake, expected return and the empirical hit count over the
archived slates. The 13/13 prize is a guaranteed pot (it has inflated ~4%/yr,
not rolled over from stakes), so the winners' share assumption is 1.
"""
from __future__ import annotations

import itertools
import math
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from rapidfuzz import fuzz

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jackpot_predictor.predictor.edgebot_bridge import EdgeBotBridge  # noqa: E402
from jackpot_predictor.resolver.fuzzy_matcher import core_tokens  # noqa: E402
from jackpot_predictor.scraper.archive import load_archive  # noqa: E402

PICK = {"Home": "H", "Draw": "D", "Away": "A"}
LINE_TZS = 1000
LIMITS = {"only_doubles": 10, "only_triples": 5, "mix_doubles": 9, "mix_triples": 5, "mix_total": 10}


def _norm(name: str) -> str:
    return " ".join(core_tokens(name))


def joined_slates():
    b = EdgeBotBridge()
    hist = b.history
    if b.tier2_history is not None:
        hist = pd.concat([hist, b.tier2_history], ignore_index=True)
    hist = hist.dropna(subset=["B365H", "B365D", "B365A"]).copy()
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
    hist["nh"] = hist["HomeTeam"].map(_norm)
    hist["na"] = hist["AwayTeam"].map(_norm)
    by_date = {d: g for d, g in hist.groupby("Date")}
    slates = {}
    for j in load_archive():
        if len(j.get("events") or []) != 17:
            continue
        fx = []
        for ev in j["events"]:
            o = PICK.get(ev.get("resultPick"))
            if not o:
                break
            k = datetime.fromisoformat(ev["kickoffTime"].replace("Z", "+00:00"))
            h, a = _norm(ev["competitorHome"]), _norm(ev["competitorAway"])
            best, best_s, second = None, 0, 0
            for dd in (-1, 0, 1):
                g = by_date.get((k + timedelta(days=dd)).date())
                if g is None:
                    continue
                for r in g.itertuples(index=False):
                    s = min(fuzz.WRatio(h, r.nh), fuzz.WRatio(a, r.na))
                    if s > best_s:
                        second, best_s, best = best_s, s, r
                    elif s > second:
                        second = s
            if best is None or best_s < 80 or best_s - second < 5 or best.FTR != o:
                break
            inv = np.array([1 / best.B365H, 1 / best.B365D, 1 / best.B365A])
            fx.append((inv / inv.sum(), o))
        if len(fx) == 17:
            slates[j["jackpotHumanId"]] = fx
    return slates


def best_ticket(fx, n_double, n_triple):
    """Choose 13 of 17 and the doubles/triples to maximise P(all covered)."""
    # value of a fixture as single / double / triple
    p = np.array([sorted(f[0], reverse=True) for f in fx])
    single, double = p[:, 0], p[:, 0] + p[:, 1]
    # Greedy over an exhaustive split: triples go to the fixtures whose
    # double coverage is worst (largest gain), doubles to the next worst,
    # singles to the most confident. Exhaustive over the 13-subset is 2380
    # combos; cheap.
    best = (-math.inf, None)
    for subset in itertools.combinations(range(17), 13):
        idx = np.array(subset)
        # order by how much a double adds relative to a single
        gain = np.log(double[idx]) - np.log(single[idx])
        order = idx[np.argsort(-gain)]
        triples, doubles, singles = order[:n_triple], order[n_triple:n_triple + n_double], order[n_triple + n_double:]
        lp = np.log(double[doubles]).sum() + np.log(single[singles]).sum()
        if lp > best[0]:
            best = (lp, (triples, doubles, singles))
    return math.exp(best[0]), best[1]


def ticket_hits(fx, plan):
    """Number of correct legs on the best line of the ticket."""
    triples, doubles, singles = plan
    hits = len(triples)
    for i in doubles:
        probs, o = fx[i]
        top2 = np.argsort(-probs)[:2]
        hits += int("HDA".index(o) in top2)
    for i in singles:
        probs, o = fx[i]
        hits += int("HDA".index(o) == int(np.argmax(probs)))
    return hits


def main() -> int:
    slates = joined_slates()
    print(f"slates with all 17 fixtures priced: {len(slates)}")
    # prize table (13/13 tier), from audit_archive.py
    prize_13 = 354_000_000
    per_winner = {12: 1_456_896, 11: 688_662, 10: 154_231}
    plans = [(0, 0), (5, 0), (10, 0), (0, 5), (5, 5)]
    for nd, nt in plans:
        lines = 2 ** nd * 3 ** nt
        stake = lines * LINE_TZS
        p_hit, hits, ev_low = [], Counter(), []
        for fx in slates.values():
            p, plan = best_ticket(fx, nd, nt)
            p_hit.append(p)
            hits[ticket_hits(fx, plan)] += 1
        pm = float(np.mean(p_hit))
        n = len(slates)
        emp13 = hits[13] / n
        # lower categories: a ticket whose best line has 12 correct holds at
        # least one 12/13 line (approximation: exactly one). Same for 11, 10.
        ret_low = sum(per_winner[k] * hits[k] / n for k in per_winner)
        ev = pm * prize_13 + ret_low
        print(f"\n{nd} doubles, {nt} triples: {lines:5d} lines, stake {stake:>10,} TZS")
        print(f"   model P(13/13) {pm:.2e} (1 in {1/pm:,.0f}); empirical 13/13 {hits[13]}/{n}")
        print(f"   best-line hits distribution: {dict(sorted(hits.items()))}")
        print(f"   expected return {ev:>12,.0f} TZS  ({ev/stake:.2f}x stake); "
              f"of which jackpot {pm*prize_13:,.0f}, lower tiers {ret_low:,.0f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
