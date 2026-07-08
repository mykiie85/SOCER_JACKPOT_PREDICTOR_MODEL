# SportPesa Jackpot Predictor Bot -- Super-Prompt

**Project:** `jackpot_predictor` (backend-only, no frontend)
**Reference System:** EdgeBot (located at `C:\Users\mykii\Downloads\Betting algorithim`)
**Resources:** `C:\Users\mykii\Downloads\BETTING ALGO RECOURCES`
**Target:** SportPesa TZ -- Supa Jackpot 17 & Midweek Jackpot
**Delivery:** Telegram + Email, 2 days before jackpot, 20:00 EAT
**Style:** Forebet-style 1X2 predictions (Home / Draw / Away)
**Status:** Paper/Research mode -- no real betting execution

---

## 1. Executive Summary

Build a **fully automated backend bot** that:

1. **Scrapes** SportPesa Tanzania jackpot fixtures (Supa Jackpot 17 + Midweek Jackpot) from `https://sportpesa.co.tz/en/supa-jackpot`
2. **Resolves** scraped team names to EdgeBot's canonical team names (critical -- 'Man Utd' vs 'Manchester United' must match)
3. **Predicts** 1X2 (Home / Draw / Away) using EdgeBot's **already-trained ensemble** -- no retraining, no new models
4. **Ranks** predictions by confidence (highest probability pick per match)
5. **Formats** a clean Forebet-style jackpot prediction table
6. **Delivers** via Telegram bot + Email at **20:00 EAT, 2 days before the jackpot closes**
7. **Runs** on the same VPS as EdgeBot (or standalone) via `systemd` timer

**Core principle:** This is a **prediction delivery system**, not a betting bot. It leverages EdgeBot's existing probabilistic engine to produce educated jackpot picks. No odds comparison, no Kelly staking, no bankroll logic. Pure 1X2 prediction + confidence ranking.

---

## 2. SportPesa Jackpot Structure (Verified from Research)

### 2.1 Supa Jackpot 17
- **Matches:** 17 pre-selected football matches
- **Stake:** TZS 1,000 per ticket (was TZS 2,000 historically)
- **Grand Prize:** Over TZS 1 billion (rolls over if not won)
- **Bonuses:** Awarded for 12-16 correct predictions
- **Schedule:** Weekend (typically closes Saturday/Sunday)
- **Markets:** 1X2 (Home Win, Draw, Away Win)
- **Double Chance:** Available for up to 10 matches (increases stake)

### 2.2 Midweek Jackpot
- **Matches:** 13 pre-selected football matches
- **Stake:** TZS 1,000 per ticket
- **Schedule:** Midweek (typically closes Wednesday)
- **Same structure as Supa but fewer matches

### 2.3 Supa Jackpot Pro (Sub-variants)
SportPesa also offers Pro versions where you can play fewer games:
- Pro 13: 13 matches
- Pro 14: 14 matches
- Pro 15: 15 matches
- Pro 16: 16 matches
- Pro 17: Full 17 matches

**Our bot focuses on the full Supa Jackpot 17 and Midweek 13.**

### 2.4 Typical Fixture Composition
Based on research of past jackpots, fixtures come from:
- Premier League (England)
- La Liga (Spain)
- Serie A (Italy)
- Bundesliga (Germany)
- Ligue 1 (France)
- Championship (England)
- Eredivisie (Netherlands)
- Primeira Liga (Portugal)
- Belgian Pro League
- Scottish Premiership
- Various lower divisions and international leagues

**This means the team name resolver must handle teams from 15+ leagues.**

---

## 3. System Architecture

