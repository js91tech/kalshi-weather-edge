from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import ROOT
from .kalshi_client import KalshiClient


class KalshiMarketData(KalshiClient):
    def __init__(self, base_url: str, timeout: float = 30.0, cache_path: Path | None = None) -> None:
        super().__init__(base_url, timeout=timeout)
        self.cache_path = cache_path or (ROOT / "data" / "cache" / "entry_quotes.json")
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, Any] = {}
        self._lock = threading.Lock()
        if self.cache_path.exists():
            try:
                self._cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
            except Exception:
                self._cache = {}

    def _save_cache(self) -> None:
        with self._lock:
            self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")

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
        cache_key = f"{ticker}|{hours_before_close}|{close_time_iso}"
        with self._lock:
            if cache_key in self._cache:
                return dict(self._cache[cache_key])

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
            out = {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}
            with self._lock:
                self._cache[cache_key] = out
            return dict(out)

        def _parse(c: dict[str, Any]) -> tuple[float, float | None, float | None, float | None]:
            ts = float(c.get("end_period_ts") or 0)
            bid = _dollar((c.get("yes_bid") or {}).get("close_dollars"))
            ask = _dollar((c.get("yes_ask") or {}).get("close_dollars"))
            px = _dollar((c.get("price") or {}).get("close_dollars"))
            return ts, bid, ask, px

        parsed = [_parse(c) for c in candles]
        # Prefer candles near the target entry time with a usable mid.
        ranked = sorted(parsed, key=lambda x: abs(x[0] - target_ts))

        def _mid(bid: float | None, ask: float | None, px: float | None) -> float | None:
            if bid is not None and ask is not None and ask >= bid:
                return (float(bid) + float(ask)) / 2.0
            if px is not None:
                return float(px)
            if bid is not None:
                return float(bid)
            if ask is not None:
                return float(ask)
            return None

        result: dict[str, Any] | None = None
        # Pass 1: near target, mid in (0.01, 0.99)
        for ts, bid, ask, px in ranked:
            mid = _mid(bid, ask, px)
            if mid is None or mid < 0.01 or mid > 0.99:
                continue
            # Normalize quotes for maker simulation
            if bid is None or bid <= 0:
                bid = max(0.01, mid - 0.01)
            if ask is None or ask >= 1:
                ask = min(0.99, mid + 0.01)
            if ask < bid:
                ask = min(0.99, bid + 0.01)
            result = {
                "yes_bid": float(bid),
                "yes_ask": float(ask),
                "mid": float(mid),
                "source": f"candle@{int(ts)}",
            }
            break

        # Pass 2: any candle with any price
        if result is None:
            for ts, bid, ask, px in ranked:
                mid = _mid(bid, ask, px)
                if mid is None or mid <= 0 or mid >= 1:
                    continue
                bid_n = bid if bid is not None and bid > 0 else max(0.01, mid - 0.01)
                ask_n = ask if ask is not None and ask < 1 else min(0.99, mid + 0.01)
                if ask_n < bid_n:
                    ask_n = min(0.99, bid_n + 0.01)
                result = {
                    "yes_bid": float(bid_n),
                    "yes_ask": float(ask_n),
                    "mid": float(mid),
                    "source": f"candle_fallback@{int(ts)}",
                }
                break

        if result is None:
            result = {"yes_bid": None, "yes_ask": None, "mid": None, "source": None}

        with self._lock:
            self._cache[cache_key] = result
            # Persist periodically (every ~25 inserts)
            if len(self._cache) % 25 == 0:
                self.cache_path.write_text(json.dumps(self._cache), encoding="utf-8")
        return dict(result)

    def flush_cache(self) -> None:
        self._save_cache()


def _dollar(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
