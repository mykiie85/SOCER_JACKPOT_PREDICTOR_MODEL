"""Join the SportPesa jackpot archive to closing 1X2 odds and re-test calibration.

The archive (data/jackpots/archive) has outcomes but no prices. EdgeBot's
football-data history carries Bet365 closing 1X2 prices for ~35 leagues, so
every archived fixture is matched to a history row on the same date (+-1 day)
by fuzzy team names. On the matched subset we ask, at n in the thousands:

  * favourite-longshot: does the odds-implied favourite win as often as the
    price says? (the bot's picks ARE the odds favourite)
  * draws: stated vs actual
  * reliability curve of the de-vigged favourite probability
  * home bias inside the jackpot selection
  * P(13 of 13) for the 13 most confident picks per slate
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
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
MIN_SCORE = 80


def _norm(name: str) -> str:
    return " ".join(core_tokens(name))


def main() -> int:
    b = EdgeBotBridge()
    hist = b.history
    t2 = b.tier2_history
    if t2 is not None:
        hist = pd.concat([hist, t2], ignore_index=True)
    hist = hist.dropna(subset=["B365H", "B365D", "B365A"]).copy()
    hist["Date"] = pd.to_datetime(hist["Date"]).dt.date
    hist["nh"] = hist["HomeTeam"].map(_norm)
    hist["na"] = hist["AwayTeam"].map(_norm)
    by_date = {d: g for d, g in hist.groupby("Date")}
    print(f"history rows with B365 prices: {len(hist)}, "
          f"{hist['Date'].min()} -> {hist['Date'].max()}")

    jps = [j for j in load_archive() if j.get("events")]
    rows, unmatched = [], 0
    for j in jps:
        for ev in j["events"]:
            o = PICK.get(ev.get("resultPick"))
            if not o:
                continue
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
            if best is None or best_s < MIN_SCORE or best_s - second < 5:
                unmatched += 1
                continue
            inv = [1 / best.B365H, 1 / best.B365D, 1 / best.B365A]
            tot = sum(inv)
            p = [v / tot for v in inv]
            rows.append({"jackpot": j["jackpotHumanId"], "date": k.date(),
                         "league": best.league, "outcome": o,
                         "pH": p[0], "pD": p[1], "pA": p[2],
                         "hist_outcome": best.FTR})
    df = pd.DataFrame(rows)
    agree = (df["outcome"] == df["hist_outcome"]).mean()
    print(f"matched {len(df)} of {len(df) + unmatched} archived fixtures "
          f"({len(df) / (len(df) + unmatched):.0%}); outcome agreement with "
          f"history {agree:.3f} (checks the join)")
    df = df[df["outcome"] == df["hist_outcome"]].copy()
    print("leagues:", dict(Counter(df["league"]).most_common(12)))

    # 1. favourite hit rate vs stated
    fav = df[["pH", "pD", "pA"]].idxmax(axis=1).str[1]
    df["fav"] = fav
    df["fav_p"] = df[["pH", "pD", "pA"]].max(axis=1)
    hit = (df["fav"] == df["outcome"]).mean()
    print(f"\n1. favourite: stated {df['fav_p'].mean():.3f}  hit {hit:.3f}  "
          f"n={len(df)}  se {math.sqrt(hit*(1-hit)/len(df)):.3f}")
    print(f"   draw: stated {df['pD'].mean():.3f}  actual {(df['outcome']=='D').mean():.3f}")
    print(f"   home: stated {df['pH'].mean():.3f}  actual {(df['outcome']=='H').mean():.3f}")
    print(f"   away: stated {df['pA'].mean():.3f}  actual {(df['outcome']=='A').mean():.3f}")
    # Brier of the de-vigged odds vs a base-rate-only forecast
    y = np.stack([(df["outcome"] == k).values for k in "HDA"], 1).astype(float)
    P = df[["pH", "pD", "pA"]].values
    base = y.mean(0)
    print(f"   Brier odds {((P - y) ** 2).sum(1).mean():.4f}   "
          f"base-rate only {((base - y) ** 2).sum(1).mean():.4f}")

    # 2. reliability curve of the favourite
    print("\n2. reliability of the favourite pick")
    bins = [0, 0.4, 0.45, 0.5, 0.55, 0.6, 0.7, 1.01]
    df["bin"] = pd.cut(df["fav_p"], bins, right=False)
    for bn, g in df.groupby("bin", observed=True):
        if len(g):
            print(f"   {str(bn):<12} n={len(g):4d} stated {g['fav_p'].mean():.3f} "
                  f"hit {(g['fav']==g['outcome']).mean():.3f}")

    # 3. per-slate: P(13/13) with the 13 most confident, and how often 13+ hit
    print("\n3. per slate (only slates with >= 13 matched fixtures)")
    p13, hits13, nsl = [], [], 0
    for jid, g in df.groupby("jackpot"):
        if len(g) < 13:
            continue
        nsl += 1
        top = g.sort_values("fav_p", ascending=False).head(13)
        p13.append(float(np.prod(top["fav_p"])))
        hits13.append(int((top["fav"] == top["outcome"]).sum()))
    if nsl:
        print(f"   slates {nsl}; mean P(13/13 of top-13) {np.mean(p13):.2e} "
              f"(1 in {1/np.mean(p13):,.0f}); hits of 13: "
              f"{dict(sorted(Counter(hits13).items()))}")
        print(f"   expected 13/13 count over these slates {sum(p13):.3f}; "
              f"observed {sum(1 for h in hits13 if h == 13)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