```
                         JACKPOT PREDICTOR BOT

  +--------------+    +--------------+    +--------------+
  |  Scheduler   |--->|  Scraper     |--->|  Name Resolver|
  |  (systemd)   |    |  (SportPesa) |    |  (EdgeBot    |
  |  Mon/Thu     |    |  + cache)    |    |   canonical)  |
  |  20:00 EAT   |    |              |    |               |
  +--------------+    +--------------+    +--------------+
                                                   |
                                                   v
  +-----------------------------------------------------------+
  |              EDGE BOT MODEL PIPELINE (REUSED)             |
  |  +------------+  +------------+  +--------------------+  |
  |  |  Ensemble  |->| Calibrator |->| 1X2 Probabilities  |  |
  |  |  (loaded   |  |  (loaded   |  |  (H%, D%, A%)      |  |
  |  |  .pkl)     |  |  .pkl)     |  |                    |  |
  |  +------------+  +------------+  +--------------------+  |
  +-----------------------------------------------------------+
                              |
                              v
  +-----------------------------------------------------------+
  |              PREDICTION ENGINE                            |
  |  * Pick highest-probability outcome per match             |
  |  * Confidence tier (High / Medium / Low / Uncertain)      |
  |  * Alternative pick (2nd highest)                         |
  |  * Match metadata (league, kickoff, days until close)     |
  +-----------------------------------------------------------+
                              |
                              v
  +-----------------------------------------------------------+
  |              FORMATTER (Forebet-style)                    |
  |  Match 1: Arsenal vs Chelsea                              |
  |    1 (Home) -- 58.2% HIGH CONFIDENCE                     |
  |    Alternative: X (Draw) -- 24.5%                         |
  |    Margin: 33.7% | Model edge: Strong home advantage     |
  +-----------------------------------------------------------+
                              |
                              v
  +-----------------------------------------------------------+
  |              DELIVERY                                      |
  |  * Telegram Bot API (reuse EdgeBot bot or dedicated)      |
  |  * Email (SMTP / Gmail API -- reuse EdgeBot credentials)|
  |  * Save to jackpots/ directory for audit trail            |
  +-----------------------------------------------------------+
```

---

## 4. Project Structure

```
jackpot_predictor/
|-- config/
|   |-- __init__.py
|   |-- settings.py              # All paths, API keys, thresholds
|   |-- jackpot.yaml             # Jackpot-specific config
|
|-- scraper/
|   |-- __init__.py
|   |-- sportpesa.py             # Core scraping logic
|   |-- cache.py                 # Local cache to avoid re-scraping
|   |-- parsers.py               # HTML/JSON parsing helpers
|
|-- resolver/
|   |-- __init__.py
|   |-- name_resolver.py         # Team name canonicalization
|   |-- mappings.json            # SportPesa name -> EdgeBot name
|   |-- fuzzy_matcher.py         # Levenshtein / rapidfuzz fallback
|   |-- league_detector.py       # Infer league from team names
|
|-- predictor/
|   |-- __init__.py
|   |-- edgebot_bridge.py        # Loads EdgeBot artifacts, runs prediction
|   |-- confidence_tier.py       # Classifies predictions into tiers
|   |-- formatter.py             # Forebet-style output formatting
|
|-- delivery/
|   |-- __init__.py
|   |-- telegram_sender.py       # Telegram bot delivery
|   |-- email_sender.py          # Email delivery (reuse EdgeBot's)
|   |-- template.html            # HTML email template
|
|-- scheduler/
|   |-- __init__.py
|   |-- jackpot_schedule.py      # Detects next jackpot date, triggers run
|
|-- data/
|   |-- jackpots/                # Saved prediction outputs (JSON + HTML)
|   |-- cache/                   # Scraped fixture cache
|   |-- logs/                    # Application logs
|
|-- tests/
|   |-- test_scraper.py
|   |-- test_resolver.py
|   |-- test_predictor.py
|
|-- main.py                      # Entry point: python main.py
|-- requirements.txt
|-- .env.example
|-- .env                         # Gitignored -- real secrets
|-- deploy/
|   |-- jackpot-predictor.service  # systemd service file
|   |-- jackpot-midweek.timer     # systemd timer file
|   |-- jackpot-supa.timer        # systemd timer file
|   |-- install.sh                # One-command VPS setup
```

---
## 5. Detailed Build Instructions by Module

### 5.1 scraper/sportpesa.py -- The Scraping Engine

**Objective:** Extract the 17 (or midweek 13) fixtures from SportPesa TZ jackpot pages.

