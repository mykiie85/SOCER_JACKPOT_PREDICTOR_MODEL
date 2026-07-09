"""Forebet-style output: Telegram text, HTML email body, CSV, JSON archive."""
from __future__ import annotations

import csv
import json
import logging
from collections import Counter
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jackpot_predictor.config.settings import JACKPOTS_DIR, jackpot_config
from jackpot_predictor.predictor.confidence_tier import PICK_DISPLAY, PICK_LABEL

log = logging.getLogger(__name__)

_TIER_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🟠",
               "UNCERTAIN": "🔴", "UNPRICED": "⚪"}
_TIER_COLOR = {"HIGH": "#1a7f37", "MEDIUM": "#b08800", "LOW": "#bc4c00",
               "UNCERTAIN": "#cf222e", "UNPRICED": "#6e7781"}
_SOURCE_LABEL = {"model": "EdgeBot model", "blend": "model + market",
                 "odds": "market odds", "model+forebet": "model + Forebet",
                 "blend+forebet": "model + market + Forebet",
                 "odds+forebet": "market odds + Forebet", None: "—"}


def _insight_lines(p: dict) -> list[str]:
    """Compact per-match insight lines (Forebet + SofaScore), if available."""
    lines = []
    fb = p.get("forebet")
    if fb:
        pr = fb["probs"]
        agree = f" | {p['consensus']}" if p.get("consensus") else ""
        lines.append(
            f"  Forebet: {PICK_DISPLAY[fb['pick']]} "
            f"({pr['home']:.0%}/{pr['draw']:.0%}/{pr['away']:.0%}) "
            f"score {fb['predicted_score']}{agree}")
    ss = p.get("sofascore")
    if ss:
        bits = []
        if ss.get("form_home") or ss.get("form_away"):
            bits.append(f"Form {ss.get('form_home') or '?'} v "
                        f"{ss.get('form_away') or '?'}")
        if ss.get("position_home") and ss.get("position_away"):
            bits.append(f"Pos {ss['position_home']} v {ss['position_away']}")
        votes = ss.get("votes")
        if votes:
            bits.append(f"Votes {votes['home']:.0%}/{votes['draw']:.0%}/"
                        f"{votes['away']:.0%}")
        h2h = ss.get("h2h")
        if h2h:
            bits.append(f"H2H {h2h['home_wins']}-{h2h['draws']}-"
                        f"{h2h['away_wins']}")
        if bits:
            lines.append("  SofaScore: " + " | ".join(bits))
    return lines

JACKPOT_TITLES = {"supa17": "Supa Jackpot 17", "midweek13": "Midweek Jackpot 13"}


def _eat(dt_iso: str | None) -> str:
    if not dt_iso:
        return "TBC"
    tz = ZoneInfo(jackpot_config()["schedule"]["timezone"])
    dt = datetime.fromisoformat(dt_iso.replace("Z", "+00:00")).astimezone(tz)
    return dt.strftime("%a %d %b %Y, %H:%M EAT")


def _title(jackpot: dict) -> str:
    return JACKPOT_TITLES.get(jackpot["jackpot_type"],
                              f"Jackpot {jackpot['number_of_events']}")


def _pick_line(p: dict) -> str:
    pick = p["primary_pick"]
    return (f"{PICK_DISPLAY[pick]} ({PICK_LABEL[pick]}) — "
            f"{p['primary_prob']:.1%}")


def _summary(predictions: list[dict]) -> tuple[Counter, Counter, str]:
    tiers = Counter(p["confidence_tier"] for p in predictions)
    picks = Counter(p["primary_pick"] for p in predictions if p.get("primary_pick"))
    total = sum(picks.values()) or 1
    note = ""
    if picks.get("H", 0) / total > 0.6:
        note = (f"The slate leans home-heavy ({picks['H']}/{total} picks are '1'). "
                "Consider flipping 2-3 UNCERTAIN matches to X or 2 for variation.")
    return tiers, picks, note


