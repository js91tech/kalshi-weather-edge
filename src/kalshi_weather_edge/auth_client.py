from __future__ import annotations

import base64
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import requests
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from dotenv import load_dotenv

from .config import ROOT


class KalshiTradingClient:
    """Authenticated Kalshi client for live/demo order placement."""

    def __init__(
        self,
        base_url: str,
        api_key_id: str | None = None,
        private_key_path: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        load_dotenv(ROOT / ".env")
        self.base_url = base_url.rstrip("/")
        self.api_key_id = (api_key_id or os.getenv("KALSHI_API_KEY_ID", "")).strip()
        path = private_key_path or os.getenv("KALSHI_PRIVATE_KEY_PATH", "")
        self.private_key_path = str(Path(path).expanduser()) if path else ""
        self.timeout = timeout
        if not self.api_key_id or not self.private_key_path:
            raise ValueError(
                "Live trading requires KALSHI_API_KEY_ID and KALSHI_PRIVATE_KEY_PATH in .env"
            )
        self._private_key = self._load_private_key(self.private_key_path)
        self.session = requests.Session()
        self.session.headers.update(
            {"Accept": "application/json", "Content-Type": "application/json", "User-Agent": "kalshi-weather-edge/0.2"}
        )

    @staticmethod
    def _load_private_key(path: str) -> Any:
        with open(path, "rb") as f:
            return serialization.load_pem_private_key(f.read(), password=None, backend=default_backend())

    def _sign_path(self, method: str, path_with_query: str) -> dict[str, str]:
        # Sign full API path from root without query string
        parsed = urlparse(self.base_url + path_with_query.split("?")[0])
        # base_url already includes /trade-api/v2; request paths are like /portfolio/...
        sign_path = parsed.path
        timestamp = str(int(time.time() * 1000))
        message = f"{timestamp}{method.upper()}{sign_path}".encode("utf-8")
        signature = self._private_key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return {
            "KALSHI-ACCESS-KEY": self.api_key_id,
            "KALSHI-ACCESS-TIMESTAMP": timestamp,
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(signature).decode("utf-8"),
        }

    def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        headers = self._sign_path(method, path)
        url = f"{self.base_url}{path}"
        resp = self.session.request(
            method.upper(),
            url,
            headers=headers,
            params=params,
            json=json_body,
            timeout=self.timeout,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"Kalshi API {resp.status_code}: {resp.text[:500]}")
        if not resp.content:
            return {}
        return resp.json()

    def get_balance(self) -> dict[str, Any]:
        return self._request("GET", "/portfolio/balance")

    def place_order(
        self,
        *,
        ticker: str,
        side: str,
        action: str,
        count: int,
        yes_price: int | None = None,
        no_price: int | None = None,
        order_type: str = "limit",
    ) -> dict[str, Any]:
        """
        Place an order.
        side: 'yes' | 'no'
        action: 'buy' | 'sell'
        yes_price/no_price: integer cents 1-99 for limit orders
        """
        body: dict[str, Any] = {
            "ticker": ticker,
            "side": side.lower(),
            "action": action.lower(),
            "count": int(count),
            "type": order_type,
        }
        if yes_price is not None:
            body["yes_price"] = int(yes_price)
        if no_price is not None:
            body["no_price"] = int(no_price)
        return self._request("POST", "/portfolio/orders", json_body=body)
