from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from scipy.stats import norm


@dataclass
class BracketProb:
    ticker: str
    strike_type: str
    floor_strike: float | None
    cap_strike: float | None
    model_p: float
    low: float | None
    high: float | None


def _phi(x: float, mu: float, sigma: float) -> float:
    return float(norm.cdf(x, loc=mu, scale=sigma))


def bracket_probability(
    strike_type: str | None,
    floor_strike: float | None,
    cap_strike: float | None,
    mu: float,
    sigma: float,
    continuity: float = 0.5,
) -> tuple[float, float | None, float | None]:
    """
    Map Kalshi temp contract to P(YES) under T ~ N(μ, σ) in °F.

    Observed Kalshi shapes (NYC highs):
      - less    + cap=C     → YES if high <= C-1  (subtitle: "(C-1)° or below")
      - greater + floor=F   → YES if high >= F+1  (subtitle: "(F+1)° or above")
      - between + floor=A, cap=B → YES if A <= high <= B
    Continuity correction for integer settlement.
    """
    st = (strike_type or "").lower()
    sigma = max(float(sigma), 0.1)
    mu = float(mu)

    if st == "less" and cap_strike is not None:
        # high <= cap - 1
        upper = float(cap_strike) - 1.0
        p = _phi(upper + continuity, mu, sigma)
        return _clip01(p), None, upper

    if st == "greater" and floor_strike is not None:
        # high >= floor + 1
        lower = float(floor_strike) + 1.0
        p = 1.0 - _phi(lower - continuity, mu, sigma)
        return _clip01(p), lower, None

    if st == "between" and floor_strike is not None and cap_strike is not None:
        lo = float(floor_strike)
        hi = float(cap_strike)
        p = _phi(hi + continuity, mu, sigma) - _phi(lo - continuity, mu, sigma)
        return _clip01(p), lo, hi

    # Fallback: try between-like if both strikes present
    if floor_strike is not None and cap_strike is not None:
        lo = float(floor_strike)
        hi = float(cap_strike)
        p = _phi(hi + continuity, mu, sigma) - _phi(lo - continuity, mu, sigma)
        return _clip01(p), lo, hi

    return 0.0, None, None


def market_bracket_prob(market: dict[str, Any], mu: float, sigma: float, continuity: float) -> BracketProb:
    p, lo, hi = bracket_probability(
        market.get("strike_type"),
        _num(market.get("floor_strike")),
        _num(market.get("cap_strike")),
        mu,
        sigma,
        continuity,
    )
    return BracketProb(
        ticker=market["ticker"],
        strike_type=str(market.get("strike_type") or ""),
        floor_strike=_num(market.get("floor_strike")),
        cap_strike=_num(market.get("cap_strike")),
        model_p=p,
        low=lo,
        high=hi,
    )


def _clip01(p: float) -> float:
    return float(min(1.0, max(0.0, p)))


def _num(v: Any) -> float | None:
    if v is None or v == "":
        return None
    return float(v)
