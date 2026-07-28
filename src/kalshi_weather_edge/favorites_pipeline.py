from __future__ import annotations

from typing import Any

from .config import Settings, load_settings, thresholds_for_series
from .db import Ledger, utc_now
from .favorites import evaluate_favorite
from .fees import dollar, half_spread, mid_price
from .kalshi_client import KalshiClient
from .settlements import sync_settlements_for_open_signals
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
) -> dict[str, Any]:
    """Scan open Kalshi markets using favorites or high_profit thresholds."""
    settings = settings or load_settings()
    strategy_key, series_list, contracts = _scan_params(
        settings, strategy or settings.strategy
    )

    client = KalshiClient(settings.kalshi_base_url)
    ledger = Ledger(settings.db_path)
    run_id = ledger.start_run(notes=f"{notes}:{strategy_key}")

    rows: list[dict[str, Any]] = []
    errors: list[str] = []

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
                yes_bid = dollar(market.get("yes_bid_dollars"))
                yes_ask = dollar(market.get("yes_ask_dollars"))
                last = dollar(market.get("last_price_dollars"))
                mid = mid_price(yes_bid, yes_ask, last)
                if mid is None:
                    continue

                spread = half_spread(yes_bid, yes_ask)
                if settings.max_spread > 0 and spread > settings.max_spread:
                    signal = {
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
                        "yes_bid": yes_bid,
                        "yes_ask": yes_ask,
                        "spread": spread,
                        "fee": settings.maker_fee_rate,
                        "edge": 0.0,
                        "suggested_contracts": 0.0,
                        "reason": f"spread {spread:.3f} > max {settings.max_spread:.3f}",
                        "meta": {
                            "series": series,
                            "strategy": strategy_key,
                            "title": market.get("title"),
                            "subtitle": market.get("subtitle") or market.get("no_sub_title"),
                            "yes_threshold": yes_threshold,
                            "no_threshold": no_threshold,
                            "wide_spread": True,
                        },
                    }
                    rows.append(signal)
                    continue

                decision = evaluate_favorite(
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    mid=mid,
                    yes_threshold=yes_threshold,
                    no_threshold=no_threshold,
                    contracts=contracts,
                )

                signal = {
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
                    "fee": settings.maker_fee_rate,
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
                rows.append(signal)

        # Cap trades per event before writing (keep strongest edges)
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
        "settlements": settlement_info,
        "errors": errors,
        "rows": capped,
        "stats": ledger.signal_stats(),
        "performance": ledger.performance_board(),
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
        out.append(
            {
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
                "yes_thr": meta.get("yes_threshold"),
                "no_thr": meta.get("no_threshold"),
                "reason": r.get("reason"),
            }
        )
    return out
