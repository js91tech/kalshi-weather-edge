from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from .brackets import market_bracket_prob
from .config import Settings, load_settings
from .db import Ledger, utc_now
from .edge import evaluate_market
from .fees import dollar
from .kalshi_client import KalshiClient
from .weather import fetch_ensemble_highs


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
    """KXHIGHNY-26JUL28 → 2026-07-28"""
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


def run_pipeline(settings: Settings | None = None, notes: str = "manual") -> dict[str, Any]:
    settings = settings or load_settings()
    ledger = Ledger(settings.db_path)
    client = KalshiClient(settings.kalshi_base_url)
    run_id = ledger.start_run(notes=notes)

    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    trade_count = 0
    pass_count = 0

    try:
        for city in settings.cities:
            try:
                forecasts = fetch_ensemble_highs(
                    city.lat,
                    city.lon,
                    city.timezone,
                    forecast_days=3,
                    sigma_floor=settings.sigma_floor,
                    sigma_scale=settings.sigma_scale,
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{city.id} forecast: {exc}")
                continue

            forecast_by_date = {f.target_date: f for f in forecasts}
            for fc in forecasts:
                ledger.insert_forecast(
                    run_id,
                    city.id,
                    fc.target_date,
                    fc.mu,
                    fc.sigma,
                    fc.p10,
                    fc.p50,
                    fc.p90,
                    fc.source,
                )

            try:
                markets = client.get_markets(city.series_ticker, status="open", limit=200)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{city.id} markets: {exc}")
                continue

            # Also pull recently settled for ledger backfill
            try:
                settled = client.get_markets(city.series_ticker, status="settled", limit=50)
                for m in settled:
                    ledger.upsert_settlement(m)
            except Exception:
                pass

            city_signals: list[dict[str, Any]] = []
            for market in markets:
                ledger.insert_market(run_id, city.id, market)
                event_ticker = market.get("event_ticker") or ""
                target_date = parse_event_date(event_ticker)
                if not target_date or target_date not in forecast_by_date:
                    continue
                fc = forecast_by_date[target_date]
                bp = market_bracket_prob(
                    market,
                    fc.mu,
                    fc.sigma,
                    settings.continuity_correction,
                )
                yes_bid = dollar(market.get("yes_bid_dollars"))
                yes_ask = dollar(market.get("yes_ask_dollars"))
                last = dollar(market.get("last_price_dollars"))
                decision = evaluate_market(
                    model_p=bp.model_p,
                    yes_bid=yes_bid,
                    yes_ask=yes_ask,
                    last=last,
                    city=city,
                    settings=settings,
                )

                signal = {
                    "created_at": utc_now(),
                    "city_id": city.id,
                    "ticker": market["ticker"],
                    "event_ticker": event_ticker,
                    "target_date": target_date,
                    "action": decision.action,
                    "side": decision.side,
                    "execution": decision.execution,
                    "model_p": decision.model_p,
                    "market_mid": decision.market_mid,
                    "yes_bid": decision.yes_bid,
                    "yes_ask": decision.yes_ask,
                    "spread": decision.spread,
                    "fee": decision.fee,
                    "edge": decision.edge,
                    "suggested_contracts": decision.suggested_contracts,
                    "reason": decision.reason,
                    "meta": {
                        **decision.meta,
                        "mu": fc.mu,
                        "sigma": fc.sigma,
                        "p10": fc.p10,
                        "p50": fc.p50,
                        "p90": fc.p90,
                        "strike_type": bp.strike_type,
                        "subtitle": market.get("subtitle") or market.get("no_sub_title"),
                        "city_name": city.name,
                        "raw_bracket_p": bp.model_p,
                    },
                }
                city_signals.append(signal)

            # Cap non-PASS signals per city+date to avoid correlated overtrading
            capped = _cap_trades_per_event(city_signals, settings.max_trades_per_event)
            for signal in capped:
                ledger.insert_signal(run_id, signal)
                rows.append(signal)
                if signal["action"] == "PASS":
                    pass_count += 1
                else:
                    trade_count += 1

        settled_applied = ledger.apply_settlements_to_signals()
        ledger.finish_run(run_id)
    except Exception:
        ledger.finish_run(run_id)
        raise

    return {
        "run_id": run_id,
        "mode": settings.mode,
        "markets_scored": len(rows),
        "trade_signals": trade_count,
        "pass_signals": pass_count,
        "settlements_applied": settled_applied,
        "errors": errors,
        "rows": rows,
        "stats": ledger.signal_stats(),
    }


def _cap_trades_per_event(signals: list[dict[str, Any]], max_trades: int) -> list[dict[str, Any]]:
    if max_trades <= 0:
        return signals
    from collections import defaultdict

    by_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for s in signals:
        key = (s.get("city_id") or "", s.get("target_date") or "")
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
            s["reason"] = (
                f"Capped: kept top {max_trades} |edge| for this city/date — "
                f"was {s.get('reason')}"
            )
            meta = dict(s.get("meta") or {})
            meta["capped"] = True
            s["meta"] = meta
            passes.append(s)
        out.extend(keep)
        out.extend(passes)
    return out


def rows_to_dataframe_records(result: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for r in result.get("rows") or []:
        meta = r.get("meta") or {}
        out.append(
            {
                "city": meta.get("city_name") or r.get("city_id"),
                "date": r.get("target_date"),
                "ticker": r.get("ticker"),
                "subtitle": meta.get("subtitle"),
                "action": r.get("action"),
                "side": r.get("side"),
                "execution": r.get("execution"),
                "model_p": r.get("model_p"),
                "market_mid": r.get("market_mid"),
                "edge": r.get("edge"),
                "contracts": r.get("suggested_contracts"),
                "mu": meta.get("mu"),
                "sigma": meta.get("sigma"),
                "reason": r.get("reason"),
            }
        )
    return out


if __name__ == "__main__":
    import json

    result = run_pipeline(notes="cli")
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    trades = [r for r in result["rows"] if r["action"] != "PASS"]
    print(f"\nTrade signals: {len(trades)}")
    for t in trades[:20]:
        print(
            t["action"],
            t["ticker"],
            f"model={t['model_p']:.3f}",
            f"mid={t['market_mid']:.3f}",
            f"edge={t['edge']:.3f}",
            t["reason"],
        )