**Target URLs:**
- Supa Jackpot 17: `https://sportpesa.co.tz/en/supa-jackpot`
- Midweek Jackpot: `https://sportpesa.co.tz/en/midweek-jackpot` (verify actual URL)

**Approach (use the most reliable of these):**

#### Method A: Direct HTTP + BeautifulSoup (Preferred for simplicity)

```python
import requests
from bs4 import BeautifulSoup
import json
from datetime import datetime, timedelta

def fetch_jackpot_fixtures(jackpot_type: str = 'supa') -> list[dict]:
    # Fetch jackpot fixtures from SportPesa TZ
    # Returns list of dicts with keys:
    #   match_number, home_team_raw, away_team_raw,
    #   kickoff_datetime, league_hint, jackpot_type
    
    # 1. Use requests with realistic headers (User-Agent, Accept-Language)
    # 2. Handle Cloudflare or basic bot protection with session cookies
    # 3. Parse the fixture list -- look for:
    #    - Match number (1-17)
    #    - Home team name (left side)
    #    - Away team name (right side)
    #    - Date/time of match
    #    - League/competition name (if shown)
    # 4. If HTML structure changes, log raw HTML snippet for debugging
    # 5. Return structured data, never raw HTML to downstream
    pass
```

#### Method B: API Endpoint Discovery (More robust)

```python
# SportPesa may expose an internal API. Use browser DevTools to:
# 1. Open https://sportpesa.co.tz/en/supa-jackpot in browser
# 2. Open Network tab, refresh
# 3. Look for XHR/fetch requests returning JSON with fixture data
# 4. Replicate that request in Python
# 5. This is often more stable than HTML parsing
```

#### Method C: Playwright / Selenium (Last resort)

```python
# Only if the site is heavily JavaScript-rendered and no API exists
# Use playwright-stealth or selenium with undetected-chromedriver
# Headless browser, wait for fixture list to render, extract data
# SLOWER and HEAVIER -- avoid unless necessary
```

**Cache Strategy (scraper/cache.py):**

```python
# Cache scraped fixtures to avoid hammering SportPesa
# Key: jackpot_type + date
# TTL: 24 hours (jackpots don't change frequently)
# Storage: JSON files in data/cache/
# If cache hit and < 24h old, return cached data
# If cache miss or > 24h old, scrape fresh
```

**Error Handling:**
- If scrape fails -> log error, send admin alert (Telegram), exit gracefully
- If partial data (e.g., only 15 of 17 matches) -> flag as incomplete, still process what we have
- If site structure changed -> save raw HTML to data/logs/scrape_error_YYYYMMDD.html for debugging

---

### 5.2 resolver/name_resolver.py -- Team Name Canonicalization

**This is the most critical module.** EdgeBot's models are trained on canonical names (e.g., 'Manchester City'). SportPesa may use abbreviations ('Man City', 'MCFC'). Mismatches = model can't find team = prediction fails.

**Strategy -- Three-tier fallback:**

#### Tier 1: Exact Mapping Table (resolver/mappings.json)

