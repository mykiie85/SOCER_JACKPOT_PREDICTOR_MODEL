#!/usr/bin/env python3
"""Walk-forward backtest of the 13/13 coupon rules (stdlib only).

    python3 scripts/backtest_coupon13.py \
        --matches "<analysis folder>/jackpot_matches_with_odds.csv" \
        --prizes  "<analysis folder>/jackpot_prizes.csv"

Four strategies are scored on the same rounds, each built by
jackpot_predictor.predictor.coupon13.build_coupon (the code the bot sends):

  (i)   all favourites, no doubles         1 line
  (ii)  (i) + team-form deviations         1 line
  (iii) (i) + odds-based doubles           up to 8 lines
  (iv)  doubles + team-form deviations     up to 8 lines

Walk-forward: a round only sees its own closing odds and the results of
earlier rounds. Team form is each team's record in EARLIER jackpot rounds.

Money: every line costs 1,000 TZS. A line with k correct is paid from that
round's real 13/13-game pool for tier k, shared with the real winners:
lines_in_tier * pool / (real_winners + lines_in_tier).
"""
from __future__ import annotations

import argparse
import csv
import math
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from jackpot_predictor.predictor.coupon13 import (PAYING_FROM, at_least,  # noqa: E402
                                                  build_coupon, score_coupon)

LINE_TZS = 1000
CODE = {"1": "H", "X": "D", "2": "A"}
MIN_APPS = 15            # appearances before a team's record may be used
Z_MIN = 2.0              # how far from expectation counts as "strongly"
LAST_N = 6               # the recent window that must agree with the history
PRIOR = {"H": 0.36, "D": 0.31, "A": 0.33}   # base rates until history exists
PRIOR_WEIGHT = 100


