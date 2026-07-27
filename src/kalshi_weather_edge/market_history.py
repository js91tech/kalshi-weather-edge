from __future__ import annotations

from typing import Any
from datetime import datetime, timezone

import requests

from .kalshi_client import KalshiClient  # re-export pattern avoided; extend below


class KalshiMarketData(KalshiClient):
    def get_candlesticks(
        self,
        series_ticker: str,
        ticker: str,
        start_ts: int,
        end_ts: int,
        period_interval: int = 60,
    ) -> list[dict[str, Any]]:
        path = f"/series/{series_ticker}/markets/{ticker}/candlesticks"
        data = self._get(
            path,
            {
                "start_ts": int(start_ts),
                "end_ts": int(end_ts),
                "period_interval": int(period_interval),
            },
        )
        return list(data.get("candlesticks") or [])

    def entry_quote_from_candles(
        self,
        series_ticker: str,
        ticker: str,
        close_time_iso: str | None,
        hours_before_close: int = 18,
    ) -> dict[str, float | None]:
        """
        Approximate tradable bid/ask using candle closes ~hours_before_close before market close.
        Falls back to earliest candle with a sane mid if exact window missing.
        """
        if not close_time_iso:
            return {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}

        close_dt = datetime.fromisoformat(close_time_iso.replace("Z", "+00:00"))
        if close_dt.tzinfo is None:
            close_dt = close_dt.replace(tzinfo=timezone.utc)
        end_ts = int(close_dt.timestamp())
        start_ts = end_ts - max(hours_before_close + 36, 48) * 3600
        target_ts = end_ts - hours_before_close * 3600

        try:
            candles = self.get_candlesticks(series_ticker, ticker, start_ts, end_ts, period_interval=60)
        except Exception:
            return {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}

        if not candles:
            return {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}

        def _parse(c: dict[str, Any]) -> tuple[float, float | None, float | None, float | None]:
            ts = float(c.get("end_period_ts") or 0)
            bid = _dollar((c.get("yes_bid") or {}).get("close_dollars"))
            ask = _dollar((c.get("yes_ask") or {}).get("close_dollars"))
            px = _dollar((c.get("price") or {}).get("close_dollars"))
            return ts, bid, ask, px

        parsed = [_parse(c) for c in candles]
        # Prefer candle closest to target with mid in (0.02, 0.98)
        ranked = sorted(parsed, key=lambda x: abs(x[0] - target_ts))
        for ts, bid, ask, px in ranked:
            mid = None
            if bid is not None and ask is not None and 0 < bid <= ask < 1:
                mid = (bid + ask) / 2.0
            elif px is not None:
                mid = px
            if mid is None or mid <= 0.02 or mid >= 0.98:
                continue
            # If only mid, synthesize tight spread
            if bid is None or ask is None:
                bid = max(0.01, mid - 0.01)
                ask = min(0.99, mid + 0.01)
            return {
                "yes_bid": bid,
                "yes_ask": ask,
                "mid": mid,
                "source": f"candle@{int(ts)}",
            }

        # Last resort: any candle with a price
        for ts, bid, ask, px in ranked:
            mid = px
            if bid is not None and ask is not None:
                mid = (bid + ask) / 2.0
            if mid is None:
                continue
            return {
                "yes_bid": bid if bid is not None else max(0.01, mid - 0.01),
                "yes_ask": ask if ask is not None else min(0.99, mid + 0.01),
                "mid": mid,
                "source": f"candle_fallback@{int(ts)}",
            }

        return {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}


def _dollar(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
