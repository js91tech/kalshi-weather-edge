from __future__ import annotations

from typing import Any

from .auth_client import KalshiTradingClient
from .config import Settings, active_kalshi_base_url, live_credentials_configured


def cents_from_dollars(price: float | None) -> int | None:
    if price is None:
        return None
    return max(1, min(99, int(round(float(price) * 100))))


def execute_signal(
    signal: dict[str, Any],
    settings: Settings,
    *,
    mode: str,
    confirm_live: bool = False,
    use_demo: bool | None = None,
) -> dict[str, Any]:
    """
    Paper: log-only acknowledgment.
    Live: place a small maker limit order when credentials + confirm_live are set.
    """
    mode = (mode or "paper").lower()
    action = signal.get("action")
    if action in (None, "PASS"):
        return {"ok": True, "mode": mode, "skipped": True, "reason": "PASS signal"}

    contracts = float(signal.get("suggested_contracts") or 0)
    if contracts <= 0:
        return {"ok": True, "mode": mode, "skipped": True, "reason": "zero size"}

    if mode != "live":
        return {
            "ok": True,
            "mode": "paper",
            "skipped": False,
            "paper": True,
            "ticker": signal.get("ticker"),
            "action": action,
            "side": signal.get("side"),
            "contracts": contracts,
            "message": "Paper trade recorded (no exchange order sent)",
        }

    if not confirm_live:
        return {
            "ok": False,
            "mode": "live",
            "error": "Live mode requires explicit confirmation before sending orders",
        }

    creds = live_credentials_configured()
    if not creds["ready"]:
        return {
            "ok": False,
            "mode": "live",
            "error": "Missing KALSHI_API_KEY_ID or KALSHI_PRIVATE_KEY_PATH (.env)",
            "credentials": creds,
        }

    if settings.live_require_maker and (signal.get("execution") or "").lower() == "taker":
        return {
            "ok": False,
            "mode": "live",
            "error": "Config requires maker-only live orders; this signal is taker",
        }

    count = int(
        max(
            1,
            min(
                int(contracts),
                settings.live_max_contracts_per_order,
                settings.max_contracts_per_signal,
            ),
        )
    )

    side = (signal.get("side") or "YES").upper()
    if action == "BUY_NO":
        side = "NO"
    elif action == "BUY_YES":
        side = "YES"
    yes_bid = signal.get("yes_bid")
    yes_ask = signal.get("yes_ask")

    # Maker: buy YES at bid, or buy NO near (1 - ask) by posting NO bid
    if side == "YES":
        order_side = "yes"
        order_action = "buy"
        yes_price = cents_from_dollars(yes_bid if yes_bid is not None else signal.get("market_mid"))
        no_price = None
    else:
        order_side = "no"
        order_action = "buy"
        # Buy NO as maker around market NO bid ≈ 1 - yes_ask
        no_px = None
        if yes_ask is not None:
            no_px = 1.0 - float(yes_ask)
        elif signal.get("market_mid") is not None:
            no_px = 1.0 - float(signal["market_mid"])
        no_price = cents_from_dollars(no_px)
        yes_price = None

    client = KalshiTradingClient(base_url=active_kalshi_base_url(settings, use_demo=use_demo))
    try:
        resp = client.place_order(
            ticker=str(signal["ticker"]),
            side=order_side,
            action=order_action,
            count=count,
            yes_price=yes_price,
            no_price=no_price,
            order_type="limit",
        )
        return {
            "ok": True,
            "mode": "live",
            "ticker": signal.get("ticker"),
            "count": count,
            "order_side": order_side,
            "order_action": order_action,
            "yes_price": yes_price,
            "no_price": no_price,
            "response": resp,
        }
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "mode": "live", "error": str(exc)}
