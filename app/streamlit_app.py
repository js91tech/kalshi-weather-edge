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
        active_fee_rate,
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
    from kalshi_weather_edge.ledger_snapshot import (  # noqa: E402
        default_snapshot_path,
        export_ledger_snapshot,
        import_if_newer,
        import_ledger_snapshot,
    )
    from kalshi_weather_edge import session_auth as _session_auth  # noqa: E402
    from kalshi_weather_edge.settlements import sync_settlements_for_open_signals  # noqa: E402
    from kalshi_weather_edge.trading import execute_signal  # noqa: E402

    connect_from_env = _session_auth.connect_from_env
    connect_kalshi_account = _session_auth.connect_kalshi_account
    refresh_balance = _session_auth.refresh_balance
    # Password login may be missing on a stale Cloud install — fall back gracefully.
    connect_with_password = getattr(_session_auth, "connect_with_password", None)
    if connect_with_password is None:

        def connect_with_password(*_a, **_k):  # type: ignore[misc]
            raise RuntimeError(
                "Password login is unavailable on this deploy. "
                "Reboot the Streamlit app, or use the API key tab. "
                f"(session_auth exports: {', '.join(sorted(x for x in dir(_session_auth) if not x.startswith('_')))})"
            )
except ImportError as exc:
    st.error(f"Failed to load app modules: {exc}")
    st.info(
        "If this is Streamlit Cloud, reboot the app after the latest GitHub push "
        "so it picks up new package files. Also confirm the deploy branch is **main**."
    )
    st.stop()


def _run_strategy_backtest(settings, base_settings, *, entry_hours: int, fee_rate: float):
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
        entry_hours_before_close=entry_hours,
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
    bt_results = backtest_strategy_profiles(
        universe["candidates"], profiles, fee_rate=fee_rate
    )
    return {
        **universe,
        "results": bt_results,
        "entry_hours_before_close": entry_hours,
        "fee_rate": fee_rate,
    }


def _clear_kalshi_session() -> None:
    for key in (
        "kalshi_connected",
        "kalshi_api_key_id",
        "kalshi_private_key_pem",
        "kalshi_private_key_path",
        "kalshi_access_token",
        "kalshi_email",
        "kalshi_auth_mode",
        "kalshi_use_demo",
        "kalshi_balance",
        "kalshi_key_suffix",
        "kalshi_display_name",
    ):
        st.session_state.pop(key, None)


def _apply_kalshi_session(info: dict) -> None:
    st.session_state["kalshi_connected"] = True
    st.session_state["kalshi_auth_mode"] = info.get("auth_mode") or "api_key"
    st.session_state["kalshi_api_key_id"] = info.get("api_key_id")
    st.session_state["kalshi_private_key_pem"] = info.get("private_key_pem")
    st.session_state["kalshi_private_key_path"] = info.get("private_key_path")
    st.session_state["kalshi_access_token"] = info.get("access_token")
    st.session_state["kalshi_email"] = info.get("email")
    st.session_state["kalshi_use_demo"] = info["use_demo"]
    st.session_state["kalshi_balance"] = info["balance"]
    st.session_state["kalshi_key_suffix"] = info.get("key_id_suffix") or ""
    st.session_state["kalshi_display_name"] = info.get("display_name") or info.get("key_id_suffix") or ""


def _session_bankroll() -> float | None:
    bal = st.session_state.get("kalshi_balance") or {}
    dollars = bal.get("balance_dollars")
    return float(dollars) if dollars is not None else None


def _load_latest_scan() -> dict | None:
    path = ROOT / "data" / "scans" / "latest.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


