"""Render a graded jackpot into a Telegram message and an HTML email body."""
from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from jackpot_predictor.config.settings import jackpot_config
from jackpot_predictor.predictor.confidence_tier import PICK_DISPLAY
from jackpot_predictor.predictor.formatter import JACKPOT_TITLES

_MARK = {True: "✅", False: "❌", None: "➖"}


def _title(jackpot: dict) -> str:
    return JACKPOT_TITLES.get(jackpot["jackpot_type"],
                              f"Jackpot {jackpot['number_of_events']}")


def _eat_now() -> str:
    tz = ZoneInfo(jackpot_config()["schedule"]["timezone"])
    return datetime.now(timezone.utc).astimezone(tz).strftime(
        "%a %d %b %Y, %H:%M EAT")


def _score_str(row: dict) -> str:
    sc = row.get("actual_score")
    if sc:
        return f"{sc[0]}–{sc[1]}"
    if row["result_status"] == "void":
        return "void"
    return "—"


def _pick_vs_actual(row: dict) -> str:
    pick = PICK_DISPLAY.get(row.get("primary_pick"), "?")
    actual = PICK_DISPLAY.get(row.get("actual_outcome"), "?")
    if row["result_status"] == "played":
        return f"pick {pick} · result {actual}"
    if row["result_status"] == "void":
        return f"pick {pick} · declared {actual} (void)"
    return f"pick {pick} · not settled"


def _tier_breakdown(rows: list[dict]) -> str:
    order = ["HIGH", "MEDIUM", "LOW", "UNCERTAIN"]
    bits = []
    for t in order:
        played = [r for r in rows
                  if r.get("confidence_tier") == t and r["result_status"] == "played"]
        if played:
            hit = sum(1 for r in played if r["is_correct"])
            bits.append(f"{t[:3]} {hit}/{len(played)}")
    return " · ".join(bits) if bits else "—"


def format_telegram(graded: dict, jackpot: dict, source: str) -> str:
    rows = graded["rows"]
    head = [
        f"🏁 SportPesa {_title(jackpot)} #{jackpot['human_id']} — Results",
        f"Correct: {graded['correct']}/{graded['played']} played"
        + (f"  ·  {graded['void']} void" if graded['void'] else "")
        + (f"  ·  {graded['pending']} pending" if graded['pending'] else ""),
        f"Graded {_eat_now()}",
        "─" * 30,
    ]
    body = []
    for r in rows:
        mark = _MARK[r["is_correct"]] if r["result_status"] != "pending" else "⏳"
        home = (r["home_team_raw"] or "")[:15]
        away = (r["away_team_raw"] or "")[:15]
        body.append(
            f"{mark} {r['match_number']:>2} {home} {_score_str(r)} {away}")
        body.append(f"      {_pick_vs_actual(r)}")
    foot = [
        "─" * 30,
        f"By confidence (correct/played): {_tier_breakdown(rows)}",
    ]
    if graded["pending"]:
        foot.append("Some matches are not settled yet — they roll into the next "
                    "results check.")
    foot.append("⚠️ Research only — model predictions, not guarantees.")
    return "\n".join(head + body + foot)


def format_html(graded: dict, jackpot: dict, source: str) -> str:
    trs = []
    for r in graded["rows"]:
        mark = _MARK[r["is_correct"]] if r["result_status"] != "pending" else "⏳"
        pick = PICK_DISPLAY.get(r.get("primary_pick"), "?")
        actual = PICK_DISPLAY.get(r.get("actual_outcome"), "?")
        color = ("#1a7f37" if r["is_correct"] else "#cf222e"
                 if r["is_correct"] is False else "#6e7781")
        trs.append(
            f"<tr><td style='text-align:center'>{mark}</td>"
            f"<td>{r['match_number']}</td>"
            f"<td><b>{r['home_team_raw']}</b> vs <b>{r['away_team_raw']}</b><br>"
            f"<small>{r.get('tournament','')} ({r.get('country','')})</small></td>"
            f"<td style='text-align:center'>{_score_str(r)}</td>"
            f"<td style='text-align:center'><b>{pick}</b></td>"
            f"<td style='text-align:center;color:{color};font-weight:bold'>{actual}</td>"
            f"<td>{r.get('confidence_tier','')}</td></tr>")
    return f"""<html><body style="font-family:Segoe UI,Arial,sans-serif;color:#1f2328">
<h2>🏁 SportPesa {_title(jackpot)} #{jackpot['human_id']} — Results</h2>
<p><b>{graded['correct']} / {graded['played']}</b> correct on played matches
&nbsp;|&nbsp; {graded['void']} void &nbsp;|&nbsp; {graded['pending']} pending
&nbsp;|&nbsp; graded {_eat_now()}</p>
<table border="1" cellpadding="6" cellspacing="0"
       style="border-collapse:collapse;border-color:#d0d7de">
<tr style="background:#f6f8fa"><th></th><th>#</th><th>Match</th><th>Score</th>
<th>Our pick</th><th>Result</th><th>Confidence</th></tr>
{''.join(trs)}
</table>
<p><b>By confidence (correct/played):</b> {_tier_breakdown(graded['rows'])}</p>
<p style="color:#6e7781"><small>⚠️ Research only. Void matches (no score
published) sit outside the correct count. Results are SportPesa's own settled
outcomes.</small></p>
</body></html>"""
