# Kalshi Favorites

Paper/live scanner for **consensus Kalshi trades** on gas, FX, Nasdaq, CPI, and NFP brackets.

Two strategy profiles in `config.yaml`:

| Profile | BUY YES | BUY NO | Goal |
|---------|---------|--------|------|
| **favorites** | mid >= 0.90 | mid <= 0.10 | Highest hit rate (~97%) |
| **high_profit** | mid >= 0.90 | mid <= 0.30 | Higher $/contract (~$0.075 avg) |

This is **not** financial advice. Default mode is `paper`.

## Backtest snapshot (Apr–Jul 2026)

| Profile | Hit rate | Avg $/contract | Total PnL |
|---------|----------|----------------|-----------|
| favorites | 96.8% | $0.032 | +$16.90 |
| high_profit | 94.2% | $0.075 | +$55.79 |

EUR/USD drives most high-profit gains. Run `python scripts/backtest_strategies.py` to refresh.

## Markets scanned

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
python scripts\run_pipeline.py --strategy high_profit
```

## Streamlit UI

```powershell
streamlit run app\streamlit_app.py
```

Pick **Favorites** or **High profit** in the sidebar, then scan or backtest.

## Backtests

```powershell
python scripts\backtest_strategies.py
python scripts\hunt_profit_per_contract.py
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
| `favorites.*` | Tight consensus thresholds (0.90 / 0.10) |
| `high_profit.*` | Looser NO band (0.90 / 0.30) |
| `mode` | `paper` or `live` |

## Caveats

- High hit rate != huge profit per contract on favorites
- High profit widens the NO band — more $/win, slightly lower hit rate
- You're trading **with** the crowd, not beating it
- Past backtests don't guarantee future results