```json
{
  'sportpesa_to_edgebot': {
    'Man Utd': 'Manchester United',
    'Man United': 'Manchester United',
    'Man City': 'Manchester City',
    'MCFC': 'Manchester City',
    'Spurs': 'Tottenham',
    'Tottenham Hotspur': 'Tottenham',
    'Newcastle Utd': 'Newcastle',
    'Wolves': 'Wolverhampton',
    'Nottm Forest': 'Nottingham Forest',
    'Brighton & Hove Albion': 'Brighton',
    'West Ham Utd': 'West Ham',
    'C Palace': 'Crystal Palace',
    'Sheffield Utd': 'Sheffield United',
    'Bayern Munich': 'Bayern Munchen',
    'Bayern Munchen': 'Bayern Munchen',
    'Dortmund': 'Borussia Dortmund',
    'PSG': 'Paris Saint-Germain',
    'Inter Milan': 'Inter',
    'AC Milan': 'Milan',
    'Roma': 'AS Roma',
    'Lazio': 'SS Lazio',
    'Napoli': 'Napoli',
    'Juventus': 'Juventus',
    'Atletico Madrid': 'Atletico Madrid',
    'Athletic Bilbao': 'Athletic Club',
    'Real Sociedad': 'Real Sociedad',
    'Betis': 'Real Betis',
    'Sevilla': 'Sevilla FC',
    'Leverkusen': 'Bayer Leverkusen',
    'Frankfurt': 'Eintracht Frankfurt',
    'Leipzig': 'RB Leipzig',
    'Wolfsburg': 'VfL Wolfsburg',
    'Stuttgart': 'VfB Stuttgart',
    'Marseille': 'Olympique Marseille',
    'Lyon': 'Olympique Lyon',
    'Lille': 'Lille OSC',
    'Rennes': 'Stade Rennes',
    'Nice': 'OGC Nice',
    'Monaco': 'AS Monaco',
    'Ajax': 'Ajax Amsterdam',
    'PSV': 'PSV Eindhoven',
    'Feyenoord': 'Feyenoord Rotterdam',
    'Benfica': 'Benfica Lisbon',
    'Porto': 'FC Porto',
    'Sporting': 'Sporting Lisbon',
    'Celtic': 'Celtic Glasgow',
    'Rangers': 'Rangers Glasgow',
    'Galatasaray': 'Galatasaray Istanbul',
    'Fenerbahce': 'Fenerbahce Istanbul',
    'Besiktas': 'Besiktas Istanbul'
  }
}
```

**Build process for mappings:**
1. Scrape SportPesa once, collect all unique team names
2. Manually (or semi-automatically) map to EdgeBot's historical.parquet team names
3. Store in `mappings.json`
4. If a name isn't in mappings, log it and use Tier 2

#### Tier 2: Fuzzy Matching (resolver/fuzzy_matcher.py)

```python
from rapidfuzz import fuzz, process

def fuzzy_match_team(sportpesa_name: str, edgebot_teams: list[str]) -> tuple[str, float]:
    result = process.extractOne(sportpesa_name, edgebot_teams, scorer=fuzz.token_sort_ratio)
    if result and result[1] >= 85:
        return result[0], result[1]
    return None, 0.0
```

#### Tier 3: Manual Flag + Admin Alert

```python
# If Tier 1 and Tier 2 both fail:
# 1. Log the unmatched name
# 2. Send Telegram alert to admin: 'Unmatched team: X in Match Y'
# 3. Skip that match (return None) -- don't guess
# 4. Admin adds mapping to mappings.json for next run
```

**EdgeBot Team Name Loading:**

```python
import pandas as pd

def load_edgebot_teams(edgebot_data_path: str) -> list[str]:
    df = pd.read_parquet(f'{edgebot_data_path}/data_cache/historical.parquet')
    teams = set(df['HomeTeam'].dropna().unique()) | set(df['AwayTeam'].dropna().unique())
    return sorted(list(teams))
```

---
### 5.3 predictor/edgebot_bridge.py -- The Prediction Bridge

**Objective:** Load EdgeBot's trained artifacts and run 1X2 predictions on jackpot fixtures WITHOUT retraining.

**What to Reuse from EdgeBot:**

| Artifact | Path (relative to EdgeBot root) | Purpose |
|----------|--------------------------------|---------|
| `catboost_ensemble.pkl` | `data_cache/catboost_ensemble.pkl` | Strongest 1X2 model |
| `ml_ensemble.pkl` | `data_cache/ml_ensemble.pkl` | LightGBM backup |
| `calibrator.pkl` | `data_cache/calibrator.pkl` | Probability calibration |
| `historical.parquet` | `data_cache/historical.parquet` | Feature engineering needs history |
| `config.yaml` | `config.yaml` | Model weights, thresholds |

**Bridge Implementation:**