@st.dialog("Log in to Kalshi")
def _kalshi_login_dialog(settings, env_creds: dict) -> None:
    """Modal login: email/password first, API key as fallback."""
    from kalshi_weather_edge.password_auth import env_password_configured  # noqa: E402

    pw_env = env_password_configured()
    login_demo = st.checkbox(
        "Use DEMO exchange",
        value=env_creds.get("env") == "demo",
        help="Demo practice exchange vs production.",
        key="dialog_login_demo",
    )

    tab_pw, tab_key = st.tabs(["Email & password", "API key"])

    with tab_pw:
        st.markdown("Sign in with the **same email and password** you use on kalshi.com.")
        st.caption(
            "If Kalshi rejects password login for apps (common on the new Trade API), "
            "use the **API key** tab instead — that is Kalshi’s official method."
        )
        email = st.text_input(
            "Email",
            value=pw_env.get("email") or "",
            key="dialog_email",
            autocomplete="username",
        )
        password = st.text_input(
            "Password",
            type="password",
            key="dialog_password",
            autocomplete="current-password",
        )
        pw_login = st.button(
            "Login",
            type="primary",
            use_container_width=True,
            key="dialog_pw_login_btn",
            help="Log in with your Kalshi website email and password.",
        )
        if pw_env.get("ready") and st.button(
            "Login with saved .env email/password",
            use_container_width=True,
            key="dialog_pw_env_btn",
        ):
            try:
                import os

                with st.spinner("Signing in..."):
                    info = connect_with_password(
                        settings,
                        email=pw_env["email"],
                        password=os.getenv("KALSHI_PASSWORD", ""),
                        use_demo=login_demo,
                    )
                _apply_kalshi_session(info)
                st.success("Logged in with email/password.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))
        if pw_login:
            try:
                with st.spinner("Signing in to Kalshi..."):
                    info = connect_with_password(
                        settings,
                        email=email,
                        password=password,
                        use_demo=login_demo,
                    )
                _apply_kalshi_session(info)
                st.success("Logged in — balance loaded.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    with tab_key:
        st.markdown(
            "Official Kalshi app auth: **API Key ID** + **private key** from "
            "[Account → API Keys](https://kalshi.com/account/profile)."
        )
        api_key_id = st.text_input(
            "API Key ID",
            value=env_creds.get("key_id") or "",
            key="dialog_api_key_id",
        )
        pem_text = st.text_area(
            "Private key (PEM)",
            value=env_creds.get("private_key_pem") or "",
            height=120,
            key="dialog_pem_text",
        )
        pem_file = st.file_uploader(
            "Or upload .pem file",
            type=["pem", "key", "txt"],
            key="dialog_pem_file",
        )
        if pem_file is not None:
            pem_text = pem_file.getvalue().decode("utf-8", errors="ignore")

        col_login, col_env = st.columns(2)
        with col_login:
            key_login = st.button(
                "Login with API key",
                type="primary",
                use_container_width=True,
                key="dialog_login_btn",
            )
        with col_env:
            env_clicked = st.button(
                "Login with .env key",
                use_container_width=True,
                disabled=not env_creds.get("ready"),
                key="dialog_env_btn",
            )

        if key_login:
            try:
                with st.spinner("Connecting to Kalshi..."):
                    info = connect_kalshi_account(
                        settings,
                        api_key_id=api_key_id,
                        private_key_pem=pem_text,
                        use_demo=login_demo,
                    )
                _apply_kalshi_session(info)
                st.success("Logged in — balance loaded.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Login failed: {exc}")

        if env_clicked:
            try:
                with st.spinner("Connecting from environment..."):
                    info = connect_from_env(settings, use_demo=login_demo)
                _apply_kalshi_session(info)
                st.success("Logged in from .env / secrets.")
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(f"Login failed: {exc}")

base_settings = load_settings()
creds = live_credentials_configured()

# Always-visible account bar (hard to miss on Cloud / mobile)
acct_l, acct_r = st.columns([3, 1])
with acct_l:
    if st.session_state.get("kalshi_connected"):
        bal = st.session_state.get("kalshi_balance") or {}
        dollars = bal.get("balance_dollars")
        env_label = "DEMO" if st.session_state.get("kalshi_use_demo") else "PROD"
        money = f"${dollars:,.2f}" if dollars is not None else "—"
        who = st.session_state.get("kalshi_display_name") or st.session_state.get("kalshi_key_suffix") or ""
        mode = st.session_state.get("kalshi_auth_mode") or "api_key"
        st.info(
            f"**Logged in** ({env_label} · {mode}) · {who} · balance **{money}**"
        )
    else:
        st.warning(
            "**Not logged in** — click **Login** to connect your Kalshi API key and load live balance."
        )
with acct_r:
    if st.session_state.get("kalshi_connected"):
        if st.button("Logout", use_container_width=True, key="top_logout"):
            _clear_kalshi_session()
            st.rerun()
    else:
        if st.button("Login", type="primary", use_container_width=True, key="top_login"):
            _kalshi_login_dialog(base_settings, creds)

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
1. Click **Login** and sign in with your Kalshi **email & password** (or API key if password login is blocked).
2. Keep **Paper** mode on while learning.
3. Pick a strategy profile (Favorites = safer/tighter; High profit = a bit looser).
4. Choose **Fee assumption** (maker vs taker) — taker is more conservative.
5. Click **Scan open markets**.
6. Review the **Top signals** table (includes net EV + sized contracts).
7. Later, click **Sync settlements** to update wins/losses and paper PnL.
"""
    )

with st.sidebar:
    st.header("Account")
    if st.session_state.get("kalshi_connected"):
        bal = st.session_state.get("kalshi_balance") or {}
        dollars = bal.get("balance_dollars")
        env_label = "DEMO" if st.session_state.get("kalshi_use_demo") else "PROD"
        st.success(
            f"Logged in ({env_label}) · …{st.session_state.get('kalshi_key_suffix', '')}"
        )
        st.metric(
            "Available balance",
            f"${dollars:,.2f}" if dollars is not None else "—",
            help="Live balance from Kalshi portfolio API.",
        )
        col_a, col_b = st.columns(2)
        with col_a:
            if st.button("Refresh", use_container_width=True, help="Re-fetch balance", key="side_refresh"):
                try:
                    st.session_state["kalshi_balance"] = refresh_balance(
                        base_settings,
                        api_key_id=st.session_state.get("kalshi_api_key_id"),
                        private_key_pem=st.session_state.get("kalshi_private_key_pem"),
                        private_key_path=st.session_state.get("kalshi_private_key_path"),
                        access_token=st.session_state.get("kalshi_access_token"),
                        use_demo=bool(st.session_state.get("kalshi_use_demo")),
                    )
                    st.rerun()
                except Exception as exc:  # noqa: BLE001
                    st.error(str(exc))
        with col_b:
            if st.button("Logout", use_container_width=True, key="side_logout"):
                _clear_kalshi_session()
                st.rerun()
    else:
        st.caption("Not logged in. Login connects to your real Kalshi account balance.")
        if st.button(
            "Login",
            type="primary",
            use_container_width=True,
            key="side_login",
            help="Open login form and connect with your Kalshi API key.",
        ):
            _kalshi_login_dialog(base_settings, creds)
        if creds.get("ready") and st.button(
            "Quick login (.env)",
            use_container_width=True,
            key="side_quick_env",
            help="One-click login using server .env / Streamlit secrets.",
        ):
            try:
                info = connect_from_env(
                    base_settings, use_demo=(creds.get("env") == "demo")
                )
                _apply_kalshi_session(info)
                st.rerun()
            except Exception as exc:  # noqa: BLE001
                st.error(str(exc))

    st.divider()
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

    if st.session_state.get("kalshi_connected"):
        use_demo = bool(st.session_state.get("kalshi_use_demo"))
        st.caption(f"Orders target {'DEMO' if use_demo else 'PRODUCTION'} (from login).")
    else:
        use_demo = st.checkbox(
            "Use Kalshi DEMO API",
            value=creds.get("env") == "demo",
            help=(
                "Use Kalshi's practice/demo exchange instead of real production markets. "
                "Still separate from Paper mode in this app."
            ),
        )
    st.caption(
        "API keys: "
        + (
            "logged in"
            if st.session_state.get("kalshi_connected")
            else ("ready (.env)" if creds["ready"] else "not configured")
        )
    )
    st.caption(
        "Alert webhook: " + ("ready" if webhook_ready() else "not set (ALERT_WEBHOOK_URL)")
    )
    if mode == "live":
        st.warning("Live mode can place real orders.")
        if not st.session_state.get("kalshi_connected") and not creds["ready"]:
            st.error("Click Login before live trading.")
        confirm_live = st.checkbox(
            "I understand — allow live order submission",
            help="Extra safety lock. Even in Live mode, orders only send if this is checked.",
        )
    else:
        confirm_live = False

    fee_assumption = st.selectbox(
        "Fee assumption",
        options=["maker", "taker"],
        index=0 if base_settings.fee_assumption != "taker" else 1,
        help=(
            "Maker assumes limit orders resting on the book (often $0 fee). "
            "Taker assumes crossing the spread and subtracts Kalshi taker fees from EV/sizing."
        ),
    )

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
        "Contracts per signal (fallback)",
        1,
        25,
        contracts,
        help=(
            "Used when not logged in, or when balance sizing is off. "
            "When connected, size ≈ risk% of Kalshi balance / entry price."
        ),
    )
    top_n = st.slider(
        "Show top N signals",
        3,
        25,
        10,
        help="How many of the strongest suggestions to highlight in the main table.",
    )
    bt_entry_hours = st.slider(
        "Backtest entry hours before close",
        1,
        48,
        int(base_settings.backtest_entry_hours_before_close),
        help="When replaying history, sample mid this many hours before market close.",
    )

    if strategy == "favorites":
        settings = with_overrides(
            base_settings,
            mode=mode,
            strategy=strategy,
            favorites_yes_threshold=float(yes_thr),
            favorites_no_threshold=float(no_thr),
            favorites_contracts=float(contracts),
            fee_assumption=fee_assumption,
        )
    else:
        settings = with_overrides(
            base_settings,
            mode=mode,
            strategy=strategy,
            high_profit_yes_threshold=float(yes_thr),
            high_profit_no_threshold=float(no_thr),
            high_profit_contracts=float(contracts),
            fee_assumption=fee_assumption,
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

    st.divider()
    st.subheader("Paper ledger backup")
    st.caption(
        f"Cloud reboots wipe SQLite. Snapshots restore W/L + PnL from "
        f"`data/ledger_snapshot.json` (updated by scans & GitHub Action)."
    )
    export_clicked = st.button(
        "Export ledger snapshot",
        use_container_width=True,
        help="Save current paper trades to data/ledger_snapshot.json in the repo folder.",
    )
    import_clicked = st.button(
        "Import ledger snapshot",
        use_container_width=True,
        help="Merge data/ledger_snapshot.json into the local database.",
    )

ledger = Ledger(settings.db_path)
_snapshot_path = default_snapshot_path(base_settings.data_dir)

if "snapshot_boot" not in st.session_state:
    boot = import_if_newer(ledger, _snapshot_path)
    st.session_state["snapshot_boot"] = boot
    if boot.get("imported_signals"):
        st.toast(f"Restored {boot['imported_signals']} signals from snapshot")
    elif boot.get("ok") is False and boot.get("reason"):
        st.warning(f"Ledger snapshot restore skipped: {boot['reason']}")
    elif boot.get("errors"):
        st.warning("Snapshot import had partial errors: " + "; ".join(boot["errors"][:3]))

if export_clicked:
    info = export_ledger_snapshot(ledger, _snapshot_path)
    st.success(f"Exported snapshot ({info['stats']['settled']} settled signals)")

if import_clicked:
    info = import_ledger_snapshot(ledger, _snapshot_path, merge=True)
    if info.get("ok"):
        st.success(
            f"Imported {info.get('imported_signals', 0)} signals · "
            f"paper PnL ${info['stats']['paper_pnl']:.2f}"
        )
        if info.get("errors"):
            st.warning("; ".join(info["errors"][:5]))
    else:
        st.info(info.get("reason", "Import skipped"))

if scan_clicked:
    with st.spinner("Scanning Kalshi open markets + settlements..."):
        try:
            result = run_consensus_scan(
                settings,
                strategy=strategy,
                notes=f"streamlit:{mode}",
                sync_settlements=True,
                bankroll_dollars=_session_bankroll(),
            )
            st.session_state["last_result"] = result
            st.session_state["last_strategy"] = strategy
            settle = result.get("settlements") or {}
            filt = result.get("filtered_close", 0)
            ded = result.get("deduped", 0)
            fev = result.get("filtered_ev", 0)
            st.success(
                f"Run #{result['run_id']}: {result['trade_signals']} signals / "
                f"{result['pass_signals']} pass · settlements {settle.get('signals_updated', 0)} · "
                f"filtered {filt} close · deduped {ded} · EV-filtered {fev}"
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
            export_ledger_snapshot(ledger, _snapshot_path)
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
            fee_rate = active_fee_rate(settings)
            bt_payload = _run_strategy_backtest(
                settings,
                base_settings,
                entry_hours=int(bt_entry_hours),
                fee_rate=fee_rate,
            )
            st.session_state["backtest"] = bt_payload
            hp = next(r for r in bt_payload["results"]["profiles"] if r["name"] == "high_profit")
            st.success(
                f"High profit @ {bt_entry_hours}h / fee={fee_rate:.3f}: "
                f"{hp['wins']}W/{hp['losses']}L ({hp['win_rate']:.1%}), "
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
bankroll = _session_bankroll()
c1, c2, c3, c4, c5, c6 = st.columns(6)
c1.metric("Mode", mode.upper(), help="Paper = practice. Live = real orders possible.")
c2.metric(
    "Strategy",
    strategy.replace("_", " ").title(),
    help="Which rule set is used when you scan.",
)
c3.metric(
    "Kalshi $",
    f"${bankroll:,.2f}" if bankroll is not None else "—",
    help="Live available balance after login (used for sizing when enabled).",
)
c4.metric(
    "Open trades",
    stats.get("open_trades", 0),
    help="Paper suggestions that have not settled yet.",
)
c5.metric(
    "Settled W/L",
    f"{stats.get('wins', 0)}/{stats.get('losses', 0)}",
    f"{stats.get('win_rate', 0):.0%} WR"
    if (stats.get("wins", 0) + stats.get("losses", 0))
    else None,
    help="Wins and losses after markets resolved (paper).",
)
c6.metric(
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
                "Only markets **closing within "
                f"{base_settings.scan_close_within_hours:.0f}h** (matches backtest style). "
                "Duplicates for open paper trades are hidden."
            )
            top = trades.head(top_n)
            show_cols = [
                c
                for c in (
                    "action",
                    "series",
                    "ticker",
                    "subtitle",
                    "market_mid",
                    "win_if_right",
                    "loss_if_wrong",
                    "net_ev",
                    "contracts",
                    "edge",
                )
                if c in top.columns
            ]
            st.dataframe(top[show_cols], use_container_width=True, hide_index=True)

            st.subheader("What these mean (payoff preview)")
            for _, row in top.iterrows():
                label = f"{row.get('action')} · {row.get('ticker')}"
                with st.expander(label, expanded=len(top) <= 3):
                    st.markdown(row.get("explain") or row.get("reason") or "")

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
                            api_key_id=st.session_state.get("kalshi_api_key_id"),
                            private_key_pem=st.session_state.get("kalshi_private_key_pem"),
                            private_key_path=st.session_state.get("kalshi_private_key_path"),
                            access_token=st.session_state.get("kalshi_access_token"),
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
            f"{bt.get('n_candidates')} candidates · "
            f"entry **{bt.get('entry_hours_before_close', '?')}h** before close · "
            f"fee_rate **{bt.get('fee_rate', 0):.3f}**"
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
- Closing-soon window: {base_settings.scan_close_within_hours:.0f}h
- Fee assumption default: `{base_settings.fee_assumption}` (taker fee rate {base_settings.taker_fee_rate})
- Assumed win rate for EV filter: {base_settings.assumed_win_rate:.0%}
- Balance sizing: {"on" if base_settings.use_balance_sizing else "off"} at {base_settings.bankroll_risk_fraction:.0%} of Kalshi balance when logged in

### Kalshi login
Click **Login** and use the **Email & password** tab (same credentials as kalshi.com).  
If Kalshi rejects password login for apps, switch to the **API key** tab and create a key at [kalshi.com/account/profile](https://kalshi.com/account/profile).  
Session credentials stay in the browser session only (or use `.env` / Streamlit secrets).

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
