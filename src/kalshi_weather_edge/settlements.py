from __future__ import annotations

from typing import Any

from .db import Ledger
from .kalshi_client import KalshiClient


def sync_settlements_for_open_signals(
    ledger: Ledger,
    client: KalshiClient,
    *,
    series_list: list[str] | None = None,
    max_markets_per_series: int = 200,
) -> dict[str, Any]:
    """
    Fetch settled markets for series with open paper signals, upsert settlements,
    and apply PnL to matching signals.
    """
    open_rows = ledger.unsettled_trade_tickers()
    if not open_rows:
        return {"tickers_checked": 0, "settlements_upserted": 0, "signals_updated": 0}

    series_needed: set[str] = set(series_list or [])
    for row in open_rows:
        series_needed.add(row["city_id"])
        # Prefer series from meta when available
        meta_series = (row.get("meta") or {}).get("series")
        if meta_series:
            series_needed.add(str(meta_series))

    open_tickers = {r["ticker"] for r in open_rows}
    upserted = 0
    for series in sorted(series_needed):
        try:
            markets = client.get_markets(series, status="settled", limit=max_markets_per_series)
        except Exception:
            try:
                markets = client.get_markets_any_status(series, limit=max_markets_per_series)
            except Exception:
                continue
        for m in markets:
            ticker = m.get("ticker")
            if not ticker or ticker not in open_tickers:
                continue
            result = (m.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue
            ledger.upsert_settlement(m)
            upserted += 1

    updated = ledger.apply_settlements_to_signals()
    return {
        "tickers_checked": len(open_tickers),
        "settlements_upserted": upserted,
        "signals_updated": updated,
    }