```python
import sys
import os

# Add EdgeBot to Python path so we can import its modules
EDGEBOT_PATH = os.environ.get('EDGEBOT_PATH', 'C:\\Users\\mykii\\Downloads\\Betting algorithim')
sys.path.insert(0, EDGEBOT_PATH)

from models.orchestrator import Ensemble
from models.config_loader import load_config
from models.feature_engineering import build_features
from models.calibration import ProbabilityCalibrator
import pickle

class EdgeBotBridge:
    # Loads EdgeBot's production pipeline and exposes a simple interface:
    # predict_1x2(home_team, away_team, league_hint) -> {home_prob, draw_prob, away_prob}
    
    def __init__(self, edgebot_root: str):
        self.edgebot_root = edgebot_root
        self.cfg = load_config(os.path.join(edgebot_root, 'config.yaml'))
        
        # Load historical data for feature engineering
        self.history = pd.read_parquet(os.path.join(edgebot_root, 'data_cache', 'historical.parquet'))
        
        # Load ensemble (this loads all model artifacts internally)
        self.ensemble = Ensemble.from_config(self.cfg, history=self.history)
        
        # Load calibrator
        with open(os.path.join(edgebot_root, 'data_cache', 'calibrator.pkl'), 'rb') as f:
            self.calibrator = pickle.load(f)
    
    def predict(self, home_team: str, away_team: str, league_code: str = 'E0') -> dict:
        # Predict 1X2 probabilities for a single fixture
        # Steps:
        # 1. Build the 27-feature vector (same as EdgeBot daily run)
        # 2. Run ensemble prediction
        # 3. Apply calibration
        # 4. Return {home: float, draw: float, away: float}
        # Note: If teams not in history, ensemble falls back to league prior
        pass
```

**Critical Implementation Notes for Agent:**

1. **Do NOT retrain anything.** Only load pre-trained artifacts. The entire value proposition is reusing EdgeBot's existing models.

2. **Feature Engineering:** The 27-feature vector requires historical context. For a jackpot match happening in 3 days, build features using all history **before** that date. Since the jackpot is future-dated, use the latest available history (the model implicitly assumes the match is 'today' for feature computation).

3. **League Code Detection:** SportPesa may not show the league. Use the `league_hint` from scraping, or infer from team names:
   - Arsenal, Chelsea, Liverpool -> 'E0' (Premier League)
   - Bayern, Dortmund -> 'D1' (Bundesliga)
   - Real Madrid, Barcelona -> 'SP1' (La Liga)
   - Juventus, Inter, Milan -> 'I1' (Serie A)
   - PSG, Marseille -> 'F1' (Ligue 1)
   - Build a `team_to_league` mapping from historical.parquet

4. **If a team is unknown to EdgeBot:** The ensemble will use league-average prior (shrinkage). This is fine -- prediction will be low-confidence, which the tier system will flag.

5. **No odds integration.** This bot does not fetch or compare odds. It only produces model probabilities.

---
### 5.4 predictor/confidence_tier.py -- Confidence Classification

**Objective:** Convert raw probabilities into human-readable confidence tiers.

```python
from enum import Enum

class ConfidenceTier(Enum):
    HIGH = 'high'      # Best pick > 55% probability, margin > 15% over 2nd
    MEDIUM = 'medium'  # Best pick 45-55%, margin > 8% over 2nd
    LOW = 'low'        # Best pick < 45%, or margin < 8% over 2nd
    UNCERTAIN = 'uncertain'  # All three outcomes within 10% of each other

def classify_prediction(home_prob: float, draw_prob: float, away_prob: float) -> dict:
    probs = {'H': home_prob, 'D': draw_prob, 'A': away_prob}
    sorted_probs = sorted(probs.items(), key=lambda x: x[1], reverse=True)
    
    primary_pick, primary_prob = sorted_probs[0]
    secondary_pick, secondary_prob = sorted_probs[1]
    margin = primary_prob - secondary_prob
    
    if primary_prob > 0.55 and margin > 0.15:
        tier = ConfidenceTier.HIGH
        reasoning = f'Strong {primary_pick} pick with {primary_prob:.1%} confidence'
    elif primary_prob > 0.45 and margin > 0.08:
        tier = ConfidenceTier.MEDIUM
        reasoning = f'Moderate {primary_pick} pick with {primary_prob:.1%} confidence'
    elif margin < 0.08:
        tier = ConfidenceTier.UNCERTAIN
        reasoning = f'Very close match -- {primary_pick} only {margin:.1%} ahead'
    else:
        tier = ConfidenceTier.LOW
        reasoning = f'Weak signal -- {primary_prob:.1%} for {primary_pick}'
    
    return {
        'primary_pick': primary_pick,
        'primary_prob': primary_prob,
        'secondary_pick': secondary_pick,
        'secondary_prob': secondary_prob,
        'confidence_tier': tier,
        'margin': margin,
        'reasoning': reasoning
    }
```

