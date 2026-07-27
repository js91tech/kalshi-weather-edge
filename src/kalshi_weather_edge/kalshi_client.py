from __future__ import annotations

from typing import Any

import requests
import time


class KalshiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "kalshi-weather-edge/0.1"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        # Fresh request per call so ThreadPool backtests stay thread-safe
        last_err: Exception | None = None
        for attempt in range(5):
            try:
                resp = requests.get(
                    url,
                    params=params or {},
                    timeout=self.timeout,
                    headers=dict(self.session.headers),
                )
                if resp.status_code == 429:
                    time.sleep(0.5 * (2**attempt))
                    continue
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001
                last_err = exc
                time.sleep(0.3 * (2**attempt))
        raise RuntimeError(f"Kalshi GET failed after retries: {path}: {last_err}")

    def get_markets(
        self,
        series_ticker: str,
        status: str | None = "open",
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        markets: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            params: dict[str, Any] = {
                "series_ticker": series_ticker,
                "limit": min(200, max(1, limit - len(markets)) if limit else 200),
            }
            if status:
                params["status"] = status
            if cursor:
                params["cursor"] = cursor
            data = self._get("/markets", params)
            batch = data.get("markets") or []
            markets.extend(batch)
            cursor = data.get("cursor") or None
            if not cursor or not batch:
                break
            if limit and len(markets) >= limit:
                break
        return markets[:limit] if limit else markets

    def get_markets_any_status(self, series_ticker: str, limit: int = 200) -> list[dict[str, Any]]:
        """Fetch recent markets including settled (for backfill)."""
        return self.get_markets(series_ticker, status=None, limit=limit)
