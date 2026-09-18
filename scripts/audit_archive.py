"""Audit SportPesa's published jackpot archive (data/jackpots/archive/).

What it answers, on every archived jackpot since 2022:
  1. Outcome base rates (H/D/A) overall, by year, by event slot, by weekday.
     A slot effect would mean SportPesa orders the hard matches; a year
     drift would mean the fixture selection policy changed.
  2. How many draws / aways a slate carries (the "13 favourites" arithmetic).
  3. Pool bias: winners per tier-category versus the outcome mix. Under a
     public that picks in proportion to true frequencies, each extra draw in
     the result changes the winner count by draw_rate/home_rate. The fitted
     ratio is the public's real draw/home pick ratio — the number a
     pari-mutuel contrarian ticket needs.
  4. Payout facts per tier: how often each tier pays, per-winner amounts.
  5. Earliest-kickoff hour per jackpot (for scheduling the final send).
"""
from __future__ import annotations

import math
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jackpot_predictor.scraper.archive import load_archive  # noqa: E402

PICK = {"Home": "H", "Draw": "D", "Away": "A"}


def _dt(iso):
    return datetime.fromisoformat(iso.replace("Z", "+00:00"))


def main() -> int:
    jps = load_archive()
    jps = [j for j in jps if j.get("events")]
    print(f"archived jackpots with details: {len(jps)}")
    sizes = Counter(len(j["events"]) for j in jps)
    print("events per jackpot:", dict(sizes))
    fin = sorted(j["finished"] for j in jps)
    print("finished range:", fin[0][:10], "->", fin[-1][:10])

    rows = []  # (human_id, year, slot, weekday, outcome, n_events)
    for j in jps:
        n = len(j["events"])
        for ev in j["events"]:
            o = PICK.get(ev.get("resultPick"))
            if not o:
                continue
            k = _dt(ev["kickoffTime"])
            rows.append((j["jackpotHumanId"], k.year, ev["eventNumber"],
                         k.strftime("%a"), o, n))
    print(f"graded fixtures: {len(rows)}")

    def rates(sub):
        c = Counter(r[4] for r in sub)
        t = sum(c.values()) or 1
        return t, c["H"] / t, c["D"] / t, c["A"] / t

    def show(label, sub):
        t, h, d, a = rates(sub)
        print(f"  {label:<14} n={t:5d}  H {h:.3f}  D {d:.3f}  A {a:.3f}")

    print("\n1. outcome base rates")
    show("all", rows)
    for n in sorted(sizes):
        show(f"{n}-event", [r for r in rows if r[5] == n])
    for y in sorted({r[1] for r in rows}):
        show(str(y), [r for r in rows if r[1] == y])
    print("  by slot (17-event jackpots):")
    r17 = [r for r in rows if r[5] == 17]
    slot_counts = defaultdict(Counter)
    for r in r17:
        slot_counts[r[2]][r[4]] += 1
    for s in sorted(slot_counts):
        c = slot_counts[s]; t = sum(c.values())
        print(f"    slot {s:2d} n={t:3d}  H {c['H']/t:.2f}  D {c['D']/t:.2f}  A {c['A']/t:.2f}")
    # chi-square: slot x outcome independence
    slots = sorted(slot_counts)
    obs = np.array([[slot_counts[s][o] for o in "HDA"] for s in slots], float)
    exp = obs.sum(1, keepdims=True) * obs.sum(0, keepdims=True) / obs.sum()
    chi2 = float(((obs - exp) ** 2 / exp).sum())
    dof = (len(slots) - 1) * 2
    print(f"  slot x outcome chi2={chi2:.1f} dof={dof} (mean under null {dof}, "
          f"~95% cut {dof + 2 * math.sqrt(2 * dof):.0f})")
    print("  by weekday:")
    for wd in ("Fri", "Sat", "Sun", "Mon", "Tue", "Wed", "Thu"):
        sub = [r for r in rows if r[3] == wd]
        if sub:
            show(wd, sub)

    print("\n2. draws / aways per 17-event slate")
    per = defaultdict(Counter)
    for r in r17:
        per[r[0]][r[4]] += 1
    nd = [c["D"] for c in per.values()]
    na = [c["A"] for c in per.values()]
    nh = [c["H"] for c in per.values()]
    print(f"  slates={len(per)}  draws mean {np.mean(nd):.2f} sd {np.std(nd):.2f} "
          f"min {min(nd)} max {max(nd)}; aways mean {np.mean(na):.2f}; homes mean {np.mean(nh):.2f}")
    print("  draws histogram:", dict(sorted(Counter(nd).items())))
    print("  P(<=2 draws) =", f"{np.mean([d <= 2 for d in nd]):.3f}",
          " P(<=3 draws) =", f"{np.mean([d <= 3 for d in nd]):.3f}")

    print("\n3. pool bias: winners vs outcome mix (17-event jackpots)")
    # For each tier-category with enough non-zero weeks, fit
    #   log(winners / poolTZS) = a + b*n_draw + c*n_away   (homes are the base)
    # exp(b) = public's draw:home pick ratio, exp(c) = away:home.
    tiers = defaultdict(list)  # (jackpotType, category) -> (nd, na, winners, prize)
    for j in jps:
        if len(j["events"]) != 17:
            continue
        c = Counter(PICK.get(e.get("resultPick")) for e in j["events"])
        for dist in j.get("winningDistribution", []):
            for w in dist.get("winningAmounts", []):
                tiers[(dist["jackpotType"], w["category"])].append(
                    (c["D"], c["A"], w["countWinnings"], w["prize"]))
    cat_label = {"Jackpot": "0 miss", "Category1": "1 miss", "Category2": "2 miss",
                 "Category3": "3 miss", "Category4": "4 miss", "Category5": "5 miss"}
    for key in sorted(tiers, key=lambda k: (-int(k[0].split("/")[0]), k[1])):
        pts = tiers[key]
        nz = [p for p in pts if p[2] > 0]
        share_wins = len(nz) / len(pts)
        line = (f"  {key[0]} {cat_label.get(key[1], key[1]):<7} weeks={len(pts):3d} "
                f"paid-weeks={share_wins:.2f} median winners={np.median([p[2] for p in pts]):.0f}")
        if len(nz) >= 25:
            X = np.array([[1, p[0], p[1]] for p in nz], float)
            y = np.array([math.log(p[2]) for p in nz])
            beta, *_ = np.linalg.lstsq(X, y, rcond=None)
            resid = y - X @ beta
            se = math.sqrt((resid ** 2).sum() / max(1, len(y) - 3))
            cov = se ** 2 * np.linalg.inv(X.T @ X)
            line += (f" | per extra draw x{math.exp(beta[1]):.2f} "
                     f"(se {math.sqrt(cov[1,1]):.2f} log), per extra away x{math.exp(beta[2]):.2f}"
                     f" (se {math.sqrt(cov[2,2]):.2f} log)")
        print(line)
    t, h, d, a = rates(r17)
    print(f"  reference: true draw:home ratio {d/h:.2f}, away:home {a/h:.2f}. "
          "A fitted ratio well below the true one means the public under-picks that outcome.")

    print("\n4. payouts per tier (17-event jackpots, TZS)")
    for key in sorted(tiers, key=lambda k: (-int(k[0].split("/")[0]), k[1])):
        pts = tiers[key]
        paid = [(p[3] / p[2]) for p in pts if p[2] > 0 and p[3] > 0]
        if paid:
            print(f"  {key[0]} {cat_label.get(key[1], key[1]):<7} per-winner median "
                  f"{np.median(paid):>14,.0f}  p10 {np.percentile(paid,10):>14,.0f}  "
                  f"p90 {np.percentile(paid,90):>14,.0f}  weeks paid {len(paid)}")
    jp = [(j["jackpotHumanId"], j["finished"][:10],
           next((p["prize"] for p in j.get("prizes", []) if p["jackpotType"] == "17/17"), None))
          for j in jps if len(j["events"]) == 17]
    print("  17/17 top prize over time:", [(h, f"{p/1e9:.2f}B") for h, _, p in jp[::20] if p])
    hits = []
    for j in jps:
        for dist in j.get("winningDistribution", []):
            for w in dist.get("winningAmounts", []):
                if w["category"] == "Jackpot" and w["countWinnings"] > 0:
                    hits.append((j["jackpotHumanId"], dist["jackpotType"], w["countWinnings"], w["winningAmount"]))
    print(f"  full-tier hits ever: {len(hits)}")
    for hh in hits:
        print("   ", hh)

    print("\n5. earliest kickoff per jackpot (UTC hour)")
    first = Counter()
    for j in jps:
        k = min(_dt(e["kickoffTime"]) for e in j["events"])
        first[(k.strftime("%a"), k.hour)] += 1
    for (wd, h), n in sorted(first.items(), key=lambda x: -x[1])[:12]:
        print(f"    {wd} {h:02d}:00 UTC  x{n}")
    print("   earliest hour seen:", min(h for (_, h) in first))
    return 0


if __name__ == "__main__":
    sys.exit(main())
