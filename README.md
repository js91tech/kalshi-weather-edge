# Kalshi Favorites

Paper/live scanner for **high hit-rate Kalshi trades**: buy YES when the market is very bullish, buy NO when very bearish.

This is **not** financial advice. Default mode is `paper`.

## Strategy

| Signal | When |
|--------|------|
| **BUY YES** | Market mid >= `yes_threshold` (default 0.90) |
| **BUY NO** | Market mid <= `no_threshold` (default 0.10) |
| **PASS** | Everything in between |

Backtests on 2026 settled history showed **~95%+ hit rates** on extreme favorites (small $/contract).

## Markets scanned

Configured in `config.yaml` under `favorites.series`:

- `KXAAAGASD` — AAA gas prices
- `KXEURUSD` — EUR/USD
- `KXNASDAQDUD` — Nasdaq
- `KXCPINDEX` — CPI index
- `KXUSNFP` — Nonfarm payrolls

## Setup

```powershell
cd $env:USERPROFILE\Projects\kalshi-weather-edge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run scan (CLI)

```powershell
$env:PYTHONPATH="src"
python scripts\run_pipeline.py
```

## Streamlit UI

```powershell
streamlit run app\streamlit_app.py
```

## Hit-rate backtest

```powershell
python scripts\hunt_hit_rates.py --fast
```

## Live trading

Copy `.env.example` to `.env` and add Kalshi API keys. In Streamlit, switch to **live** mode and check the confirmation box before executing.

## Deploy (Streamlit Community Cloud)

1. Push to GitHub
2. [share.streamlit.io](https://share.streamlit.io/) → Create app
3. Main file: `app/streamlit_app.py`

## Config

| Setting | Meaning |
|---------|---------|
| `favorites.yes_threshold` | Buy YES when mid >= this |
| `favorites.no_threshold` | Buy NO when mid <= this |
| `favorites.contracts` | Paper size per signal |
| `mode` | `paper` or `live` |

## Caveats

- High hit rate on near-certain contracts != huge profit per trade
- You're trading **with** the crowd, not beating it
- Past backtests don't guarantee future results
