from __future__ import annotations

from typing import Any

from .config import Settings, load_settings
from .db import Ledger, utc_now
from .favorites import evaluate_favorite
from .fees import dollar, half_spread, mid_price
from .kalshi_client import KalshiClient


def run_favorites_scan(
    settings: Settings | None = None,
    notes: str = "favorites",
) -> dict[str, Any]:
    """Scan open Kalshi markets for extreme consensus (favorites) signals."""
    settings = settings or load_settings()
    client = KalshiClient(settings.kalshi_base_url)
    ledger = Ledger(settings.db_path)
    run_id = ledger.start_run(notes=notes)

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    trade_count = 0
    pass_count = 0

    try:
        for series in settings.favorites_series:
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

                decision = evaluate_favorite(
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    mid=mid,
                    yes_threshold=settings.favorites_yes_threshold,
                    no_threshold=settings.favorites_no_threshold,
                    contracts=settings.favorites_contracts,
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
                    "spread": half_spread(yes_bid, yes_ask),
                    "fee": settings.maker_fee_rate,
                    "edge": decision.edge,
                    "suggested_contracts": decision.suggested_contracts,
                    "reason": decision.reason,
                    "meta": {
                        "series": series,
                        "strategy": "favorites",
                        "title": market.get("title"),
                        "subtitle": market.get("subtitle") or market.get("no_sub_title"),
                        "yes_threshold": settings.favorites_yes_threshold,
                        "no_threshold": settings.favorites_no_threshold,
                    },
                }
                ledger.insert_signal(run_id, signal)
                rows.append(signal)
                if decision.action == "PASS":
                    pass_count += 1
                else:
                    trade_count += 1

        ledger.apply_settlements_to_signals()
        ledger.finish_run(run_id)
    except Exception:
        ledger.finish_run(run_id)
        raise

    return {
        "run_id": run_id,
        "mode": settings.mode,
        "strategy": settings.strategy,
        "markets_scored": len(rows),
        "trade_signals": trade_count,
        "pass_signals": pass_count,
        "errors": errors,
        "rows": rows,
        "stats": ledger.signal_stats(),
    }


def rows_to_dataframe_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in result.get("rows") or []:
        meta = r.get("meta") or {}
        out.append(
            {
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
                "edge": r.get("edge"),
                "contracts": r.get("suggested_contracts"),
                "reason": r.get("reason"),
            }
        )
    return out