**Pick Encoding:**
- 'H' = Home Win -> Display as '1' (standard betting notation)
- 'D' = Draw -> Display as 'X'
- 'A' = Away Win -> Display as '2'

---

### 5.5 predictor/formatter.py -- Forebet-Style Output

**Objective:** Produce clean, readable output that looks like Forebet or similar prediction sites.

**Telegram Format (Plain Text):**

```
SportPesa Supa Jackpot 17 -- Predictions
Jackpot closes: Saturday, 12 July 2026
Generated: Thursday, 10 July 2026 at 20:00 EAT

Match 1: Arsenal vs Chelsea (Premier League)
  1 (Home) -- 58.2% HIGH CONFIDENCE
  Alternative: X (Draw) -- 24.5%
  Margin: 33.7% | Model edge: Strong home advantage

Match 2: Bayern Munich vs Borussia Dortmund (Bundesliga)
  1 (Home) -- 62.1% HIGH CONFIDENCE
  Alternative: 2 (Away) -- 18.3%
  Margin: 43.8% | Model edge: Dominant home form

Match 3: Real Madrid vs Sevilla (La Liga)
  1 (Home) -- 55.4% MEDIUM CONFIDENCE
  Alternative: X (Draw) -- 26.1%
  Margin: 29.3% | Model edge: Home quality

... (Match 4-17)

----------------------------------------
SUMMARY
High Confidence:   8 picks
Medium Confidence: 5 picks
Low Confidence:    3 picks
Uncertain:         1 pick

Strategy Note: The model favors home teams (12/17 picks are '1').
Consider mixing 2-3 away picks for variation.

Disclaimer: These are model predictions (~53-54% accuracy), not guarantees.
A 17-match jackpot is statistically very difficult. Use as research only.
```

**Email Format (HTML):**
- Same content but formatted as HTML table
- Color-coded confidence: Green High, Yellow Medium, Orange Low, Red Uncertain
- Include a 'Download CSV' link or attachment with raw probabilities

**CSV Attachment (for power users):**

```csv
Match,Home Team,Away Team,League,1 Prob,X Prob,2 Prob,Pick,Confidence,Margin,Alternative
1,Arsenal,Chelsea,E0,0.582,0.245,0.173,1,HIGH,0.337,X
2,Bayern Munich,Dortmund,D1,0.621,0.203,0.176,1,HIGH,0.418,2
...
```

---

### 5.6 delivery/telegram_sender.py & delivery/email_sender.py

**Reuse EdgeBot's delivery modules where possible.**

**Telegram:**

```python
import os
import requests

TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_CHAT_ID = os.getenv('TELEGRAM_CHAT_ID')

def send_jackpot_prediction(text: str, html_path: str = None):
    url = f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage'
    payload = {
        'chat_id': TELEGRAM_CHAT_ID,
        'text': text,
        'parse_mode': 'HTML'
    }
    requests.post(url, json=payload, timeout=30)
    
    if html_path and os.path.exists(html_path):
        with open(html_path, 'rb') as f:
            requests.post(
                f'https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendDocument',
                data={'chat_id': TELEGRAM_CHAT_ID},
                files={'document': f},
                timeout=30
            )
```

**Email:**

```python
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders

def send_jackpot_email(subject: str, html_body: str, csv_attachment: str = None):
    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = os.getenv('EMAIL_SENDER')
    msg['To'] = os.getenv('EMAIL_RECEIVER')
    
    msg.attach(MIMEText(html_body, 'html'))
    
    if csv_attachment:
        part = MIMEBase('application', 'octet-stream')
        with open(csv_attachment, 'rb') as f:
            part.set_payload(f.read())
        encoders.encode_base64(part)
        part.add_header('Content-Disposition', f'attachment; filename={os.path.basename(csv_attachment)}')
        msg.attach(part)
    
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as server:
        server.login(os.getenv('EMAIL_SENDER'), os.getenv('EMAIL_PASSWORD'))
        server.send_message(msg)
```

