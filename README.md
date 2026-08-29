# SportPesa Jackpot Predictor

Backend-only bot that predicts the **SportPesa Tanzania Supa Jackpot 17**
(and Midweek 13 when offered) with Forebet-style 1X2 picks, delivered via
Telegram + email **2 days before the first kickoff at 20:00 EAT**.

It reuses **EdgeBot's already-trained ensemble** (Dixon-Coles, Elo, Pi-rating,
NegBinom, bivariate Poisson, LightGBM + CatBoost boosters, isotonic
calibration) — nothing is retrained here.

> **Paper/Research mode.** This is a prediction delivery system, not a betting
> bot. No staking, no bet placement, no bankroll logic.

## How it works

```
scraper  ──►  resolver  ──►  predictor  ──►  formatter  ──►  delivery
(JSON API)   (league +      (EdgeBot        (Telegram txt,   (Telegram,
              team names)    ensemble or     HTML, CSV,       email)
                             odds fallback)  JSON archive)
```

1. **Scraper** — the jackpot page embeds a React widget whose public JSON API
   we call directly (`jackpot-offer-api.sportpesa.co.tz/api/jackpots/active`).
   No HTML parsing, no Selenium. Responses are cached (6 h) and raw payloads
   archived.
2. **Resolver** — maps `tournamentName + countryName` to EdgeBot league codes
   (`E0`, `BR1`, ...), then resolves team spellings through three tiers:
   `resolver/mappings.json` → EdgeBot's own alias map → rapidfuzz (≥ 85).
3. **Predictor** — resolved fixtures go through EdgeBot's fitted ensemble
   (tier-1 leagues) or the Tier2Stack (Argentina, MLS, Romania, Russia, ...).
   Fixtures outside coverage — common in the European off-season when jackpots
   use Belarus / Uruguay / Brazil Serie C fixtures — fall back to **de-vigged
   SportPesa odds**, clearly labelled `market odds` in the output.

   A tier-2 league whose model fails EdgeBot's quality gate (its walk-forward
   Brier does not beat the bookmaker baseline by a significant margin) is
   *also* priced from the odds, labelled `market odds (model gated)`. The model
   there has been measured as no better than the price it would replace, so it
   never displaces it; the gated model is used only when no odds are published.
   As of the 2026-08-29 gate run this covers every tier-2 league.
4. **Insights** — two best-effort second opinions enrich every fixture:
   - **Forebet** (`scripts/getrs.php` JSON): their 1X2 percentages + predicted
     score, matched country-scoped with both team names above a fuzzy
     threshold. Configurable `blend_weight` (default 0.25) folds Forebet's
     probabilities into ours — agreement sharpens a pick, disagreement pushes
     it toward UNCERTAIN. Consensus is called out per match.
     **Currently dead:** the endpoint has returned HTTP 403 on every run since
     2026-07-17, so the blend has not applied since. Runs are unaffected apart
     from the missing second opinion.
   - **SofaScore** (via EdgeBot's `SofaScoreClient`): last-5 form, standings
     position, fan-vote split and head-to-head, shown per match and in the CSV.
   Both sources degrade gracefully — a block or mismatch never stops a run.
5. **Confidence tiers** — HIGH / MEDIUM / LOW / UNCERTAIN from top-pick
   probability + margin over the second pick. Model predictions EdgeBot flags
   as `low_confidence` (thin team history) are capped at LOW.
6. **Delivery** — Telegram message (auto-chunked) + CSV document, HTML email
   with CSV attachment. Every run archives `.txt/.html/.csv/.json` under
   `data/jackpots/`.

## Quick start (Windows dev box)

```bat
:: uses EdgeBot's virtualenv — it already has every dependency
copy .env.example .env   && notepad .env
run_jackpot.bat --dry-run
```

## Deploy (Linux VPS, alongside EdgeBot)

```bash
git clone https://github.com/mykiie85/SOCER_JACKPOT_PREDICTOR_MODEL.git ~/jackpot_predictor
cd ~/jackpot_predictor && cp .env.example .env && nano .env
./deploy/install.sh
```

One systemd timer fires **daily at 20:00 EAT**; `main.py` itself checks
whether today is `days_before` (default 2) days ahead of the jackpot's first
kickoff and exits quietly otherwise. A sent-registry prevents duplicate
deliveries, and a missed evening (VPS down) is caught by the next firing.

## Configuration

- `.env` — paths + credentials (`EDGEBOT_PATH`, `TELEGRAM_BOT_TOKEN`,
  `TELEGRAM_CHAT_ID`, `EMAIL_SENDER`, `EMAIL_PASSWORD`, `EMAIL_RECEIVER`).
  Same names EdgeBot uses, so one `.env` can serve both.
- `jackpot_predictor/config/jackpot.yaml` — tier thresholds, fuzzy threshold,
  odds-fallback toggle, model/odds blend weight, schedule, delivery toggles.

## CLI

```
python main.py              # scheduled mode (gated send)
python main.py --dry-run    # predict + print, send nothing
python main.py --force      # send now regardless of schedule
python main.py --no-cache   # refetch the fixture feed
```

## Results grading

After the matches play, `results_main.py` grades the archived predictions
against SportPesa's own settled 1X2 outcomes and delivers a recap
(Telegram + email): how many of the 17 picks were correct, per-match final
scores, and a hit rate by confidence tier.

```
python results_main.py             # scheduled: grade + send once per jackpot
python results_main.py --dry-run   # grade + print, send nothing
python results_main.py --force     # re-send even if already reported
```

Results come from the same jackpot feed the scraper uses — the live events
carry a per-match `score` once finished, joined to the archive on the stable
`event_id`. Because `/jackpots/active` only serves the *current* jackpot, the
raw results are snapshotted to `data/jackpots/<type>_<human>_results.json` so a
late grade still works after the feed rolls over. Matches SportPesa settles
without a score (postponed/void, exposed via `publicDraw`) sit outside the
correct/played count. A `deploy/jackpot-results.timer` fires at 08:00 and 20:30
EAT; the run is idempotent via `data/cache/results_sent.json`.

## Tests

```bash
python -m pytest tests/ -q
```

## Maintaining team mappings

When a run reports `unresolved team 'X'` (Telegram admin alert + log), add the
SportPesa spelling → football-data spelling to
`jackpot_predictor/resolver/mappings.json`. The resolver only fuzzy-matches
within the detected league, so mappings are rarely needed outside promoted or
renamed clubs.
