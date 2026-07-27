from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class FavoriteDecision:
    action: str  # PASS | BUY_YES | BUY_NO
    side: str | None
    execution: str
    market_mid: float
    edge: float
    reason: str
    suggested_contracts: float


def evaluate_favorite(
    *,
    yes_bid: float | None,
    yes_ask: float | None,
    mid: float | None,
    yes_threshold: float = 0.90,
    no_threshold: float = 0.10,
    contracts: float = 1.0,
) -> FavoriteDecision:
    """
    High hit-rate strategy: trade with strong market consensus.
    - BUY YES when mid >= yes_threshold
    - BUY NO when mid <= no_threshold
    Backtests across gas/FX/weather showed ~95%+ hit rates at extreme thresholds.
    """
    if mid is None:
        if yes_bid is not None and yes_ask is not None:
            mid = (float(yes_bid) + float(yes_ask)) / 2.0
        else:
            return FavoriteDecision("PASS", None, "none", 0.0, 0.0, "No mid", 0.0)

    mid = float(mid)
    if mid >= yes_threshold:
        return FavoriteDecision(
            action="BUY_YES",
            side="YES",
            execution="maker",
            market_mid=mid,
            edge=mid - 0.5,
            reason=f"Favorite YES: mid {mid:.3f} >= {yes_threshold:.2f}",
            suggested_contracts=contracts,
        )
    if mid <= no_threshold:
        return FavoriteDecision(
            action="BUY_NO",
            side="NO",
            execution="maker",
            market_mid=mid,
            edge=0.5 - mid,
            reason=f"Favorite NO: mid {mid:.3f} <= {no_threshold:.2f}",
            suggested_contracts=contracts,
        )
    return FavoriteDecision(
        action="PASS",
        side=None,
        execution="none",
        market_mid=mid,
        edge=0.0,
        reason=f"Mid {mid:.3f} not extreme enough",
        suggested_contracts=0.0,
    )
