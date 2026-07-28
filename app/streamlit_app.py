from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.express as px
import streamlit as st

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
for path in (SRC, ROOT):
    entry = str(path)
    if entry not in sys.path:
        sys.path.insert(0, entry)

st.set_page_config(page_title="Kalshi Favorites", page_icon="📈", layout="wide")

try:
    from kalshi_weather_edge.alerts import maybe_alert_scan  # noqa: E402
    from kalshi_weather_edge.config import (  # noqa: E402
        alert_webhook_url,
        live_credentials_configured,
        load_settings,
        save_favorites_thresholds,
        save_mode_to_config,
        thresholds_for_series,
        with_overrides,
    )
    from kalshi_weather_edge.db import Ledger  # noqa: E402
    from kalshi_weather_edge.favorites_pipeline import (  # noqa: E402
        rows_to_dataframe_records,
        run_consensus_scan,
    )
    from kalshi_weather_edge.kalshi_client import KalshiClient  # noqa: E402
    from kalshi_weather_edge.settlements import sync_settlements_for_open_signals  # noqa: E402
    from kalshi_weather_edge.trading import execute_signal  # noqa: E402
except ImportError as exc:
    st.error(f"Failed to load app modules: {exc}")
    st.stop()


def _run_strategy_backtest(settings, base_settings):
    from datetime import date

    from kalshi_weather_edge.hit_rate_scan import (  # noqa: E402
        backtest_strategy_profiles,
        build_candidates,
    )

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
    return {**universe, "results": bt_results}


def _load_latest_scan() -> dict | None:
    path = ROOT / "data" / "scans" / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


st.title("Kalshi Favorites")
st.caption(
    "Series-aware consensus scanner with settlements, equity board, and scheduled alerts. "
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
    st.write(
        "Alert webhook: "
        + ("ready" if alert_webhook_url() else "not set (ALERT_WEBHOOK_URL)")
    )
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
        format_func=lambda s: (
            "Favorites (tight)" if s == "favorites" else "High profit (series-aware)"
        ),
    )

    if strategy == "high_profit":
        yes_thr = float(base_settings.high_profit_yes_threshold)
        no_thr = float(base_settings.high_profit_no_threshold)
        contracts = int(base_settings.high_profit_contracts)
        series_list = base_settings.high_profit_series
        st.caption("Per-series overrides from config:")
        for s in series_list:
            y, n = thresholds_for_series(base_settings, "high_profit", s)
            st.caption(f"`{s}` YES≥{y:.2f} NO≤{n:.2f}")
    else:
        yes_thr = float(base_settings.favorites_yes_threshold)
        no_thr = float(base_settings.favorites_no_threshold)
        contracts = int(base_settings.favorites_contracts)
        series_list = base_settings.favorites_series
        st.caption(f"Global YES≥{yes_thr:.2f} · NO≤{no_thr:.2f}")

    if strategy == "favorites":
        yes_thr = st.slider("Buy YES when mid >=", 0.80, 0.99, yes_thr, 0.01)
        no_thr = st.slider("Buy NO when mid <=", 0.01, 0.20, no_thr, 0.01)
    else:
        st.info("High profit uses series overrides; sliders adjust global fallback only.")
        yes_thr = st.slider("Fallback YES >=", 0.80, 0.99, yes_thr, 0.01)
        no_thr = st.slider("Fallback NO <=", 0.05, 0.40, no_thr, 0.01)

    contracts = st.number_input("Contracts per signal", 1, 25, contracts)
    top_n = st.slider("Show top N signals", 3, 25, 10)

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
    settle_clicked = st.button("Sync settlements", use_container_width=True)
    backtest_clicked = st.button("Run strategy backtest", use_container_width=True)
    alert_clicked = st.button("Send alert for last scan", use_container_width=True)

ledger = Ledger(settings.db_path)

if scan_clicked:
    with st.spinner("Scanning Kalshi open markets + settlements..."):
        try:
            result = run_consensus_scan(
                settings, strategy=strategy, notes=f"streamlit:{mode}", sync_settlements=True
            )
            st.session_state["last_result"] = result
            st.session_state["last_strategy"] = strategy
            settle = result.get("settlements") or {}
            st.success(
                f"Run #{result['run_id']}: {result['trade_signals']} signals / "
                f"{result['pass_signals']} pass · settlements updated {settle.get('signals_updated', 0)}"
            )
            if result["errors"]:
                st.warning("\n".join(result["errors"]))
        except Exception as exc:  # noqa: BLE001
            st.error(f"Scan failed: {exc}")

