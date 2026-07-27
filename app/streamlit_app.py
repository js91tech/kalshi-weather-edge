from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.backtest import (  # noqa: E402
    apply_best_params_to_settings,
    fine_tune,
    persist_tuned_params,
    run_backtest,
    score_universe,
)
from kalshi_weather_edge.config import (  # noqa: E402
    live_credentials_configured,
    load_settings,
    save_mode_to_config,
    with_overrides,
)
from kalshi_weather_edge.db import Ledger  # noqa: E402
from kalshi_weather_edge.pipeline import rows_to_dataframe_records, run_pipeline  # noqa: E402
from kalshi_weather_edge.trading import execute_signal  # noqa: E402


st.set_page_config(
    page_title="Kalshi Weather Edge",
    page_icon="🌡️",
    layout="wide",
)

st.title("Kalshi Weather Edge")
st.caption("Ensemble forecast vs Kalshi temperature brackets. Not financial advice.")

base_settings = load_settings()
creds = live_credentials_configured()

with st.sidebar:
    st.header("Trading mode")
    mode = st.radio(
        "Mode",
        options=["paper", "live"],
        index=0 if base_settings.mode != "live" else 1,
        horizontal=True,
        help="Paper = signals + backtests only. Live can send real Kalshi orders.",
    )
    if mode != base_settings.mode:
        if st.button("Save mode to config.yaml"):
            save_mode_to_config(mode)
            st.success(f"Saved mode={mode}")
            st.rerun()

    use_demo = st.checkbox("Use Kalshi DEMO API", value=creds.get("env") == "demo")
    st.write(
        "API keys: "
        + ("ready" if creds["ready"] else "not configured (.env)")
    )
    if mode == "live":
        st.warning("Live mode can place real orders. Confirm below before executing.")
        confirm_live = st.checkbox("I understand — allow live order submission")
    else:
        confirm_live = False

    st.divider()
    st.header("Live params")
    min_edge = st.slider("min_edge", 0.01, 0.15, float(base_settings.min_edge), 0.01)
    shrinkage = st.slider(
        "market_shrinkage", 0.0, 0.9, float(base_settings.market_shrinkage), 0.05
    )
    lookback = st.slider(
        "Backtest lookback (days)", 7, 60, int(base_settings.backtest_lookback_days), 1
    )

    settings = with_overrides(
        base_settings,
        mode=mode,
        min_edge=float(min_edge),
        market_shrinkage=float(shrinkage),
        backtest_lookback_days=int(lookback),
    )

    run_clicked = st.button("Run live scan", type="primary", use_container_width=True)
    backtest_clicked = st.button("Run historical backtest", use_container_width=True)
    tune_clicked = st.button("Fine-tune on last backtest", use_container_width=True)

ledger = Ledger(settings.db_path)

