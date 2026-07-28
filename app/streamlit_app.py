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
    from kalshi_weather_edge.alerts import maybe_alert_scan, webhook_ready  # noqa: E402
    from kalshi_weather_edge.config import (  # noqa: E402
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
    st.info(
        "If this is Streamlit Cloud, reboot the app after the latest GitHub push "
        "so it picks up new package files."
    )
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
    "Finds markets where the crowd already agrees strongly — then suggests following that side. "
    "Starts in paper mode (practice only). Not financial advice."
)

with st.expander("New here? Read this first (plain English)", expanded=False):
    st.markdown(
        """
### What this app does
Kalshi markets are like yes/no questions (example: “Will gas be above $4.10?”).  
The **mid** price is roughly the market’s implied chance — `0.90` means “about 90% likely YES.”

This app looks for **extreme** prices and suggests:
- **BUY YES** when the market says YES is very likely
- **BUY NO** when the market says YES is very unlikely (so NO is the favorite)

You are **not** trying to outsmart the market — you are following strong consensus.

### Key words
| Term | Meaning |
|------|---------|
| **Paper mode** | Practice only. No real money. Safe default. |
| **Live mode** | Can send real orders (needs API keys + your confirmation). |
| **Mid** | Average of buy/sell prices ≈ implied probability (0 to 1). |
| **BUY YES / BUY NO** | Which side of the contract to buy. |
| **Contracts** | How many $1 contracts to size each suggestion. |
| **Edge** | How extreme the mid is vs 50/50 (higher = more one-sided). |
| **Settlement** | When Kalshi decides the winner; we then score paper wins/losses. |
| **Hit rate / W–L** | How often settled paper trades were correct. |
| **Alert** | Optional Discord/Slack ping with top signals — does **not** place trades. |

### Suggested workflow
1. Keep **Paper** mode on.
2. Pick a strategy profile (Favorites = safer/tighter; High profit = a bit looser).
3. Click **Scan open markets**.
4. Review the **Top signals** table.
5. Later, click **Sync settlements** to update wins/losses and paper PnL.
"""
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
        help=(
            "Paper = practice only, logs suggestions without real orders. "
            "Live = can place real Kalshi orders (dangerous until you know what you're doing)."
        ),
    )
    if mode != base_settings.mode:
        if st.button(
            "Save mode to config.yaml",
            help="Writes paper/live into config.yaml so the next restart remembers your choice.",
        ):
            save_mode_to_config(mode)
            st.success(f"Saved mode={mode}")
            st.rerun()

    use_demo = st.checkbox(
        "Use Kalshi DEMO API",
        value=creds.get("env") == "demo",
        help=(
            "Use Kalshi's practice/demo exchange instead of real production markets. "
            "Still separate from Paper mode in this app."
        ),
    )
    st.caption("API keys: " + ("ready" if creds["ready"] else "not configured (.env)"))
    st.caption(
        "Alert webhook: " + ("ready" if webhook_ready() else "not set (ALERT_WEBHOOK_URL)")
    )
    if mode == "live":
        st.warning("Live mode can place real orders.")
        confirm_live = st.checkbox(
            "I understand — allow live order submission",
            help="Extra safety lock. Even in Live mode, orders only send if this is checked.",
        )
    else:
        confirm_live = False

    st.divider()
    st.header("Strategy")
    strategy = st.radio(
        "Profile",
        options=["favorites", "high_profit"],
        index=0 if base_settings.strategy != "high_profit" else 1,
        format_func=lambda s: (
            "Favorites (safer / tighter)" if s == "favorites" else "High profit (series-aware)"
        ),
        help=(
            "Favorites only takes very extreme prices (fewer trades, higher hit rate). "
            "High profit loosens some markets (especially EUR/USD) to earn a bit more per win, "
            "with a slightly lower hit rate. High profit skips NFP/CPI."
        ),
    )

    if strategy == "high_profit":
        yes_thr = float(base_settings.high_profit_yes_threshold)
        no_thr = float(base_settings.high_profit_no_threshold)
        contracts = int(base_settings.high_profit_contracts)
        series_list = base_settings.high_profit_series
        st.caption("Per-market rules (from config):")
        for s in series_list:
            y, n = thresholds_for_series(base_settings, "high_profit", s)
            st.caption(f"`{s}` buy YES if mid≥{y:.0%} · buy NO if mid≤{n:.0%}")
    else:
        yes_thr = float(base_settings.favorites_yes_threshold)
        no_thr = float(base_settings.favorites_no_threshold)
        contracts = int(base_settings.favorites_contracts)
        series_list = base_settings.favorites_series
        st.caption(
            f"Buy YES if market says ≥{yes_thr:.0%} · "
            f"Buy NO if market says YES ≤{no_thr:.0%}"
        )

    yes_help = (
        "Only suggest BUY YES when the market mid is at least this high. "
        "Example: 0.90 means “market thinks YES is about 90%+ likely.” "
        "Higher = fewer, more one-sided YES bets."
    )
    no_help = (
        "Only suggest BUY NO when the YES mid is at most this low. "
        "Example: 0.10 means “market thinks YES is about 10% or less,” so NO is the favorite. "
        "Lower = fewer, more one-sided NO bets."
    )

    if strategy == "favorites":
        yes_thr = st.slider(
            "Buy YES when mid ≥",
            0.80,
            0.99,
            yes_thr,
            0.01,
            help=yes_help,
        )
        no_thr = st.slider(
            "Buy NO when mid ≤",
            0.01,
            0.20,
            no_thr,
            0.01,
            help=no_help,
        )
    else:
        st.info(
            "High profit mainly uses per-series rules above. "
            "These sliders only change the fallback for series without their own override."
        )
        yes_thr = st.slider("Fallback YES ≥", 0.80, 0.99, yes_thr, 0.01, help=yes_help)
        no_thr = st.slider("Fallback NO ≤", 0.05, 0.40, no_thr, 0.01, help=no_help)

    contracts = st.number_input(
        "Contracts per signal",
        1,
        25,
        contracts,
        help=(
            "How many contracts each suggestion is sized for. "
            "Each contract pays $1 if you win. Start with 1 while learning."
        ),
    )
    top_n = st.slider(
        "Show top N signals",
        3,
        25,
        10,
        help="How many of the strongest suggestions to highlight in the main table.",
    )

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

    if strategy == "favorites" and st.button(
        "Save favorites thresholds to config",
        help="Writes the YES/NO sliders into config.yaml for next time.",
    ):
        save_favorites_thresholds(yes_thr, no_thr)
        st.success("Saved favorites thresholds")

    st.divider()
    st.subheader("Actions")
    scan_clicked = st.button(
        "Scan open markets",
        type="primary",
        use_container_width=True,
        help="Pull live Kalshi markets, apply your strategy rules, and try to update settled paper trades.",
    )
    settle_clicked = st.button(
        "Sync settlements",
        use_container_width=True,
        help=(
            "Ask Kalshi which of your open paper trades have finished, then mark wins/losses "
            "and update paper PnL."
        ),
    )
    backtest_clicked = st.button(
        "Run strategy backtest",
        use_container_width=True,
        help="Replay past settled markets to compare Favorites vs High profit (can take a few minutes).",
    )
    alert_clicked = st.button(
        "Send alert for last scan",
        use_container_width=True,
        help=(
            "Text your Discord/Slack webhook with the top signals from the last scan. "
            "Does not place any trades."
        ),
    )

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
                series_list=sorted(
                    set(base_settings.favorites_series + base_settings.high_profit_series)
                ),
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
        if alert.get("ok") and not alert.get("skipped"):
            st.success(f"Alert sent ({alert.get('n', 0)} signals)")
        else:
            st.info(alert.get("reason") or alert.get("error") or str(alert))

