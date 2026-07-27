# Kalshi Weather Edge

Paper-only research monitor: compare **Open-Meteo GFS ensemble** high-temperature forecasts to **Kalshi** city high-temp brackets, score edge after fees, and log signals before settlement.

This is **not** a live trading bot and **not** financial advice. Default mode is `paper`.

## What it does

1. Pulls open Kalshi markets for configured cities (`KXHIGHNY`, `KXHIGHCHI`, …)
2. Fetches GFS ensemble daily max temps (°F)
3. Maps p10/p50/p90 → `N(μ, σ)` and prices each `less` / `between` / `greater` contract
4. Computes maker/taker edge vs bid-ask (longshot fade preferred)
5. Writes forecasts + signals to SQLite (`data/ledger.db`)
6. Shows an edge board in Streamlit

## Deploy online (recommended: Streamlit Community Cloud)

Best free host for this app: **[Streamlit Community Cloud](https://share.streamlit.io/)**.

1. Push this repo to GitHub (done if you used the setup above)
2. Go to [share.streamlit.io](https://share.streamlit.io/) and sign in with GitHub
3. **Create app** → select `kalshi-weather-edge`
4. Main file path: `app/streamlit_app.py`
5. Click **Deploy**

Your public URL will look like:
`https://share.streamlit.io/<your-github-user>/kalshi-weather-edge/main/app/streamlit_app.py`
(or a custom subdomain Streamlit assigns)

### Other hosts
| Host | Notes |
|------|--------|
| **Streamlit Community Cloud** | Easiest; free; auto-deploys on git push |
| **Render** / **Railway** / **Fly.io** | Good if you outgrow Community Cloud |
| **Hugging Face Spaces** | Streamlit Spaces also work |

Keep `mode: paper` unless you intentionally add authenticated Kalshi trading later.

## Setup

```powershell
cd $env:USERPROFILE\Projects\kalshi-weather-edge
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

## Run pipeline (CLI)

```powershell
python scripts\run_pipeline.py
```

## Streamlit UI

```powershell
streamlit run app\streamlit_app.py
```

Click **Run pipeline now** in the sidebar.

## Config

Edit `config.yaml` for cities, edge thresholds, fees, and bankroll sizing.

| Setting | Meaning |
|--------|---------|
| `edge.min_edge` | Minimum maker edge (probability points) |
| `edge.min_edge_taker` | Higher bar for crossing the spread |
| `edge.longshot_market_max` | Mid below this → longshot fade candidate |
| `mode` | Keep `paper` until you intentionally change it |

## Important caveats

- Public weather models are often already priced into Kalshi within minutes of release.
- High hit rate on near-certain contracts ≠ profitable edge.
- Settlement uses **NWS CLI** for the station in each contract’s rules — the ensemble is an approximation in settlement space; calibrate further with historical CLI before trusting live size.
- No gematria / mystical signals. Math + market microstructure only.

## Project layout

```
config.yaml
app/streamlit_app.py
scripts/run_pipeline.py
src/kalshi_weather_edge/
  config.py
  kalshi_client.py
  weather.py
  brackets.py
  fees.py
  edge.py
  db.py
  pipeline.py
data/ledger.db   # created on first run
```
