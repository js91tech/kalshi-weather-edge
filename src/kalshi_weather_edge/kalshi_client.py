from __future__ import annotations

from typing import Any

import requests


class KalshiClient:
    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"Accept": "application/json", "User-Agent": "kalshi-weather-edge/0.1"})

    def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        resp = self.session.get(url, params=params or {}, timeout=self.timeout)
        resp.raise_for_status()
        return resp.json()

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
                "limit": min(limit, 200),
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
            if len(markets) >= limit:
                break
        return markets

    def get_markets_any_status(self, series_ticker: str, limit: int = 200) -> list[dict[str, Any]]:
        """Fetch recent markets including settled (for backfill)."""
        return self.get_markets(series_ticker, status=None, limit=limit)
