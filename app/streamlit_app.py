from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import (  # noqa: E402
    live_credentials_configured,
    load_settings,
    save_favorites_thresholds,
    save_mode_to_config,
    with_overrides,
)
from kalshi_weather_edge.db import Ledger  # noqa: E402
from kalshi_weather_edge.favorites_pipeline import (  # noqa: E402
    rows_to_dataframe_records,
    run_consensus_scan,
)
from kalshi_weather_edge.hit_rate_scan import (  # noqa: E402
    backtest_strategy_profiles,
    build_candidates,
)
from kalshi_weather_edge.trading import execute_signal  # noqa: E402


st.set_page_config(page_title="Kalshi Favorites", page_icon="📈", layout="wide")

st.title("Kalshi Favorites")
st.caption(
    "Consensus scanners: tight favorites (high hit rate) or high-profit (looser NO band). "
    "Paper by default. Not financial advice."
)

base_settings = load_settings()
creds = live_credentials_configured()

with st.sidebar:
    st.header("Trading mode")
    mode = st.radio(
        "Mode",
        options=["paper", "live"],
        index=0 if base_settings.mode != "live" else 1,
        horizontal=True,
    )
    if mode != base_settings.mode:
        if st.button("Save mode to config.yaml"):
            save_mode_to_config(mode)
            st.success(f"Saved mode={mode}")
            st.rerun()

    use_demo = st.checkbox("Use Kalshi DEMO API", value=creds.get("env") == "demo")
    st.write("API keys: " + ("ready" if creds["ready"] else "not configured (.env)"))
    if mode == "live":
        st.warning("Live mode can place real orders.")
        confirm_live = st.checkbox("I understand — allow live order submission")
    else:
        confirm_live = False

    st.divider()
    st.header("Strategy")
    strategy = st.radio(
        "Profile",
        options=["favorites", "high_profit"],
        index=0 if base_settings.strategy != "high_profit" else 1,
        format_func=lambda s: "Favorites (0.90 / 0.10)" if s == "favorites" else "High profit (0.90 / 0.30)",
    )

    if strategy == "high_profit":
        yes_thr = float(base_settings.high_profit_yes_threshold)
        no_thr = float(base_settings.high_profit_no_threshold)
        contracts = int(base_settings.high_profit_contracts)
        series_list = base_settings.high_profit_series
    else:
        yes_thr = float(base_settings.favorites_yes_threshold)
        no_thr = float(base_settings.favorites_no_threshold)
        contracts = int(base_settings.favorites_contracts)
        series_list = base_settings.favorites_series

    st.caption(f"YES >= {yes_thr:.2f} · NO <= {no_thr:.2f} · {len(series_list)} series")

    if strategy == "favorites":
        yes_thr = st.slider("Buy YES when mid >=", 0.80, 0.99, yes_thr, 0.01)
        no_thr = st.slider("Buy NO when mid <=", 0.01, 0.20, no_thr, 0.01)
    else:
        yes_thr = st.slider("Buy YES when mid >=", 0.80, 0.99, yes_thr, 0.01)
        no_thr = st.slider("Buy NO when mid <=", 0.05, 0.40, no_thr, 0.01)

    contracts = st.number_input("Contracts per signal", 1, 25, contracts)

    if strategy == "favorites":
        settings = with_overrides(
            base_settings,
            mode=mode,
            strategy=strategy,
            favorites_yes_threshold=float(yes_thr),
            favorites_no_threshold=float(no_thr),
            favorites_contracts=float(contracts),
        )
    else:
        settings = with_overrides(
            base_settings,
            mode=mode,
            strategy=strategy,
            high_profit_yes_threshold=float(yes_thr),
            high_profit_no_threshold=float(no_thr),
            high_profit_contracts=float(contracts),
        )

    if strategy == "favorites" and st.button("Save favorites thresholds to config"):
        save_favorites_thresholds(yes_thr, no_thr)
        st.success("Saved favorites thresholds")

    st.divider()
    scan_clicked = st.button("Scan open markets", type="primary", use_container_width=True)
    backtest_clicked = st.button("Run strategy backtest", use_container_width=True)

ledger = Ledger(settings.db_path)

