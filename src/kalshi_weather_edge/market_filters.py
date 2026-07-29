from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


def parse_close_time(market: dict[str, Any]) -> datetime | None:
    raw = market.get("close_time") or market.get("expiration_time")
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except Exception:
        return None


def hours_until_close(market: dict[str, Any], *, now: datetime | None = None) -> float | None:
    close = parse_close_time(market)
    if close is None:
        return None
    now = now or datetime.now(timezone.utc)
    if close.tzinfo is None:
        close = close.replace(tzinfo=timezone.utc)
    return (close - now).total_seconds() / 3600.0


def is_closing_soon(market: dict[str, Any], within_hours: float) -> bool:
    """True if market closes within within_hours (or close time unknown -> allow)."""
    if within_hours <= 0:
        return True
    hrs = hours_until_close(market)
    if hrs is None:
        return True
    return 0 < hrs <= within_hours


def dedup_open_trades(
    signals: list[dict[str, Any]],
    open_tickers: set[str],
) -> list[dict[str, Any]]:
    """PASS duplicate trade suggestions for tickers already in open paper book."""
    if not open_tickers:
        return signals
    out: list[dict[str, Any]] = []
    for s in signals:
        if s.get("action") == "PASS" or s.get("ticker") not in open_tickers:
            out.append(s)
            continue
        dup = dict(s)
        dup["action"] = "PASS"
        dup["side"] = None
        dup["execution"] = "none"
        dup["suggested_contracts"] = 0.0
        dup["reason"] = "Already tracking open paper trade for this ticker"
        meta = dict(dup.get("meta") or {})
        meta["deduped"] = True
        dup["meta"] = meta
        out.append(dup)
    return out


def filter_closing_soon(
    signals: list[dict[str, Any]],
    markets_by_ticker: dict[str, dict[str, Any]],
    within_hours: float,
) -> list[dict[str, Any]]:
    """PASS markets that are not closing within the configured window."""
    if within_hours <= 0:
        return signals
    out: list[dict[str, Any]] = []
    for s in signals:
        m = markets_by_ticker.get(s.get("ticker") or "")
        if s.get("action") == "PASS" or is_closing_soon(m or {}, within_hours):
            if m and s.get("action") != "PASS":
                hrs = hours_until_close(m)
                meta = dict(s.get("meta") or {})
                meta["hours_to_close"] = round(hrs, 2) if hrs is not None else None
                s = dict(s)
                s["meta"] = meta
            out.append(s)
            continue
        hrs = hours_until_close(m or {})
        if hrs is not None and hrs <= 0:
            reason = "Market already closed or closing now"
        elif hrs is not None:
            reason = f"Closes in {hrs:.1f}h — outside {within_hours:.0f}h window"
        else:
            reason = "Close time unknown / too far out"
        late = dict(s)
        late["action"] = "PASS"
        late["side"] = None
        late["execution"] = "none"
        late["suggested_contracts"] = 0.0
        late["reason"] = reason
        meta = dict(late.get("meta") or {})
        meta["filtered_close"] = True
        late["meta"] = meta
        out.append(late)
    return out
