from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .config import City, Settings
from .fees import half_spread, mid_price, taker_fee_per_contract


@dataclass
class EdgeDecision:
    action: str  # PASS | BUY_YES | FADE_YES
    side: str | None  # YES | NO
    execution: str  # maker | taker | none
    model_p: float
    market_mid: float
    yes_bid: float | None
    yes_ask: float | None
    spread: float
    fee: float
    edge: float
    suggested_contracts: float
    reason: str
    meta: dict[str, Any]


def fractional_kelly(edge: float, price: float, bankroll: float, fraction: float, cap: int) -> float:
    """
    Rough binary Kelly for buying YES at `price` when true p = price + edge.
    f* ≈ edge / (1 - price) for YES; clamp and scale.
    """
    p = min(max(price, 0.01), 0.99)
    # edge is already model_p - market costs; use |edge| for sizing magnitude
    edge_abs = abs(edge)
    if edge_abs <= 0:
        return 0.0
    # Approximate Kelly fraction of bankroll in contract count (~$1 notional)
    raw = (edge_abs / max(1.0 - p, 0.01)) * fraction * bankroll
    return float(max(0.0, min(cap, raw)))


def shrink_toward_market(model_p: float, market_mid: float, weight: float) -> float:
    """Blend model toward market to reduce overconfidence before CLI calibration."""
    w = min(max(float(weight), 0.0), 1.0)
    return (1.0 - w) * float(model_p) + w * float(market_mid)


def evaluate_market(
    *,
    model_p: float,
    yes_bid: float | None,
    yes_ask: float | None,
    last: float | None,
    city: City,
    settings: Settings,
) -> EdgeDecision:
    mid = mid_price(yes_bid, yes_ask, last)
    if mid is None:
        return EdgeDecision(
            action="PASS",
            side=None,
            execution="none",
            model_p=model_p,
            market_mid=0.0,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=0.0,
            fee=0.0,
            edge=0.0,
            suggested_contracts=0.0,
            reason="No usable market prices",
            meta={},
        )

    raw_model_p = float(model_p)
    model_p = shrink_toward_market(raw_model_p, mid, settings.market_shrinkage)

    spread = half_spread(yes_bid, yes_ask)
    maker_fee = settings.maker_fee_rate
    taker_fee = taker_fee_per_contract(mid, settings.taker_fee_rate)

    # Maker edges: buy YES at bid, or fade YES (buy NO) near ask
    buy_yes_edge_maker = model_p - float(yes_bid or mid) - maker_fee - 0.0
    # Fading YES: model says lower than market. Value of selling YES / buying NO
    # approx (market_ask - model_p) when we can sell at ask as maker
    fade_edge_maker = float(yes_ask or mid) - model_p - maker_fee

    buy_yes_edge_taker = model_p - float(yes_ask or mid) - taker_fee
    fade_edge_taker = float(yes_bid or mid) - model_p - taker_fee

    longshot = mid <= settings.longshot_market_max
    overprice = mid - model_p
    longshot_ok = (
        longshot
        and overprice >= max(settings.longshot_overprice_min, city.longshot_bias * 0.5)
    )

    meta = {
        "raw_model_p": raw_model_p,
        "shrunk_model_p": model_p,
        "market_shrinkage": settings.market_shrinkage,
        "buy_yes_edge_maker": buy_yes_edge_maker,
        "fade_edge_maker": fade_edge_maker,
        "buy_yes_edge_taker": buy_yes_edge_taker,
        "fade_edge_taker": fade_edge_taker,
        "longshot": longshot,
        "overprice": overprice,
        "longshot_bias": city.longshot_bias,
    }

    # Prefer maker longshot fade
    if longshot_ok and fade_edge_maker >= settings.min_edge:
        size = fractional_kelly(
            fade_edge_maker,
            1.0 - float(yes_ask or mid),
            settings.paper_bankroll,
            settings.kelly_fraction,
            settings.max_contracts_per_signal,
        )
        return EdgeDecision(
            action="FADE_YES",
            side="NO",
            execution="maker",
            model_p=model_p,
            market_mid=mid,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=spread,
            fee=maker_fee,
            edge=fade_edge_maker,
            suggested_contracts=size,
            reason=(
                f"Longshot fade: market mid {mid:.3f} vs model {model_p:.3f} "
                f"(overprice {overprice:.3f})"
            ),
            meta=meta,
        )

    if buy_yes_edge_maker >= settings.min_edge:
        size = fractional_kelly(
            buy_yes_edge_maker,
            float(yes_bid or mid),
            settings.paper_bankroll,
            settings.kelly_fraction,
            settings.max_contracts_per_signal,
        )
        return EdgeDecision(
            action="BUY_YES",
            side="YES",
            execution="maker",
            model_p=model_p,
            market_mid=mid,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=spread,
            fee=maker_fee,
            edge=buy_yes_edge_maker,
            suggested_contracts=size,
            reason=f"Maker BUY YES: model {model_p:.3f} vs bid {yes_bid}",
            meta=meta,
        )

    if fade_edge_maker >= settings.min_edge:
        size = fractional_kelly(
            fade_edge_maker,
            1.0 - float(yes_ask or mid),
            settings.paper_bankroll,
            settings.kelly_fraction,
            settings.max_contracts_per_signal,
        )
        return EdgeDecision(
            action="FADE_YES",
            side="NO",
            execution="maker",
            model_p=model_p,
            market_mid=mid,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=spread,
            fee=maker_fee,
            edge=fade_edge_maker,
            suggested_contracts=size,
            reason=f"Maker fade YES: model {model_p:.3f} vs ask {yes_ask}",
            meta=meta,
        )

    # Higher bar for taker
    if buy_yes_edge_taker >= settings.min_edge_taker:
        size = fractional_kelly(
            buy_yes_edge_taker,
            float(yes_ask or mid),
            settings.paper_bankroll,
            settings.kelly_fraction,
            settings.max_contracts_per_signal,
        )
        return EdgeDecision(
            action="BUY_YES",
            side="YES",
            execution="taker",
            model_p=model_p,
            market_mid=mid,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=spread,
            fee=taker_fee,
            edge=buy_yes_edge_taker,
            suggested_contracts=size,
            reason=f"Taker BUY YES clears high bar: edge {buy_yes_edge_taker:.3f}",
            meta=meta,
        )

    if fade_edge_taker >= settings.min_edge_taker:
        size = fractional_kelly(
            fade_edge_taker,
            1.0 - float(yes_bid or mid),
            settings.paper_bankroll,
            settings.kelly_fraction,
            settings.max_contracts_per_signal,
        )
        return EdgeDecision(
            action="FADE_YES",
            side="NO",
            execution="taker",
            model_p=model_p,
            market_mid=mid,
            yes_bid=yes_bid,
            yes_ask=yes_ask,
            spread=spread,
            fee=taker_fee,
            edge=fade_edge_taker,
            suggested_contracts=size,
            reason=f"Taker fade YES clears high bar: edge {fade_edge_taker:.3f}",
            meta=meta,
        )

    best = max(buy_yes_edge_maker, fade_edge_maker, buy_yes_edge_taker, fade_edge_taker)
    return EdgeDecision(
        action="PASS",
        side=None,
        execution="none",
        model_p=model_p,
        market_mid=mid,
        yes_bid=yes_bid,
        yes_ask=yes_ask,
        spread=spread,
        fee=0.0,
        edge=best,
        suggested_contracts=0.0,
        reason=f"No edge above thresholds (best {best:.3f})",
        meta=meta,
    )