stats = ledger.signal_stats()
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Mode", mode.upper(), help="Paper = practice. Live = real orders possible.")
c2.metric(
    "Strategy",
    strategy.replace("_", " ").title(),
    help="Which rule set is used when you scan.",
)
c3.metric(
    "Open trades",
    stats.get("open_trades", 0),
    help="Paper suggestions that have not settled yet.",
)
c4.metric(
    "Settled W/L",
    f"{stats.get('wins', 0)}/{stats.get('losses', 0)}",
    f"{stats.get('win_rate', 0):.0%} WR"
    if (stats.get("wins", 0) + stats.get("losses", 0))
    else None,
    help="Wins and losses after markets resolved (paper).",
)
c5.metric(
    "Paper PnL ($)",
    f"{stats['paper_pnl']:.2f}",
    help="Estimated practice profit/loss from settled signals only.",
)

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
        st.caption(
            f"Last scheduled scan: {latest_scan.get('scanned_at', '?')} "
            "(from GitHub Action / CLI). Click Scan to refresh live."
        )

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
            st.caption(
                "These are the strongest suggestions. "
                "**PASS** rows are skipped markets that did not meet your thresholds."
            )
            st.dataframe(trades.head(top_n), use_container_width=True, hide_index=True)

            fig = px.histogram(
                trades,
                x="market_mid",
                color="action",
                title="Where signals sit on the probability scale (mid)",
            )
            st.plotly_chart(fig, use_container_width=True)

            st.subheader("Execute selected (paper or live)")
            st.caption(
                "In Paper mode this only logs that you “took” the idea. "
                "In Live mode (with confirmation) it can send real limit orders."
            )
            selected = st.multiselect(
                "Tickers",
                options=trades["ticker"].tolist(),
                help="Pick one or more market tickers from the top signals list.",
            )
            if st.button("Execute selected", help="Run paper or live execution for the tickers you picked."):
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
            st.info("No trade signals under current thresholds — try High profit or loosen the sliders a bit.")

        with st.expander("All scored markets (including PASS)"):
            st.dataframe(
                show.sort_values(["action", "market_mid"], ascending=[True, False]),
                use_container_width=True,
                hide_index=True,
            )

with tab_perf:
    st.caption(
        "This board only updates after markets settle and you sync settlements "
        "(or after a scan that syncs automatically)."
    )
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
        st.caption("Which market types (gas, EUR/USD, etc.) are helping or hurting paper PnL.")
        st.dataframe(pd.DataFrame(board["by_series"]), use_container_width=True, hide_index=True)
    if board["trades"]:
        st.subheader("Recent settled trades")
        st.dataframe(pd.DataFrame(board["trades"]), use_container_width=True, hide_index=True)
    else:
        st.info(
            "No settled paper trades yet. Scan, wait for markets to resolve, then **Sync settlements**."
        )

with tab_backtest:
    st.caption(
        "Backtests replay history to estimate hit rate and $/contract. "
        "Past results do not guarantee future results."
    )
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
- Max {base_settings.max_trades_per_event} trades per event (avoids buying every dead bracket)
- Skip spreads wider than {base_settings.max_spread:.2f} (thin / hard-to-trade books)

### Alerts
Optional Discord/Slack messages with top signals. They **do not** place bets.  
Set `ALERT_WEBHOOK_URL` in `.env` or as a GitHub Actions secret.

### Scheduled scans
GitHub Action runs every 30 minutes (`.github/workflows/scheduled_scan.yml`).

### CLI
```
python scripts/run_pipeline.py --strategy high_profit
python scripts/scheduled_scan.py --strategy both
python scripts/backtest_strategies.py
```
"""
    )
