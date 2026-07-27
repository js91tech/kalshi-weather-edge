from __future__ import annotations


def taker_fee_per_contract(price: float, fee_rate: float = 0.07) -> float:
    """Kalshi-style taker fee ≈ rate * p * (1-p) dollars per $1 contract."""
    p = min(max(float(price), 0.0), 1.0)
    return float(fee_rate) * p * (1.0 - p)


def mid_price(yes_bid: float | None, yes_ask: float | None, last: float | None = None) -> float | None:
    if yes_bid is not None and yes_ask is not None and yes_ask > 0:
        return (float(yes_bid) + float(yes_ask)) / 2.0
    if last is not None:
        return float(last)
    if yes_bid is not None:
        return float(yes_bid)
    if yes_ask is not None:
        return float(yes_ask)
    return None


def half_spread(yes_bid: float | None, yes_ask: float | None) -> float:
    if yes_bid is None or yes_ask is None:
        return 0.02
    return max(0.0, (float(yes_ask) - float(yes_bid)) / 2.0)


def dollar(v: str | float | None) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