if scan_clicked:
    with st.spinner("Scanning Kalshi open markets..."):
        try:
            result = run_consensus_scan(settings, strategy=strategy, notes=f"streamlit:{mode}")
            st.session_state["last_result"] = result
            st.session_state["last_strategy"] = strategy
            st.success(
                f"Run #{result['run_id']}: {result['markets_scored']} markets — "
                f"{result['trade_signals']} signals / {result['pass_signals']} pass"
            )
            if result["errors"]:
                st.warning("\n".join(result["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scan failed: {exc}")

if backtest_clicked:
    with st.spinner("Backtesting favorites vs high_profit on settled history..."):
        try:
            from datetime import date

            year = date.today().year
            all_series = sorted(set(base_settings.favorites_series + base_settings.high_profit_series))
            universe = build_candidates(
                all_series,
                settings,
                start_date=f"{year}-01-01",
                max_markets_per_series=settings.backtest_max_markets_per_series,
                entry_hours_before_close=settings.backtest_entry_hours_before_close,
            )
            profiles = [
                {
                    "name": "favorites",
                    "yes_threshold": base_settings.favorites_yes_threshold,
                    "no_threshold": base_settings.favorites_no_threshold,
                    "series": base_settings.favorites_series,
                },
                {
                    "name": "high_profit",
                    "yes_threshold": base_settings.high_profit_yes_threshold,
                    "no_threshold": base_settings.high_profit_no_threshold,
                    "series": base_settings.high_profit_series,
                },
            ]
            bt_results = backtest_strategy_profiles(universe["candidates"], profiles)
            st.session_state["backtest"] = {**universe, "results": bt_results}
            hp = next(r for r in bt_results["profiles"] if r["name"] == "high_profit")
            st.success(
                f"High profit: {hp['wins']}W/{hp['losses']}L ({hp['win_rate']:.1%}), "
                f"avg ${hp['avg_pnl']:.3f}/contract, total ${hp['pnl']:.2f}"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")

stats = ledger.signal_stats()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Mode", mode.upper())
c2.metric("Strategy", strategy.replace("_", " ").title())
c3.metric("Signals logged", stats["total_signals"])
c4.metric("Paper PnL ($)", f"{stats['paper_pnl']:.2f}")

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
                "series": meta.get("series") or s.get("city_id"),
                "ticker": s.get("ticker"),
                "title": meta.get("title"),
                "subtitle": meta.get("subtitle"),
                "action": s.get("action"),
                "side": s.get("side"),
                "execution": s.get("execution"),
                "market_mid": s.get("market_mid"),
                "yes_bid": s.get("yes_bid"),
                "yes_ask": s.get("yes_ask"),
                "edge": s.get("edge"),
                "contracts": s.get("suggested_contracts"),
                "reason": s.get("reason"),
            }
        )
    df = pd.DataFrame(records)

tab_signals, tab_backtest, tab_help = st.tabs(["Live signals", "Backtest results", "Help"])

with tab_signals:
    if df.empty:
        st.info("Click **Scan open markets** in the sidebar.")
    else:
        show = df.copy()
        show["market_mid"] = pd.to_numeric(show["market_mid"], errors="coerce")
        st.dataframe(
            show.sort_values(["action", "market_mid"], ascending=[True, False]),
            use_container_width=True,
            hide_index=True,
        )
        trades = show[show["action"] != "PASS"]
        if not trades.empty:
            fig = px.histogram(
                trades,
                x="market_mid",
                color="action",
                title="Signal distribution by market mid",
            )
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Execute selected (paper or live)")
            selected = st.multiselect("Tickers", options=trades["ticker"].tolist())
            if st.button("Execute selected"):
                trade_map = {r["ticker"]: r for r in (result.get("rows") if result else [])}
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
                            "yes_bid": row.get("yes_bid"),
                            "yes_ask": row.get("yes_ask"),
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
    saved_path = ROOT / "data" / "backtests" / "strategy_profiles_2026-07-28.json"
    if not saved_path.exists():
        saved_path = sorted((ROOT / "data" / "backtests").glob("strategy_profiles_*.json"))[-1:] 
        saved_path = saved_path[0] if saved_path else None

    if bt and bt.get("results"):
        st.write(
            f"Window **{bt.get('data_start')} -> {bt.get('data_end')}** · "
            f"{bt.get('n_candidates')} candidates"
        )
        profiles = bt["results"].get("profiles") or []
        st.dataframe(pd.DataFrame(profiles), use_container_width=True, hide_index=True)
        for name, rows in (bt["results"].get("per_series") or {}).items():
            st.subheader(f"{name} — per series")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    elif saved_path and saved_path.exists():
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        st.write(f"Saved backtest: `{saved_path.name}`")
        st.dataframe(pd.DataFrame(saved["results"]["profiles"]), use_container_width=True, hide_index=True)
        for name, rows in saved["results"].get("per_series", {}).items():
            st.subheader(f"{name} — per series")
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
    else:
        st.info("Click **Run strategy backtest** in the sidebar.")

with tab_help:
    st.markdown(
        f"""
### Strategy profiles

| Profile | BUY YES | BUY NO | Goal |
|---------|---------|--------|------|
| **Favorites** | mid >= {base_settings.favorites_yes_threshold:.2f} | mid <= {base_settings.favorites_no_threshold:.2f} | Highest hit rate |
| **High profit** | mid >= {base_settings.high_profit_yes_threshold:.2f} | mid <= {base_settings.high_profit_no_threshold:.2f} | More $/contract |

Both trade **with** market consensus on bracket markets — not against it.

### Series
- Favorites: {', '.join(f'`{s}`' for s in base_settings.favorites_series)}
- High profit: {', '.join(f'`{s}`' for s in base_settings.high_profit_series)}

### CLI
```
python scripts/run_pipeline.py
python scripts/run_pipeline.py --strategy high_profit
python scripts/backtest_strategies.py
```
"""
    )
