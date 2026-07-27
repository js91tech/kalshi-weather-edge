from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any

import requests

from .config import ROOT, Settings, load_settings
from .market_history import KalshiMarketData
from .pipeline import parse_event_date


DEFAULT_SERIES = [
    # Daily / frequent — best sample size
    "KXAAAGASD",  # AAA gas prices
    "KXEURUSD",  # EUR/USD thresholds
    "KXNASDAQDUD",  # Nasdaq above/below
    "KXHIGHNY",
    "KXHIGHCHI",
    "KXHIGHMIA",
    "KXHIGHLAX",
    "KXHIGHDEN",
    "KXHIGHAUS",
    # Monthly econ (smaller N)
    "KXCPINDEX",
    "KXUSNFP",
]

# Fast first-pass set (depth + variety)
FAST_SERIES = [
    "KXAAAGASD",
    "KXEURUSD",
    "KXNASDAQDUD",
    "KXHIGHNY",
    "KXHIGHCHI",
    "KXHIGHLAX",
    "KXCPINDEX",
    "KXUSNFP",
]


def _get_json(url: str, params: dict[str, Any] | None = None, timeout: float = 30.0) -> dict[str, Any]:
    last: Exception | None = None
    for attempt in range(5):
        try:
            resp = requests.get(url, params=params or {}, timeout=timeout, headers={"Accept": "application/json"})
            if resp.status_code == 429:
                time.sleep(0.5 * (2**attempt))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(0.3 * (2**attempt))
    raise RuntimeError(f"GET failed {url}: {last}")