if run_clicked:
    with st.spinner("Fetching Kalshi markets + Open-Meteo ensembles..."):
        try:
            result = run_pipeline(settings, notes=f"streamlit:{mode}")
            st.session_state["last_result"] = result
            st.success(
                f"Run #{result['run_id']}: scored {result['markets_scored']} markets — "
                f"{result['trade_signals']} trade / {result['pass_signals']} pass"
            )
            if result["errors"]:
                st.warning("\n".join(result["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Pipeline failed: {exc}")

if backtest_clicked:
    with st.spinner("Backtesting settled markets + historical forecasts (may take a minute)..."):
        try:
            bt = run_backtest(settings, lookback_days=lookback)
            # Drop heavy city objects for session state
            for c in bt.get("candidates") or []:
                c.pop("city", None)
            st.session_state["backtest"] = bt
            st.success(
                f"Backtest {bt['start_date']} → {bt['end_date']}: "
                f"{bt['wins']}W / {bt['losses']}L "
                f"(win rate {bt['win_rate']:.1%}), PnL ${bt['pnl']:.2f}"
            )
            if bt.get("errors"):
                st.warning("\n".join(bt["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")

if tune_clicked:
    bt = st.session_state.get("backtest")
    if not bt or not bt.get("candidates"):
        st.error("Run a historical backtest first.")
    else:
        with st.spinner("Grid-searching edge params on backtest universe..."):
            try:
                # Reattach minimal city fields for scoring
                from kalshi_weather_edge.config import City

                city_map = {c.id: c for c in settings.cities}
                cands = []
                for c in bt["candidates"]:
                    cc = dict(c)
                    cc["city"] = city_map.get(c["city_id"])
                    cands.append(cc)
                tuned = fine_tune(cands, settings)
                st.session_state["tune"] = tuned
                best = tuned.get("best")
                if best:
                    st.success(
                        f"Best: min_edge={best['min_edge']}, "
                        f"shrink={best['market_shrinkage']}, "
                        f"longshot_over={best['longshot_overprice_min']} → "
                        f"{best['wins']}W/{best['losses']}L "
                        f"({best['win_rate']:.1%}), PnL ${best['pnl']:.2f}"
                    )
                else:
                    st.warning("No tunable result.")
            except Exception as exc:  # noqa: BLE001
                st.error(f"Fine-tune failed: {exc}")

stats = ledger.signal_stats()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Mode", mode.upper())
c2.metric("Signals logged", stats["total_signals"])
c3.metric("Trade suggestions", stats["trade_signals"])
c4.metric("Settled (ledger)", stats["settled"])
c5.metric("Paper PnL ($)", f"{stats['paper_pnl']:.2f}")

tab_board, tab_trades, tab_backtest, tab_tune, tab_help = st.tabs(
    ["Edge board", "Trade suggestions", "Backtest W/L", "Fine-tune", "Help"]
)

result = st.session_state.get("last_result")
if result:
    df = pd.DataFrame(rows_to_dataframe_records(result))
else:
    sigs = ledger.latest_signals(limit=300)
    records = []
    for s in sigs:
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

with tab_board:
    if df.empty:
        st.info("No signals yet. Click **Run live scan**.")
    else:
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
            fig.add_shape(type="line", x0=0, y0=0, x1=1, y1=1, line=dict(dash="dash", color="gray"))
            st.plotly_chart(fig, use_container_width=True)

with tab_trades:
    if df.empty:
        st.info("No trades yet.")
    else:
        trades = df[df["action"] != "PASS"].copy()
        if trades.empty:
            st.write("No trade suggestions this scan.")
        else:
            st.dataframe(trades.sort_values("edge", ascending=False), use_container_width=True, hide_index=True)
            st.subheader("Execute suggestions")
            if mode == "paper":
                st.caption("Paper mode: execution is simulated only.")
            selected = st.multiselect(
                "Select tickers to execute",
                options=trades["ticker"].tolist(),
            )
            if st.button("Execute selected"):
                trade_map = {r["ticker"]: r for r in (result.get("rows") if result else [])}
                # Fallback reconstruct from dataframe
                outs = []
                for ticker in selected:
                    sig = trade_map.get(ticker)
                    if not sig:
                        row = trades[trades["ticker"] == ticker].iloc[0].to_dict()
                        sig = {
                            "ticker": ticker,
                            "action": row.get("action"),
                            "side": row.get("side"),
                            "execution": row.get("execution"),
                            "suggested_contracts": row.get("contracts") or 1,
                            "yes_bid": None,
                            "yes_ask": None,
                            "market_mid": row.get("market_mid"),
                        }
                    outs.append(
                        execute_signal(
                            sig,
                            settings,
                            mode=mode,
                            confirm_live=confirm_live,
                            use_demo=use_demo,
                        )
                    )
                st.json(outs)

with tab_backtest:
    bt = st.session_state.get("backtest")
    if not bt:
        st.info("Click **Run historical backtest** in the sidebar.")
    else:
        m1, m2, m3, m4, m5 = st.columns(5)
        m1.metric("Trades", bt["n_trades"])
        m2.metric("Wins", bt["wins"])
        m3.metric("Losses", bt["losses"])
        m4.metric("Win rate", f"{bt['win_rate']:.1%}")
        m5.metric("PnL ($)", f"{bt['pnl']:.2f}")
        st.caption(f"Window {bt['start_date']} → {bt['end_date']} · candidates {bt['n_candidates']}")
        tdf = pd.DataFrame(bt.get("trades") or [])
        if not tdf.empty:
            st.dataframe(tdf, use_container_width=True, hide_index=True)
            fig = px.histogram(tdf, x="pnl", color="won", title="Trade PnL distribution")
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.write("No trades under current thresholds (all PASS). Loosen min_edge or shrink.")

with tab_tune:
    tuned = st.session_state.get("tune")
    if not tuned:
        st.info("Run a backtest, then click **Fine-tune on last backtest**.")
    else:
        rdf = pd.DataFrame(tuned.get("ranked") or tuned.get("results") or [])
        st.dataframe(rdf, use_container_width=True, hide_index=True)
        best = tuned.get("best")
        if best:
            st.write("Best params:", best)
            if st.button("Apply best params to config.yaml"):
                persist_tuned_params(best)
                st.success("Saved to config.yaml — reload the app to use them as defaults.")
            if st.button("Rescore backtest with best params"):
                bt = st.session_state.get("backtest")
                if bt and bt.get("candidates"):
                    from kalshi_weather_edge.config import City

                    city_map = {c.id: c for c in settings.cities}
                    cands = []
                    for c in bt["candidates"]:
                        cc = dict(c)
                        cc["city"] = city_map.get(c["city_id"])
                        cands.append(cc)
                    best_settings = apply_best_params_to_settings(settings, best)
                    rescored = score_universe(cands, best_settings)
                    rescored["start_date"] = bt["start_date"]
                    rescored["end_date"] = bt["end_date"]
                    rescored["errors"] = bt.get("errors") or []
                    rescored["candidates"] = bt["candidates"]
                    st.session_state["backtest"] = rescored
                    st.success(
                        f"Rescored: {rescored['wins']}W/{rescored['losses']}L "
                        f"({rescored['win_rate']:.1%})"
                    )
                    st.rerun()

with tab_help:
    st.markdown(
        f"""
### Modes
- **Paper** — scan + backtest + fine-tune; no exchange orders
- **Live** — requires `.env` keys (`KALSHI_API_KEY_ID`, `KALSHI_PRIVATE_KEY_PATH`) and confirmation

### Backtest
Uses settled Kalshi markets, Open-Meteo historical forecasts, and candle mids
~{settings.backtest_entry_hours_before_close}h before close as entry prices, then counts **wins vs losses**.

### Fine-tune
Grid-searches `min_edge`, `market_shrinkage`, and `longshot_overprice_min` for best win rate then PnL.

### Caveats
Historical forecasts are point estimates (wider σ), not full archived ensembles.
Candle entry is a proxy — not identical to maker fills you would have gotten.
Past win rate does not guarantee future edge.
"""
    )
