from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.db import Ledger
from kalshi_weather_edge.pipeline import rows_to_dataframe_records, run_pipeline


st.set_page_config(
    page_title="Kalshi Weather Edge",
    page_icon="🌡️",
    layout="wide",
)

st.title("Kalshi Weather Edge")
st.caption("Paper-only monitor: ensemble forecast vs Kalshi temperature brackets. Not financial advice.")

settings = load_settings()
ledger = Ledger(settings.db_path)

with st.sidebar:
    st.header("Controls")
    st.write(f"Mode: **{settings.mode}**")
    st.write(f"Min maker edge: **{settings.min_edge:.0%}**")
    st.write(f"Min taker edge: **{settings.min_edge_taker:.0%}**")
    st.write(f"Cities: {len(settings.cities)}")
    run_clicked = st.button("Run pipeline now", type="primary", use_container_width=True)
    st.divider()
    st.markdown(
        """
        **How to read this**
        - `model_p` — our P(YES) from GFS ensemble  
        - `market_mid` — Kalshi bid/ask mid  
        - `PASS` is the default and usually correct  
        - Trades are **paper suggestions** only  
        """
    )

if run_clicked:
    with st.spinner("Fetching Kalshi markets + Open-Meteo ensembles..."):
        try:
            result = run_pipeline(settings, notes="streamlit")
            st.session_state["last_result"] = result
            st.success(
                f"Run #{result['run_id']}: scored {result['markets_scored']} markets — "
                f"{result['trade_signals']} trade / {result['pass_signals']} pass"
            )
            if result["errors"]:
                st.warning("\n".join(result["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Pipeline failed: {exc}")

stats = ledger.signal_stats()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Signals logged", stats["total_signals"])
c2.metric("Trade suggestions", stats["trade_signals"])
c3.metric("Passes", stats["pass_signals"])
c4.metric("Settled", stats["settled"])
c5.metric("Paper PnL ($)", f"{stats['paper_pnl']:.2f}")

result = st.session_state.get("last_result")
if result:
    df = pd.DataFrame(rows_to_dataframe_records(result))
else:
    # Fall back to latest DB signals
    sigs = ledger.latest_signals(limit=300)
    records = []
    for s in sigs:
        import json

        meta = {}
        try:
            meta = json.loads(s.get("meta_json") or "{}")
        except Exception:
            meta = {}
        records.append(
            {
                "city": meta.get("city_name") or s.get("city_id"),
                "date": s.get("target_date"),
                "ticker": s.get("ticker"),
                "subtitle": meta.get("subtitle"),
                "action": s.get("action"),
                "side": s.get("side"),
                "execution": s.get("execution"),
                "model_p": s.get("model_p"),
                "market_mid": s.get("market_mid"),
                "edge": s.get("edge"),
                "contracts": s.get("suggested_contracts"),
                "mu": meta.get("mu"),
                "sigma": meta.get("sigma"),
                "reason": s.get("reason"),
            }
        )
    df = pd.DataFrame(records)

if df.empty:
    st.info("No signals yet. Click **Run pipeline now** in the sidebar.")
    st.stop()

tab_board, tab_trades, tab_forecasts, tab_help = st.tabs(
    ["Edge board", "Trade suggestions", "Forecasts", "Methodology"]
)

with tab_board:
    show = df.copy()
    for col in ("model_p", "market_mid", "edge"):
        if col in show.columns:
            show[col] = pd.to_numeric(show[col], errors="coerce")
    st.dataframe(
        show.sort_values(["action", "edge"], ascending=[True, False]),
        use_container_width=True,
        hide_index=True,
    )
    plot_df = show.dropna(subset=["model_p", "market_mid"])
    if not plot_df.empty:
        fig = px.scatter(
            plot_df,
            x="market_mid",
            y="model_p",
            color="action",
            hover_data=["ticker", "city", "subtitle", "edge"],
            title="Model P(YES) vs market mid",
        )
        fig.add_shape(
            type="line",
            x0=0,
            y0=0,
            x1=1,
            y1=1,
            line=dict(dash="dash", color="gray"),
        )
        st.plotly_chart(fig, use_container_width=True)

with tab_trades:
    trades = df[df["action"] != "PASS"].copy()
    if trades.empty:
        st.write("No trade suggestions this run — markets look roughly efficient vs the ensemble.")
    else:
        st.dataframe(
            trades.sort_values("edge", ascending=False),
            use_container_width=True,
            hide_index=True,
        )

with tab_forecasts:
    fcdf = pd.DataFrame(ledger.latest_forecasts())
    if fcdf.empty:
        st.write("No forecasts stored yet.")
    else:
        st.dataframe(fcdf, use_container_width=True, hide_index=True)

with tab_help:
    st.markdown(
        f"""
### What this app does
1. Pulls open Kalshi high-temperature markets for configured cities  
2. Pulls Open-Meteo **GFS ensemble** daily max temps  
3. Converts ensemble p10/p50/p90 → `N(μ, σ)` and prices each bracket  
4. Compares to Kalshi mid, subtracts fees/spread logic  
5. Logs every signal **before** settlement (paper only)

### Decision rules (config.yaml)
- Maker min edge: `{settings.min_edge}`
- Taker min edge: `{settings.min_edge_taker}`
- Longshot fade when mid ≤ `{settings.longshot_market_max}` and overpriced

### Important
Forecast skill ≠ trading edge. Public NWP is often already in the book.
This tool finds **possible** mispricings for research — it does not guarantee profits.
Live trading is disabled (`mode: paper`).
"""
    )