---
### 5.7 scheduler/jackpot_schedule.py -- When to Run

**Objective:** Detect the next jackpot date and trigger the bot at 20:00 EAT, 2 days before.

**SportPesa Schedule (verify and adjust):**
- **Supa Jackpot 17:** Typically closes Saturday/Sunday. Predictions sent Thursday 20:00 EAT.
- **Midweek Jackpot:** Typically closes Wednesday. Predictions sent Monday 20:00 EAT.

**Detection Logic:**

```python
from datetime import datetime, timedelta
import pytz

EAT = pytz.timezone('Africa/Nairobi')  # EAT = UTC+3

def get_next_jackpot_date() -> tuple[str, datetime]:
    # Determine the next jackpot type and its closing date
    # Strategy:
    # 1. Scrape the SportPesa jackpot page and look for:
    #    - 'Closes: [Date]' or 'Kickoff: [Date]'
    #    - Countdown timer
    #    - The earliest match date in the fixture list
    # 2. If today is Monday-Tuesday -> next is likely Midweek (closes Wednesday)
    # 3. If today is Thursday-Friday -> next is likely Supa (closes Saturday/Sunday)
    # 4. Return (jackpot_type, closing_datetime)
    pass

def should_run_today() -> bool:
    # Check if today is 2 days before a jackpot
    # Example: Jackpot closes Saturday 12 July
    # -> Run Thursday 10 July at 20:00 EAT
    pass
```

**systemd Timer Configuration:**

Use **two systemd timers**:

```ini
# /home/edgebot/.config/systemd/user/jackpot-midweek.timer
[Unit]
Description=Jackpot Predictor -- Midweek (Mon 20:00 EAT)

[Timer]
OnCalendar=Mon *-*-* 20:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

```ini
# /home/edgebot/.config/systemd/user/jackpot-supa.timer
[Unit]
Description=Jackpot Predictor -- Supa (Thu 20:00 EAT)

[Timer]
OnCalendar=Thu *-*-* 20:00:00
Persistent=true

[Install]
WantedBy=timers.target
```

**Service File (shared):**

```ini
# /home/edgebot/.config/systemd/user/jackpot-predictor.service
[Unit]
Description=SportPesa Jackpot Predictor
After=network.target

[Service]
Type=oneshot
WorkingDirectory=/home/edgebot/jackpot_predictor
Environment='PATH=/home/edgebot/jackpot_predictor/venv/bin'
EnvironmentFile=/home/edgebot/jackpot_predictor/.env
ExecStart=/home/edgebot/jackpot_predictor/venv/bin/python main.py
StandardOutput=append:/home/edgebot/jackpot_predictor/logs/run.log
StandardError=append:/home/edgebot/jackpot_predictor/logs/error.log

[Install]
WantedBy=default.target
```

---
## 6. main.py -- Entry Point

```python
#!/usr/bin/env python3
# SportPesa Jackpot Predictor -- Main Entry Point
# Run: python main.py [--dry-run] [--force-date YYYY-MM-DD]

