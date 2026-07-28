from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime
from typing import Any

EVENT_DATE_RE = re.compile(r"-(\d{2})([A-Z]{3})(\d{2})$")

MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def parse_event_date(event_ticker: str) -> str | None:
    """KXHIGHNY-26JUL28 -> 2026-07-28"""
    m = EVENT_DATE_RE.search(event_ticker or "")
    if not m:
        return None
    yy, mon, dd = m.group(1), m.group(2), m.group(3)
    month = MONTHS.get(mon)
    if not month:
        return None
    year = 2000 + int(yy)
    try:
        return datetime(year, month, int(dd)).date().isoformat()
    except ValueError:
        return None


def cap_trades_per_event(signals: list[dict[str, Any]], max_trades: int) -> list[dict[str, Any]]:
    if max_trades <= 0:
        return signals

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        event = s.get("event_ticker") or s.get("target_date") or s.get("ticker") or ""
        key = (s.get("city_id") or "", str(event))
        by_key[key].append(s)

    out: list[dict[str, Any]] = []
    for group in by_key.values():
        trades = [s for s in group if s["action"] != "PASS"]
        passes = [s for s in group if s["action"] == "PASS"]
        trades_sorted = sorted(trades, key=lambda x: abs(float(x.get("edge") or 0)), reverse=True)
        keep = trades_sorted[:max_trades]
        drop = trades_sorted[max_trades:]
        for s in drop:
            s = dict(s)
            s["action"] = "PASS"
            s["side"] = None
            s["execution"] = "none"
            s["suggested_contracts"] = 0.0
            s["reason"] = f"capped: max {max_trades} trades per event — was {s.get('reason')}"
            meta = dict(s.get("meta") or {})
            meta["capped"] = True
            s["meta"] = meta
            passes.append(s)
        out.extend(keep)
        out.extend(passes)
    return out
