# Kalshi Favorites

Paper/live scanner for **consensus Kalshi trades** on gas, FX, Nasdaq, CPI, and NFP brackets.

## Strategy profiles

| Profile | Behavior | Goal |
|---------|----------|------|
| **favorites** | Global YES≥0.90 / NO≤0.10 | Highest hit rate |
| **high_profit** | Series-aware (EUR NO≤0.30; gas stays tight; **no NFP/CPI**) | Higher $/contract |

Risk controls: max 3 trades per event, skip spreads > 0.08.

## Features

- Live open-market scan (paper or live)
- Auto settlement sync + paper equity / W–L board
- Scheduled GitHub Action every 30 minutes + Discord/Slack webhook alerts
- Side-by-side strategy backtests

## Setup

```powershell
cd $env:USERPROFILE\Projects\kalshi-weather-edge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
copy .env.example .env
```

Optional in `.env`:
- `ALERT_WEBHOOK_URL` — Discord or Slack incoming webhook
- Kalshi API keys for live trading

## CLI

```powershell
$env:PYTHONPATH="src"
python scripts\run_pipeline.py
python scripts\run_pipeline.py --strategy high_profit
python scripts\scheduled_scan.py --strategy both
python scripts\backtest_strategies.py
```

## Streamlit

```powershell
streamlit run app\streamlit_app.py
```

Tabs: **Live signals**, **Performance** (equity + W/L), **Backtest**, **Help**.

## Scheduled alerts (GitHub Actions)

1. Repo → Settings → Secrets → `ALERT_WEBHOOK_URL`
2. Workflow `.github/workflows/scheduled_scan.yml` runs every 30 minutes (and on demand)
3. Artifacts: `data/scans/latest.json`

## Deploy (Streamlit Community Cloud)

1. Push to GitHub
2. [share.streamlit.io](https://share.streamlit.io/) → Create app
3. Main file: `app/streamlit_app.py`

## Caveats

- High hit rate ≠ huge profit per contract
- You're trading **with** the crowd
- Past backtests don't guarantee future results
- Streamlit Cloud SQLite resets on reboot — use scheduled Action artifacts for persistence of scan summaries