import argparse
import logging
import os
import sys
from datetime import datetime
import pytz

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(name)s: %(message)s',
    handlers=[
        logging.FileHandler('data/logs/jackpot.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

def main(dry_run: bool = False, force_date: str = None):
    logger.info('=== Jackpot Predictor Starting ===')
    
    # Step 1: Detect jackpot
    from scheduler.jackpot_schedule import get_next_jackpot_date
    jackpot_type, closing_date = get_next_jackpot_date(force_date)
    logger.info(f'Detected: {jackpot_type}, closes {closing_date}')
    
    # Step 2: Scrape fixtures
    from scraper.sportpesa import fetch_jackpot_fixtures
    fixtures = fetch_jackpot_fixtures(jackpot_type)
    logger.info(f'Scraped {len(fixtures)} fixtures')
    
    if len(fixtures) < 10:
        logger.error(f'Too few fixtures ({len(fixtures)}), aborting')
        return
    
    # Step 3: Resolve names
    from resolver.name_resolver import resolve_fixture_names
    resolved = resolve_fixture_names(fixtures)
    logger.info(f'Resolved {len([r for r in resolved if r is not None])}/{len(resolved)} fixtures')
    
    # Step 4: Predict
    from predictor.edgebot_bridge import EdgeBotBridge
    bridge = EdgeBotBridge(os.getenv('EDGEBOT_PATH'))
    
    predictions = []
    for fixture in resolved:
        if fixture is None:
            predictions.append(None)
            continue
        
        probs = bridge.predict(
            home_team=fixture['home_team_canonical'],
            away_team=fixture['away_team_canonical'],
            league_code=fixture.get('league_code', 'E0')
        )
        predictions.append(probs)
    
    # Step 5: Classify confidence
    from predictor.confidence_tier import classify_prediction
    classified = []
    for fixture, probs in zip(resolved, predictions):
        if fixture is None or probs is None:
            classified.append(None)
            continue
        
        tier = classify_prediction(probs['home'], probs['draw'], probs['away'])
        classified.append({
            **fixture,
            **probs,
            **tier
        })
    
    # Step 6: Format
    from predictor.formatter import format_jackpot_output
    telegram_text, html_body, csv_path = format_jackpot_output(
        jackpot_type=jackpot_type,
        closing_date=closing_date,
        predictions=classified
    )
    
    # Step 7: Deliver
    if not dry_run:
        from delivery.telegram_sender import send_jackpot_prediction
        from delivery.email_sender import send_jackpot_email
        
        send_jackpot_prediction(telegram_text, csv_path)
        send_jackpot_email(
            subject=f'SportPesa {jackpot_type.upper()} Predictions -- {closing_date.strftime("%d %b %Y")}',
            html_body=html_body,
            csv_attachment=csv_path
        )
        logger.info('Delivered to Telegram and Email')
    else:
        logger.info('DRY RUN -- printing to console only')
        print(telegram_text)
    
    # Step 8: Archive
    from predictor.formatter import save_jackpot_archive
    save_jackpot_archive(jackpot_type, closing_date, classified)
    
    logger.info('=== Jackpot Predictor Complete ===')

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true', help='Print only, do not send')
    parser.add_argument('--force-date', type=str, help='Force jackpot closing date (YYYY-MM-DD)')
    args = parser.parse_args()
    
    main(dry_run=args.dry_run, force_date=args.force_date)
```

---

## 7. Configuration & Environment

### .env File

```bash
# EdgeBot Path (where the trained models live)
EDGEBOT_PATH=/home/edgebot/edgebot

# Telegram (can reuse EdgeBot's bot or create dedicated)
TELEGRAM_BOT_TOKEN=your_bot_token_here
TELEGRAM_CHAT_ID=your_chat_id_here

# Email (reuse EdgeBot credentials)
EMAIL_SENDER=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_RECEIVER=your_email@gmail.com

# SportPesa (if API key exists, otherwise leave empty)
SPORTPESA_API_KEY=optional

# Paths
DATA_DIR=/home/edgebot/jackpot_predictor/data
CACHE_DIR=/home/edgebot/jackpot_predictor/data/cache
JACKPOTS_DIR=/home/edgebot/jackpot_predictor/data/jackpots
```

### config/jackpot.yaml

```yaml
# Confidence tier thresholds
confidence:
  high:
    min_primary_prob: 0.55
    min_margin: 0.15
  medium:
    min_primary_prob: 0.45
    min_margin: 0.08
  low:
    min_primary_prob: 0.35
    min_margin: 0.05

# Delivery settings
delivery:
  telegram:
    enabled: true
    parse_mode: 'HTML'
  email:
    enabled: true
    smtp_host: 'smtp.gmail.com'
    smtp_port: 465

# Scraping settings
scraper:
  user_agent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
  timeout: 30
  retries: 3
  cache_ttl_hours: 24

# EdgeBot bridge settings
edgebot:
  override_models:
    - catboost
    - poisson
    - elo
  apply_calibration: true
  use_conformal: false
  default_league: 'E0'
```

---