def format_telegram(jackpot: dict, predictions: list[dict]) -> str:
    lines = [
        f"🎯 SportPesa {_title(jackpot)} — Predictions",
        f"Jackpot #{jackpot['human_id']} | first kickoff: "
        f"{_eat(jackpot['first_kickoff_utc'])}",
        f"Generated: {_eat(datetime.now(timezone.utc).isoformat())}",
        "",
    ]
    for p in predictions:
        head = (f"Match {p['match_number']}: {p['home_team_raw']} vs "
                f"{p['away_team_raw']}")
        league = f"{p['tournament']} ({p['country']})"
        lines.append(head)
        lines.append(f"  {league} | {_eat(p['kickoff_utc'])}")
        if p.get("primary_pick"):
            emoji = _TIER_EMOJI.get(p["confidence_tier"], "")
            lines.append(f"  ➤ {_pick_line(p)} {emoji} {p['confidence_tier']}")
            lines.append(
                f"  Alt: {PICK_DISPLAY[p['secondary_pick']]} — "
                f"{p['secondary_prob']:.1%} | margin {p['margin']:.1%} | "
                f"{_SOURCE_LABEL.get(p['source'], p['source'])}")
        else:
            lines.append("  ⚪ UNPRICED — no pick (see notes)")
        lines.extend(_insight_lines(p))
        lines.append("")

    tiers, picks, note = _summary(predictions)
    lines += [
        "─" * 28,
        "SUMMARY",
        f"🟢 High: {tiers.get('HIGH', 0)}   🟡 Medium: {tiers.get('MEDIUM', 0)}   "
        f"🟠 Low: {tiers.get('LOW', 0)}   🔴 Uncertain: {tiers.get('UNCERTAIN', 0)}",
        f"Picks: 1×{picks.get('H', 0)}  X×{picks.get('D', 0)}  2×{picks.get('A', 0)}",
    ]
    if note:
        lines += ["", f"💡 {note}"]
    n_model = sum(1 for p in predictions
                  if (p.get("source") or "").startswith(("model", "blend")))
    n_odds = sum(1 for p in predictions
                 if (p.get("source") or "").startswith("odds"))
    n_fb = sum(1 for p in predictions if p.get("forebet"))
    n_ss = sum(1 for p in predictions if p.get("sofascore"))
    n_agree = sum(1 for p in predictions
                  if p.get("consensus") == "Forebet agrees")
    lines += [
        "",
        f"Sources: {n_model} EdgeBot model / {n_odds} market-odds fallback | "
        f"Forebet matched {n_fb} (agrees on {n_agree}) | SofaScore on {n_ss}.",
        "⚠️ Research only — model predictions, not guarantees. A "
        f"{jackpot['number_of_events']}-match jackpot is statistically very hard.",
    ]
    return "\n".join(lines)


def format_html(jackpot: dict, predictions: list[dict]) -> str:
    tiers, picks, note = _summary(predictions)
    rows = []
    for p in predictions:
        if p.get("primary_pick"):
            pick = PICK_DISPLAY[p["primary_pick"]]
            probs = (f"{p['prob_home']:.0%} / {p['prob_draw']:.0%} / "
                     f"{p['prob_away']:.0%}")
            alt = PICK_DISPLAY[p["secondary_pick"]]
            tier = p["confidence_tier"]
        else:
            pick, probs, alt, tier = "—", "—", "—", "UNPRICED"
        color = _TIER_COLOR.get(tier, "#6e7781")
        insight = "<br>".join(l.strip() for l in _insight_lines(p)) or "—"
        rows.append(
            f"<tr><td>{p['match_number']}</td>"
            f"<td><b>{p['home_team_raw']}</b> vs <b>{p['away_team_raw']}</b><br>"
            f"<small>{p['tournament']} ({p['country']}) — {_eat(p['kickoff_utc'])}"
            f"</small></td>"
            f"<td style='text-align:center;font-size:1.2em'><b>{pick}</b></td>"
            f"<td style='text-align:center'>{probs}</td>"
            f"<td style='text-align:center'>{alt}</td>"
            f"<td style='color:{color};font-weight:bold'>{tier}</td>"
            f"<td><small>{_SOURCE_LABEL.get(p.get('source'), p.get('source'))}"
            f"</small></td>"
            f"<td><small>{insight}</small></td></tr>")

    return f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328">