if settle_clicked:
    with st.spinner("Fetching settlements for open paper trades..."):
        try:
            client = KalshiClient(settings.kalshi_base_url)
            info = sync_settlements_for_open_signals(
                ledger,
                client,
                series_list=sorted(set(base_settings.favorites_series + base_settings.high_profit_series)),
            )
            st.success(
                f"Checked {info['tickers_checked']} tickers · "
                f"upserted {info['settlements_upserted']} · "
                f"updated {info['signals_updated']} signals"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Settlement sync failed: {exc}")

if backtest_clicked:
    with st.spinner("Backtesting favorites vs high_profit on settled history..."):
        try:
            bt_payload = _run_strategy_backtest(settings, base_settings)
            st.session_state["backtest"] = bt_payload
            hp = next(r for r in bt_payload["results"]["profiles"] if r["name"] == "high_profit")
            st.success(
                f"High profit: {hp['wins']}W/{hp['losses']}L ({hp['win_rate']:.1%}), "
                f"avg ${hp['avg_pnl']:.3f}/contract, total ${hp['pnl']:.2f}"
            )
        except Exception as exc:  # noqa: BLE001
            st.error(f"Backtest failed: {exc}")

if alert_clicked:
    result = st.session_state.get("last_result")
    if not result:
        st.warning("Run a scan first.")
    else:
        alert = maybe_alert_scan(
            settings,
            strategy=strategy,
            rows=result.get("rows") or [],
            settled_updated=int((result.get("settlements") or {}).get("signals_updated") or 0),
            paper_pnl=float((result.get("stats") or {}).get("paper_pnl") or 0),
        )
        if alert.get("ok"):
            st.success(f"Alert sent ({alert.get('n', 0)} signals)")
        else:
            st.info(alert.get("reason") or alert.get("error") or str(alert))

stats = ledger.signal_stats()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Mode", mode.upper())
c2.metric("Strategy", strategy.replace("_", " ").title())
c3.metric("Open trades", stats.get("open_trades", 0))
c4.metric(
    "Settled W/L",
    f"{stats.get('wins', 0)}/{stats.get('losses', 0)}",
    f"{stats.get('win_rate', 0):.0%} WR" if (stats.get("wins", 0) + stats.get("losses", 0)) else None,
)
c5.metric("Paper PnL ($)", f"{stats['paper_pnl']:.2f}")

result = st.session_state.get("last_result")
latest_scan = _load_latest_scan()
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
                "strategy": meta.get("strategy"),
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
                "spread": s.get("spread"),
                "edge": s.get("edge"),
                "contracts": s.get("suggested_contracts"),
                "reason": s.get("reason"),
            }
        )
    df = pd.DataFrame(records)

tab_signals, tab_perf, tab_backtest, tab_help = st.tabs(
    ["Live signals", "Performance", "Backtest", "Help"]
)

with tab_signals:
    if latest_scan and not result:
        st.caption(f"Last scheduled scan: {latest_scan.get('scanned_at', '?')}")

    if df.empty:
        st.info("Click **Scan open markets** in the sidebar (or wait for the GitHub Action).")
    else:
        show = df.copy()
        show["market_mid"] = pd.to_numeric(show.get("market_mid"), errors="coerce")
        show["edge"] = pd.to_numeric(show.get("edge"), errors="coerce")
        trades = show[show["action"] != "PASS"].copy()
        if not trades.empty:
            trades = trades.sort_values("edge", key=lambda s: s.abs(), ascending=False)
            st.subheader(f"Top {min(top_n, len(trades))} actionable signals")
            st.dataframe(trades.head(top_n), use_container_width=True, hide_index=True)

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
        else:
            st.info("No trade signals under current thresholds.")

        with st.expander("All scored markets"):
            st.dataframe(
                show.sort_values(["action", "market_mid"], ascending=[True, False]),
                use_container_width=True,
                hide_index=True,
            )

with tab_perf:
    board = ledger.performance_board()
    st.write(
        f"Settled equity **${board['final_equity']:.2f}** · "
        f"{stats.get('wins', 0)}W / {stats.get('losses', 0)}L · "
        f"{stats.get('open_trades', 0)} still open"
    )
    if board["equity_curve"]:
        eq = pd.DataFrame(board["equity_curve"])
        fig = px.line(eq, x="t", y="equity", title="Paper equity curve (settled trades)")
        st.plotly_chart(fig, use_container_width=True)
    if board["by_series"]:
        st.subheader("By series")
        st.dataframe(pd.DataFrame(board["by_series"]), use_container_width=True, hide_index=True)
    if board["trades"]:
        st.subheader("Recent settled trades")
        st.dataframe(pd.DataFrame(board["trades"]), use_container_width=True, hide_index=True)
    else:
        st.info("No settled paper trades yet. Scan, wait for markets to resolve, then **Sync settlements**.")

with tab_backtest:
    bt = st.session_state.get("backtest")
    saved_files = sorted((ROOT / "data" / "backtests").glob("strategy_profiles_*.json"))
    saved_path = saved_files[-1] if saved_files else None

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
    ov = base_settings.high_profit_series_overrides
    ov_lines = "\n".join(
        f"- `{s}`: YES≥{t.yes_threshold:.2f} / NO≤{t.no_threshold:.2f}" for s, t in ov.items()
    )
    st.markdown(
        f"""
### Strategy profiles

| Profile | Behavior |
|---------|----------|
| **Favorites** | Global YES≥{base_settings.favorites_yes_threshold:.2f} / NO≤{base_settings.favorites_no_threshold:.2f} |
| **High profit** | Series-aware (EUR NO≤0.30); skips NFP/CPI |

**High-profit overrides**
{ov_lines or '- (none)'}

### Risk controls
- Max {base_settings.max_trades_per_event} trades per event
- Skip spreads wider than {base_settings.max_spread:.2f}

### Scheduled scans
GitHub Action runs every 30 minutes (`scheduled_scan.yml`).
Set repo secret `ALERT_WEBHOOK_URL` for Discord/Slack alerts.

### CLI
```
python scripts/run_pipeline.py --strategy high_profit
python scripts/scheduled_scan.py --strategy both
python scripts/backtest_strategies.py
```
"""
    )
