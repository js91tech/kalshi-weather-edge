from __future__ import annotations

from typing import Any

from .config import Settings, active_fee_rate, load_settings, thresholds_for_series
from .db import Ledger, utc_now
from .favorites import evaluate_favorite
from .fees import dollar, half_spread, mid_price
from .kalshi_client import KalshiClient
from .ledger_snapshot import default_snapshot_path, export_ledger_snapshot, import_if_newer
from .market_filters import dedup_open_trades, filter_closing_soon
from .payoffs import enrich_row
from .settlements import sync_settlements_for_open_signals
from .sizing import size_signal
from .tickers import cap_trades_per_event


def _scan_params(settings: Settings, strategy: str) -> tuple[str, list[str], float]:
    if strategy == "high_profit":
        return (
            "high_profit",
            settings.high_profit_series,
            settings.high_profit_contracts,
        )
    return (
        "favorites",
        settings.favorites_series,
        settings.favorites_contracts,
    )


def run_consensus_scan(
    settings: Settings | None = None,
    *,
    strategy: str | None = None,
    notes: str = "consensus",
    sync_settlements: bool = True,
    persist_snapshot: bool = True,
    bankroll_dollars: float | None = None,
) -> dict[str, Any]:
    """Scan open Kalshi markets using favorites or high_profit thresholds."""
    settings = settings or load_settings()
    strategy_key, series_list, contracts = _scan_params(
        settings, strategy or settings.strategy
    )
    fee_rate = active_fee_rate(settings)
    sizing_bankroll = bankroll_dollars if settings.use_balance_sizing else None

    client = KalshiClient(settings.kalshi_base_url)
    ledger = Ledger(settings.db_path)
    snapshot_path = default_snapshot_path(settings.data_dir)
    import_if_newer(ledger, snapshot_path)

    run_id = ledger.start_run(notes=f"{notes}:{strategy_key}")
    open_tickers = ledger.open_trade_ticker_set() if settings.dedup_open_trades else set()

    rows: list[dict[str, Any]] = []
    markets_by_ticker: dict[str, dict[str, Any]] = {}
    errors: list[str] = []
    filtered_close = 0
    deduped = 0
    filtered_ev = 0

    try:
        for series in series_list:
            yes_threshold, no_threshold = thresholds_for_series(
                settings, strategy_key, series
            )
            try:
                markets = client.get_markets(series, status="open", limit=200)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{series}: {exc}")
                continue

            for market in markets:
                ticker = market.get("ticker")
                if ticker:
                    markets_by_ticker[ticker] = market

                yes_bid = dollar(market.get("yes_bid_dollars"))
                yes_ask = dollar(market.get("yes_ask_dollars"))
                last = dollar(market.get("last_price_dollars"))
                mid = mid_price(yes_bid, yes_ask, last)
                if mid is None:
                    continue

                spread = half_spread(yes_bid, yes_ask)
                if settings.max_spread > 0 and spread > settings.max_spread:
                    rows.append(
                        _pass_signal(
                            series=series,
                            market=market,
                            mid=mid,
                            spread=spread,
                            strategy_key=strategy_key,
                            yes_threshold=yes_threshold,
                            no_threshold=no_threshold,
                            reason=f"spread {spread:.3f} > max {settings.max_spread:.3f}",
                            tag="wide_spread",
                        )
                    )
                    continue

                decision = evaluate_favorite(
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    mid=mid,
                    yes_threshold=yes_threshold,
                    no_threshold=no_threshold,
                    contracts=contracts,
                )

                rows.append(
                    {
                        "created_at": utc_now(),
                        "city_id": series,
                        "ticker": market["ticker"],
                        "event_ticker": market.get("event_ticker"),
                        "target_date": None,
                        "action": decision.action,
                        "side": decision.side,
                        "execution": decision.execution,
                        "model_p": decision.market_mid,
                        "market_mid": decision.market_mid,
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "spread": spread,
                        "fee": fee_rate,
                        "edge": decision.edge,
                        "suggested_contracts": decision.suggested_contracts,
                        "reason": decision.reason,
                        "meta": {
                            "series": series,
                            "strategy": strategy_key,
                            "title": market.get("title"),
                            "subtitle": market.get("subtitle") or market.get("no_sub_title"),
                            "yes_threshold": yes_threshold,
                            "no_threshold": no_threshold,
                        },
                    }
                )

        before = sum(1 for r in rows if r["action"] != "PASS")
        rows = filter_closing_soon(
            rows,
            markets_by_ticker,
            settings.scan_close_within_hours,
        )
        filtered_close = before - sum(1 for r in rows if r["action"] != "PASS")

        before = sum(1 for r in rows if r["action"] != "PASS")
        rows = dedup_open_trades(rows, open_tickers)
        deduped = before - sum(1 for r in rows if r["action"] != "PASS")

        sized_rows: list[dict[str, Any]] = []
        for row in rows:
            if row.get("action") == "PASS":
                sized_rows.append(row)
                continue
            sized = size_signal(
                row,
                fee_rate=fee_rate,
                assumed_win_rate=settings.assumed_win_rate,
                require_positive_net_ev=settings.require_positive_net_ev,
                bankroll_dollars=sizing_bankroll,
                risk_fraction=settings.bankroll_risk_fraction,
                max_contracts=settings.max_contracts_per_signal,
                base_contracts=contracts,
            )
            if sized.get("action") == "PASS" and row.get("action") != "PASS":
                filtered_ev += 1
            sized_rows.append(sized)
        rows = sized_rows

        capped = cap_trades_per_event(rows, settings.max_trades_per_event)
        trade_count = 0
        pass_count = 0
        for signal in capped:
            ledger.insert_signal(run_id, signal)
            if signal["action"] == "PASS":
                pass_count += 1
            else:
                trade_count += 1

        settlement_info: dict[str, Any] = {
            "tickers_checked": 0,
            "settlements_upserted": 0,
            "signals_updated": 0,
        }
        if sync_settlements:
            try:
                settlement_info = sync_settlements_for_open_signals(
                    ledger,
                    client,
                    series_list=series_list,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"settlements: {exc}")

        snapshot_info: dict[str, Any] = {"skipped": True}
        if persist_snapshot:
            snapshot_info = {
                "path": str(snapshot_path),
                "exported_at": export_ledger_snapshot(ledger, snapshot_path).get("exported_at"),
            }

        ledger.finish_run(run_id)
    except Exception:
        ledger.finish_run(run_id)
        raise

    return {
        "run_id": run_id,
        "mode": settings.mode,
        "strategy": strategy_key,
        "series": series_list,
        "markets_scored": len(capped),
        "trade_signals": trade_count,
        "pass_signals": pass_count,
        "filtered_close": filtered_close,
        "deduped": deduped,
        "filtered_ev": filtered_ev,
        "fee_rate": fee_rate,
        "bankroll_dollars": sizing_bankroll,
        "settlements": settlement_info,
        "snapshot": snapshot_info,
        "errors": errors,
        "rows": capped,
        "stats": ledger.signal_stats(),
        "performance": ledger.performance_board(),
    }