<h2>🎯 SportPesa {_title(jackpot)} — Predictions</h2>
<p>Jackpot #{jackpot['human_id']} &nbsp;|&nbsp; first kickoff:
{_eat(jackpot['first_kickoff_utc'])} &nbsp;|&nbsp; generated
{_eat(datetime.now(timezone.utc).isoformat())}</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;border-color:#d0d7de">
<tr style="background:#f6f8fa">
<th>#</th><th>Match</th><th>Pick</th><th>1 / X / 2</th><th>Alt</th>
<th>Confidence</th><th>Source</th><th>Insight</th></tr>
{''.join(rows)}
</table>
<p><b>Summary:</b> 🟢 {tiers.get('HIGH', 0)} high · 🟡 {tiers.get('MEDIUM', 0)} medium ·
🟠 {tiers.get('LOW', 0)} low · 🔴 {tiers.get('UNCERTAIN', 0)} uncertain —
picks 1×{picks.get('H', 0)}, X×{picks.get('D', 0)}, 2×{picks.get('A', 0)}</p>
{f'<p>💡 {note}</p>' if note else ''}
<p style="color:#6e7781"><small>⚠️ Research only. Model predictions are
probabilities, not guarantees. "Market odds" rows are de-vigged SportPesa
prices used where the model has no training coverage.</small></p>
</body></html>"""


def write_csv(jackpot: dict, predictions: list[dict], path) -> None:
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["Match", "Home Team", "Away Team", "Tournament", "Country",
                    "League Code", "Kickoff UTC", "1 Prob", "X Prob", "2 Prob",
                    "Pick", "Alt", "Margin", "Confidence", "Source",
                    "Odds 1", "Odds X", "Odds 2",
                    "Forebet Pick", "Forebet 1", "Forebet X", "Forebet 2",
                    "Forebet Score", "Consensus", "Form Home", "Form Away",
                    "Pos Home", "Pos Away", "Votes 1", "Votes X", "Votes 2",
                    "H2H (W-D-L)"])
        for p in predictions:
            has = p.get("primary_pick") is not None
            fb = p.get("forebet") or {}
            fbp = fb.get("probs") or {}
            ss = p.get("sofascore") or {}
            votes = ss.get("votes") or {}
            h2h = ss.get("h2h")
            w.writerow([
                p["match_number"], p["home_team_raw"], p["away_team_raw"],
                p["tournament"], p["country"], p.get("league_code") or "",
                p["kickoff_utc"],
                f"{p['prob_home']:.4f}" if has else "",
                f"{p['prob_draw']:.4f}" if has else "",
                f"{p['prob_away']:.4f}" if has else "",
                PICK_DISPLAY.get(p.get("primary_pick"), ""),
                PICK_DISPLAY.get(p.get("secondary_pick"), ""),
                f"{p['margin']:.4f}" if has else "",
                p.get("confidence_tier", ""), p.get("source") or "",
                p.get("odds_home"), p.get("odds_draw"), p.get("odds_away"),
                PICK_DISPLAY.get(fb.get("pick"), ""),
                f"{fbp['home']:.2f}" if fbp else "",
                f"{fbp['draw']:.2f}" if fbp else "",
                f"{fbp['away']:.2f}" if fbp else "",
                fb.get("predicted_score", ""), p.get("consensus") or "",
                ss.get("form_home") or "", ss.get("form_away") or "",
                ss.get("position_home") or "", ss.get("position_away") or "",
                f"{votes['home']:.2f}" if votes else "",
                f"{votes['draw']:.2f}" if votes else "",
                f"{votes['away']:.2f}" if votes else "",
                (f"{h2h['home_wins']}-{h2h['draws']}-{h2h['away_wins']}"
                 if h2h else ""),
            ])


def save_outputs(jackpot: dict, predictions: list[dict]) -> dict:
    """Persist telegram/html/csv/json under data/jackpots/ and return paths+text."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    base = JACKPOTS_DIR / f"{jackpot['jackpot_type']}_{jackpot['human_id']}_{stamp}"

    telegram_text = format_telegram(jackpot, predictions)
    html_body = format_html(jackpot, predictions)

    (base.with_suffix(".txt")).write_text(telegram_text, encoding="utf-8")
    (base.with_suffix(".html")).write_text(html_body, encoding="utf-8")
    csv_path = base.with_suffix(".csv")
    write_csv(jackpot, predictions, csv_path)
    (base.with_suffix(".json")).write_text(
        json.dumps({"jackpot": jackpot, "predictions": predictions},
                   indent=2, default=str), encoding="utf-8")
    log.info("outputs archived at %s.*", base)
    return {"telegram_text": telegram_text, "html_body": html_body,
            "csv_path": str(csv_path), "base": str(base)}