def fetch_settled_markets(
    series_ticker: str,
    base_url: str,
    *,
    max_markets: int = 2500,
    include_historical: bool = True,
) -> list[dict[str, Any]]:
    """Pull settled markets from live + historical tiers."""
    markets: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _ingest(batch: list[dict[str, Any]]) -> None:
        for m in batch:
            t = m.get("ticker")
            if not t or t in seen:
                continue
            if (m.get("result") or "").lower() not in ("yes", "no"):
                continue
            seen.add(t)
            markets.append(m)

    # Live settled
    cursor: str | None = None
    while len(markets) < max_markets:
        params: dict[str, Any] = {
            "series_ticker": series_ticker,
            "status": "settled",
            "limit": 200,
        }
        if cursor:
            params["cursor"] = cursor
        data = _get_json(f"{base_url}/markets", params)
        batch = data.get("markets") or []
        if not batch:
            break
        _ingest(batch)
        cursor = data.get("cursor")
        if not cursor:
            break

    if include_historical:
        cursor = None
        while len(markets) < max_markets:
            params = {"series_ticker": series_ticker, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            try:
                data = _get_json(f"{base_url}/historical/markets", params)
            except Exception:
                break
            batch = data.get("markets") or []
            if not batch:
                break
            _ingest(batch)
            cursor = data.get("cursor")
            if not cursor:
                break

    return markets[:max_markets]


def market_event_date(market: dict[str, Any]) -> str | None:
    d = parse_event_date(market.get("event_ticker") or "")
    if d:
        return d
    # Fallback: close_time date
    ct = market.get("close_time")
    if not ct:
        return None
    try:
        return datetime.fromisoformat(ct.replace("Z", "+00:00")).date().isoformat()
    except Exception:
        return None


def build_candidates(
    series_tickers: list[str],
    settings: Settings | None = None,
    *,
    start_date: str = "2026-01-01",
    end_date: str | None = None,
    max_markets_per_series: int = 2500,
    entry_hours_before_close: int = 12,
    max_workers: int = 4,
) -> dict[str, Any]:
    settings = settings or load_settings()
    end = end_date or date.today().isoformat()
    start = start_date
    client = KalshiMarketData(settings.kalshi_base_url)
    candidates: list[dict[str, Any]] = []
    errors: list[str] = []
    series_stats: dict[str, Any] = {}

    for series in series_tickers:
        try:
            markets = fetch_settled_markets(
                series,
                settings.kalshi_base_url,
                max_markets=max_markets_per_series,
                include_historical=True,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{series} fetch: {exc}")
            continue

        eligible = []
        for m in markets:
            ed = market_event_date(m)
            if not ed or ed < start or ed > end:
                continue
            eligible.append(m)

        print(f"[{series}] fetched={len(markets)} eligible={len(eligible)}", flush=True)

        def _one(m: dict[str, Any], series_ticker: str = series) -> dict[str, Any] | None:
            quote = client.entry_quote_from_candles(
                series_ticker,
                m["ticker"],
                m.get("close_time"),
                hours_before_close=entry_hours_before_close,
            )
            mid = quote.get("mid")
            if mid is None or quote.get("yes_bid") is None or quote.get("yes_ask") is None:
                return None
            return {
                "series": series_ticker,
                "ticker": m["ticker"],
                "event_ticker": m.get("event_ticker"),
                "event_date": market_event_date(m),
                "result": (m.get("result") or "").lower(),
                "title": m.get("title"),
                "subtitle": m.get("subtitle") or m.get("no_sub_title"),
                "strike_type": m.get("strike_type"),
                "floor_strike": m.get("floor_strike"),
                "cap_strike": m.get("cap_strike"),
                "yes_bid": float(quote["yes_bid"]),
                "yes_ask": float(quote["yes_ask"]),
                "mid": float(mid),
                "quote_source": quote.get("source"),
            }

        got = 0
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futs = [pool.submit(_one, m) for m in eligible]
            for fut in as_completed(futs):
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{series} quote: {exc}")
                    continue
                if row:
                    candidates.append(row)
                    got += 1
        client.flush_cache()
        print(f"[{series}] quotes={got}", flush=True)
        series_stats[series] = {
            "settled_fetched": len(markets),
            "in_window": len(eligible),
            "with_quotes": got,
        }

    dated = [c["event_date"] for c in candidates if c.get("event_date")]
    return {
        "start_date": start,
        "end_date": end,
        "data_start": min(dated) if dated else start,
        "data_end": max(dated) if dated else end,
        "n_candidates": len(candidates),
        "candidates": candidates,
        "series_stats": series_stats,
        "errors": errors[:50],
    }


def _pnl_yes(bid: float, won: bool, contracts: float = 1.0) -> float:
    return contracts * ((1.0 - bid) if won else (-bid))


def _pnl_no(ask: float, won: bool, contracts: float = 1.0) -> float:
    # Buy NO at ~(1-ask)
    entry = 1.0 - ask
    return contracts * ((1.0 - entry) if won else (-entry))


def score_strategy(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    side: str,
    min_mid: float | None = None,
    max_mid: float | None = None,
    series_filter: set[str] | None = None,
) -> dict[str, Any]:
    """
    side='YES' buys YES when mid in [min_mid, max_mid]
    side='NO' buys NO when mid in [min_mid, max_mid] (typically low YES mid = favorite NO)
    """
    wins = losses = 0
    pnl = 0.0
    trades: list[dict[str, Any]] = []
    for c in candidates:
        if series_filter and c["series"] not in series_filter:
            continue
        mid = float(c["mid"])
        if min_mid is not None and mid < min_mid:
            continue
        if max_mid is not None and mid > max_mid:
            continue
        result = c["result"]
        if side == "YES":
            won = result == "yes"
            trade_pnl = _pnl_yes(float(c["yes_bid"]), won)
        else:
            won = result == "no"
            trade_pnl = _pnl_no(float(c["yes_ask"]), won)
        wins += int(won)
        losses += int(not won)
        pnl += trade_pnl
        trades.append({**c, "strategy": name, "side": side, "won": won, "pnl": trade_pnl})

    n = wins + losses
    return {
        "name": name,
        "side": side,
        "min_mid": min_mid,
        "max_mid": max_mid,
        "n_trades": n,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else 0.0,
        "pnl": pnl,
        "avg_pnl": (pnl / n) if n else 0.0,
        "trades": trades,
    }


def hunt_hit_rates(
    candidates: list[dict[str, Any]],
    *,
    min_trades: int = 30,
) -> dict[str, Any]:
    """Grid of favorite/fade strategies across all series and per-series."""
    series_list = sorted({c["series"] for c in candidates})
    thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    results: list[dict[str, Any]] = []

    scopes: list[tuple[str, set[str] | None]] = [("ALL", None)]
    for s in series_list:
        scopes.append((s, {s}))

    for scope_name, sfilter in scopes:
        for t in thresholds:
            # Favorite YES: market already prices YES highly
            r = score_strategy(
                candidates,
                name=f"{scope_name}|FAVORITE_YES|mid>={t:.2f}",
                side="YES",
                min_mid=t,
                series_filter=sfilter,
            )
            results.append({k: v for k, v in r.items() if k != "trades"})
            results[-1]["scope"] = scope_name
            results[-1]["family"] = "favorite_yes"
            results[-1]["_trades"] = r["trades"]

            # Favorite NO: YES mid very low
            r2 = score_strategy(
                candidates,
                name=f"{scope_name}|FAVORITE_NO|mid<={1-t:.2f}",
                side="NO",
                max_mid=1.0 - t,
                series_filter=sfilter,
            )
            results.append({k: v for k, v in r2.items() if k != "trades"})
            results[-1]["scope"] = scope_name
            results[-1]["family"] = "favorite_no"
            results[-1]["_trades"] = r2["trades"]

    # Rank by win_rate then pnl, require min trades
    eligible = [r for r in results if r["n_trades"] >= min_trades]
    ranked = sorted(eligible, key=lambda r: (r["win_rate"], r["pnl"], r["n_trades"]), reverse=True)
    # Also track best positive-EV among high hit-rate (>=70%)
    high_hit = [r for r in ranked if r["win_rate"] >= 0.70]
    best_high = high_hit[0] if high_hit else None
    best_ev_high = (
        sorted(high_hit, key=lambda r: (r["pnl"], r["win_rate"]), reverse=True)[0] if high_hit else None
    )

    # Strip heavy trade lists from ranked for summary (keep top only)
    clean_ranked = []
    for r in ranked[:40]:
        clean = dict(r)
        clean.pop("_trades", None)
        clean_ranked.append(clean)

    return {
        "n_strategies_tested": len(results),
        "n_eligible": len(eligible),
        "ranked": clean_ranked,
        "best": clean_ranked[0] if clean_ranked else None,
        "best_hit_rate_ge_70": (
            {k: v for k, v in best_high.items() if k != "_trades"} if best_high else None
        ),
        "best_pnl_among_hit_rate_ge_70": (
            {k: v for k, v in best_ev_high.items() if k != "_trades"} if best_ev_high else None
        ),
        "all_high_hit": [
            {k: v for k, v in r.items() if k != "_trades"}
            for r in ranked
            if r["win_rate"] >= 0.70
        ][:25],
    }
