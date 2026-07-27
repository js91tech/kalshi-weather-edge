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
    run_favorites_scan,
)
from kalshi_weather_edge.hit_rate_scan import (  # noqa: E402
    FAST_SERIES,
    build_candidates,
    hunt_hit_rates,
)
from kalshi_weather_edge.trading import execute_signal  # noqa: E402


st.set_page_config(page_title="Kalshi Favorites", page_icon="📈", layout="wide")

st.title("Kalshi Favorites")
st.caption(
    "High hit-rate scanner: buy YES when market is very bullish, buy NO when very bearish. "
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
    st.header("Favorites thresholds")
    yes_thr = st.slider("Buy YES when mid >=", 0.80, 0.99, float(base_settings.favorites_yes_threshold), 0.01)
    no_thr = st.slider("Buy NO when mid <=", 0.01, 0.20, float(base_settings.favorites_no_threshold), 0.01)
    contracts = st.number_input("Contracts per signal", 1, 25, int(base_settings.favorites_contracts))

    settings = with_overrides(
        base_settings,
        mode=mode,
        favorites_yes_threshold=float(yes_thr),
        favorites_no_threshold=float(no_thr),
        favorites_contracts=float(contracts),
    )

    if st.button("Save thresholds to config"):
        save_favorites_thresholds(yes_thr, no_thr)
        st.success("Saved thresholds")

    st.divider()
    scan_clicked = st.button("Scan open markets", type="primary", use_container_width=True)
    backtest_clicked = st.button("Run hit-rate backtest", use_container_width=True)

ledger = Ledger(settings.db_path)

if scan_clicked:
    with st.spinner("Scanning Kalshi open markets..."):
        try:
            result = run_favorites_scan(settings, notes=f"streamlit:{mode}")
            st.session_state["last_result"] = result
            st.success(
                f"Run #{result['run_id']}: {result['markets_scored']} markets — "
                f"{result['trade_signals']} signals / {result['pass_signals']} pass"
            )
            if result["errors"]:
                st.warning("\n".join(result["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scan failed: {exc}")

if backtest_clicked:
    with st.spinner("Backtesting settled history (may take a few minutes)..."):
        try:
            from datetime import date

            year = date.today().year
            universe = build_candidates(
                settings.favorites_series or FAST_SERIES,
                settings,
                start_date=f"{year}-01-01",
                max_markets_per_series=settings.backtest_max_markets_per_series,
                entry_hours_before_close=settings.backtest_entry_hours_before_close,
            )
            hunt = hunt_hit_rates(universe["candidates"], min_trades=25)
            st.session_state["backtest"] = {
                **universe,
                "hunt": hunt,
                "yes_threshold": yes_thr,
                "no_threshold": no_thr,
            }
            best = hunt.get("best_pnl_among_hit_rate_ge_70") or hunt.get("best")
            if best:
                st.success(
                    f"Best >=70% hit-rate: {best['wins']}W/{best['losses']}L "
                    f"({best['win_rate']:.1%}), PnL ${best['pnl']:.2f}"
                )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")

stats = ledger.signal_stats()
c1, c2, c3, c4 = st.columns(4)
c1.metric("Mode", mode.upper())
c2.metric("Signals logged", stats["total_signals"])
c3.metric("Trade signals", stats["trade_signals"])
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
    hunt_path = ROOT / "data" / "backtests" / "hit_rate_hunt_2026-07-27.json"

    if bt:
        st.write(
            f"Window **{bt.get('data_start')} -> {bt.get('data_end')}** · "
            f"{bt.get('n_candidates')} candidates"
        )
        hunt = bt.get("hunt") or {}
        best = hunt.get("best_pnl_among_hit_rate_ge_70") or hunt.get("best")
        if best:
            st.success(
                f"**{best['name']}** — {best['wins']}W/{best['losses']}L "
                f"({best['win_rate']:.1%}), PnL ${best['pnl']:.2f}"
            )
        hdf = pd.DataFrame(hunt.get("all_high_hit") or hunt.get("ranked") or [])
        if not hdf.empty:
            st.dataframe(hdf, use_container_width=True, hide_index=True)
    elif hunt_path.exists():
        hunt = json.loads(hunt_path.read_text(encoding="utf-8"))
        st.write(
            f"Saved results: **{hunt.get('data_start')} -> {hunt.get('data_end')}** · "
            f"{hunt.get('n_candidates')} candidates"
        )
        h = hunt.get("hunt") or {}
        best = h.get("best_pnl_among_hit_rate_ge_70") or h.get("best")
        if best:
            st.success(
                f"**{best['name']}** — {best['wins']}W/{best['losses']}L "
                f"({best['win_rate']:.1%}), PnL ${best['pnl']:.2f}"
            )
        st.dataframe(pd.DataFrame(h.get("all_high_hit") or []), use_container_width=True, hide_index=True)
    else:
        st.info("Click **Run hit-rate backtest** in the sidebar.")

with tab_help:
    st.markdown(
        f"""
### Strategy: Favorites
Trade **with** extreme market consensus — not against it.

| Signal | Rule |
|--------|------|
| **BUY YES** | Market mid >= {yes_thr:.2f} |
| **BUY NO** | Market mid <= {no_thr:.2f} |
| **PASS** | Everything else |

### Series scanned
{', '.join(f'`{s}`' for s in settings.favorites_series)}

### Backtest results (2026 YTD)
- ALL markets, buy NO when mid <= 0.30: **95.3%** hit rate, +$73.69 paper PnL
- Gas YES when mid >= 0.95: **99.3%** hit rate
- Small $ per contract — high hit rate != huge edge

### Modes
- **Paper** — scan + log only
- **Live** — needs `.env` keys + confirmation checkbox

### CLI
```
python scripts/run_pipeline.py
python scripts/hunt_hit_rates.py --fast
```
"""
    )