def _pass_signal(
    *,
    series: str,
    market: dict[str, Any],
    mid: float,
    spread: float,
    strategy_key: str,
    yes_threshold: float,
    no_threshold: float,
    reason: str,
    tag: str,
) -> dict[str, Any]:
    meta = {
        "series": series,
        "strategy": strategy_key,
        "title": market.get("title"),
        "subtitle": market.get("subtitle") or market.get("no_sub_title"),
        "yes_threshold": yes_threshold,
        "no_threshold": no_threshold,
        tag: True,
    }
    return {
        "created_at": utc_now(),
        "city_id": series,
        "ticker": market["ticker"],
        "event_ticker": market.get("event_ticker"),
        "target_date": None,
        "action": "PASS",
        "side": None,
        "execution": "none",
        "model_p": mid,
        "market_mid": mid,
        "yes_bid": dollar(market.get("yes_bid_dollars")),
        "yes_ask": dollar(market.get("yes_ask_dollars")),
        "spread": spread,
        "fee": 0.0,
        "edge": 0.0,
        "suggested_contracts": 0.0,
        "reason": reason,
        "meta": meta,
    }


def run_favorites_scan(
    settings: Settings | None = None,
    notes: str = "favorites",
) -> dict[str, Any]:
    return run_consensus_scan(settings, strategy="favorites", notes=notes)


def run_high_profit_scan(
    settings: Settings | None = None,
    notes: str = "high_profit",
) -> dict[str, Any]:
    return run_consensus_scan(settings, strategy="high_profit", notes=notes)


def rows_to_dataframe_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in result.get("rows") or []:
        meta = r.get("meta") or {}
        row = {
            "strategy": meta.get("strategy") or result.get("strategy"),
            "series": meta.get("series") or r.get("city_id"),
            "ticker": r.get("ticker"),
            "title": meta.get("title"),
            "subtitle": meta.get("subtitle"),
            "action": r.get("action"),
            "side": r.get("side"),
            "execution": r.get("execution"),
            "market_mid": r.get("market_mid"),
            "yes_bid": r.get("yes_bid"),
            "yes_ask": r.get("yes_ask"),
            "spread": r.get("spread"),
            "edge": r.get("edge"),
            "contracts": r.get("suggested_contracts"),
            "net_ev": r.get("net_ev"),
            "yes_thr": meta.get("yes_threshold"),
            "no_thr": meta.get("no_threshold"),
            "hours_to_close": meta.get("hours_to_close"),
            "reason": r.get("reason"),
        }
        out.append(enrich_row(row))
    return out