def load_rounds(matches_csv: str) -> list[dict]:
    by = defaultdict(list)
    with open(matches_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            res = CODE.get(r["outcome_code"])
            if res is None:
                continue
            probs = None
            if r.get("p_1"):
                probs = {"H": float(r["p_1"]), "D": float(r["p_x"]),
                         "A": float(r["p_2"])}
            by[int(r["jackpot_id"])].append({
                "match_number": int(r["match_no"]), "home": r["home_team"],
                "away": r["away_team"], "probs": probs, "result": res})
    return [{"id": j, "matches": sorted(ms, key=lambda m: m["match_number"])}
            for j, ms in sorted(by.items())]


def load_prizes(prizes_csv: str) -> dict:
    """{round: {correct: (pool, real_winners)}} for the 13/13 game."""
    out = defaultdict(dict)
    with open(prizes_csv, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["jackpot_type"] != "13/13" or r["paid"] != "True":
                continue
            out[int(r["jackpot_id"])][int(r["correct_picks"])] = (
                float(r["tier_pool"] or 0), int(r["winners"] or 0))
    return out


class Form:
    """Each team's record in earlier jackpot rounds, against expectation.

    Expectation per past match is the margin-free odds where the match was
    priced (so a strong team winning is not a "pattern"), otherwise the base
    rate seen in earlier rounds.
    """

    def __init__(self):
        self.apps = defaultdict(list)      # team -> [(outcome W/D/L, {W,D,L})]
        self.weight = defaultdict(lambda: 1.0)   # decay after a failed flag
        self.base = dict.fromkeys("HDA", 0)
        self.n = 0

    def base_rates(self) -> dict:
        tot = self.n + PRIOR_WEIGHT
        return {o: (self.base[o] + PRIOR[o] * PRIOR_WEIGHT) / tot for o in "HDA"}

    def flag(self, m: dict) -> dict | None:
        """Strongest form signal for this match, or None."""
        best = None
        for team, role in ((m["home"], "H"), (m["away"], "A")):
            hist = self.apps[team]
            if len(hist) < MIN_APPS:
                continue
            opp = "A" if role == "H" else "H"
            for oc, pick, label in (("D", "D", "draws"), ("W", role, "wins"),
                                    ("L", opp, "losses")):
                got = sum(1 for o, _ in hist if o == oc)
                exp = sum(e[oc] for _, e in hist)
                var = sum(e[oc] * (1 - e[oc]) for _, e in hist)
                z = (got - exp) / math.sqrt(var) if var > 0 else 0.0
                recent = hist[-LAST_N:]
                r_got = sum(1 for o, _ in recent if o == oc)
                r_exp = sum(e[oc] for _, e in recent)
                strength = z * self.weight[team]
                if strength < Z_MIN or r_got - r_exp < 1.0:
                    continue
                if best is None or strength > best["strength"]:
                    best = {"pick": pick, "strength": strength, "team": team,
                            "why": (f"{team}: {got} {label} in {len(hist)} jackpot "
                                    f"matches (expected {exp:.1f}), "
                                    f"{r_got} in the last {len(recent)}")}
        return best

    def update(self, m: dict) -> None:
        base = self.base_rates()
        p = m["probs"] or base
        res = m["result"]
        self.apps[m["home"]].append((
            {"H": "W", "D": "D", "A": "L"}[res],
            {"W": p["H"], "D": p["D"], "L": p["A"]}))
        self.apps[m["away"]].append((
            {"A": "W", "D": "D", "H": "L"}[res],
            {"W": p["A"], "D": p["D"], "L": p["H"]}))
        self.base[res] += 1
        self.n += 1


def payout(lines_by_correct: dict, prizes: dict) -> float:
    total = 0.0
    for k, n_lines in lines_by_correct.items():
        pool, winners = prizes.get(k, (0.0, 0))
        if k >= PAYING_FROM and pool > 0 and n_lines:
            total += n_lines * pool / (winners + n_lines)
    return total


STRATEGIES = [
    ("(i) all favourites, no doubles", dict(doubles=0, form=False)),
    ("(ii) favourites + team form", dict(doubles=0, form=True)),
    ("(iii) favourites + 3 doubles", dict(doubles=3, form=False)),
    ("(iv) doubles + team form", dict(doubles=3, form=True)),
]


def run(rounds: list[dict], prizes: dict, min_priced: int = 0) -> dict:
    form = Form()
    stats = {name: defaultdict(float) for name, _ in STRATEGIES}
    form_log = {"played": 0, "won": 0, "fav_would_win": 0}
    for rnd in rounds:
        ms = rnd["matches"]
        results = {m["match_number"]: m["result"] for m in ms}
        counted = sum(1 for m in ms if m["probs"]) >= min_priced
        flags = {m["match_number"]: f for m in ms if (f := form.flag(m))}
        home_rate = form.base_rates()["H"]
        for name, cfg in STRATEGIES:
            c = build_coupon(ms, max_doubles=cfg["doubles"],
                             form_flags=flags if cfg["form"] else None,
                             unpriced_cover=home_rate)
            if name.startswith("(ii)"):
                for g in c["legs"]:
                    if g["reason"] != "form-based":
                        continue
                    hit = results[g["match_number"]] == g["picks"][0]
                    team = flags[g["match_number"]]["team"]
                    if counted:
                        form_log["played"] += 1
                        form_log["won"] += hit
                        form_log["fav_would_win"] += (
                            results[g["match_number"]] == g["base_pick"])
                    if not hit:
                        form.weight[team] *= 0.5      # decay rule
            if not counted:
                continue
            sc = score_coupon(c, results)
            s = stats[name]
            s["rounds"] += 1
            s["correct"] += sc["best"]
            s["lines"] += c["n_lines"]
            s["doubles"] += sum(1 for g in c["legs"] if g["double"])
            s["returned"] += payout(sc["lines_by_correct"], prizes.get(rnd["id"], {}))
            for k in (10, 11, 12, 13):
                s[f"hit{k}"] += sc["best"] >= k
                s[f"exp{k}"] += c["p_at_least"].get(k, 0.0)
        for m in ms:
            form.update(m)
    return {"stats": stats, "form_log": form_log}


def report(title: str, out: dict) -> None:
    print(f"\n## {title}")
    print("| Strategy | Rounds | Avg correct of 13 | 10+ | 11+ | 12+ | 13 | "
          "10+ expected from odds | Lines staked | Staked (TZS) | Returned (TZS) | "
          "Return per 1,000 |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for name, _ in STRATEGIES:
        s = out["stats"][name]
        n = int(s["rounds"])
        if not n:
            continue
        staked = s["lines"] * LINE_TZS
        print(f"| {name} | {n} | {s['correct'] / n:.2f} | "
              f"{int(s['hit10'])} ({s['hit10'] / n:.1%}) | {int(s['hit11'])} | "
              f"{int(s['hit12'])} | {int(s['hit13'])} | {s['exp10']:.1f} | "
              f"{int(s['lines']):,} | {staked:,.0f} | {s['returned']:,.0f} | "
              f"{1000 * s['returned'] / staked:,.0f} |")
    f = out["form_log"]
    if f["played"]:
        print(f"\nteam-form deviations played: {f['played']}; correct "
              f"{f['won']} ({f['won'] / f['played']:.1%}); the favourite it "
              f"replaced would have been correct {f['fav_would_win']} "
              f"({f['fav_would_win'] / f['played']:.1%})")
    else:
        print("\nteam-form deviations played: 0")
    a = out["stats"][STRATEGIES[0][0]]
    b = out["stats"][STRATEGIES[1][0]]
    verdict = "KEEP" if b["hit10"] > a["hit10"] else "DISABLE"
    print(f"Rule 2 check: 10+ rounds with form {int(b['hit10'])} vs without "
          f"{int(a['hit10'])} -> {verdict} team form")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--matches", required=True)
    ap.add_argument("--prizes", required=True)
    args = ap.parse_args()
    rounds = load_rounds(args.matches)
    prizes = load_prizes(args.prizes)
    print(f"rounds {len(rounds)} (#{rounds[0]['id']} to #{rounds[-1]['id']}), "
          f"matches {sum(len(r['matches']) for r in rounds)}, "
          f"priced {sum(1 for r in rounds for m in r['matches'] if m['probs'])}")
    report("All rounds", run(rounds, prizes))
    report("Rounds with 13 or more priced matches", run(rounds, prizes, 13))
    report("Rounds with all 17 matches priced", run(rounds, prizes, 17))
    return 0


if __name__ == "__main__":
    sys.exit(main())
