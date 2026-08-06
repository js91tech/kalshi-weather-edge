from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone
from typing import Any

import requests

from .config import ROOT, Settings, load_settings
from .fees import taker_fee_per_contract
from .market_history import KalshiMarketData
from .tickers import parse_event_date


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


def _pnl_yes(
    bid: float,
    won: bool,
    contracts: float = 1.0,
    fee_rate: float = 0.0,
) -> float:
    fee = taker_fee_per_contract(bid, fee_rate) if fee_rate else 0.0
    gross = (1.0 - bid) if won else (-bid)
    return contracts * (gross - fee)


def _pnl_no(
    ask: float,
    won: bool,
    contracts: float = 1.0,
    fee_rate: float = 0.0,
) -> float:
    # Buy NO at ~(1-ask)
    entry = 1.0 - ask
    fee = taker_fee_per_contract(entry, fee_rate) if fee_rate else 0.0
    gross = (1.0 - entry) if won else (-entry)
    return contracts * (gross - fee)


def score_strategy(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    side: str,
    min_mid: float | None = None,
    max_mid: float | None = None,
    series_filter: set[str] | None = None,
    fee_rate: float = 0.0,
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
            trade_pnl = _pnl_yes(float(c["yes_bid"]), won, fee_rate=fee_rate)
        else:
            won = result == "no"
            trade_pnl = _pnl_no(float(c["yes_ask"]), won, fee_rate=fee_rate)
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
        "fee_rate": fee_rate,
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


def score_consensus_strategy(
    candidates: list[dict[str, Any]],
    *,
    name: str,
    yes_threshold: float,
    no_threshold: float,
    series_filter: set[str] | None = None,
    fee_rate: float = 0.0,
) -> dict[str, Any]:
    """Backtest live-style favorites: BUY YES if mid >= yes_threshold, BUY NO if mid <= no_threshold."""
    wins = losses = 0
    pnl = 0.0
    yes_trades = no_trades = 0
    trades: list[dict[str, Any]] = []

    for c in candidates:
        if series_filter and c["series"] not in series_filter:
            continue
        mid = float(c["mid"])
        if mid >= yes_threshold:
            side = "YES"
        elif mid <= no_threshold:
            side = "NO"
        else:
            continue

        result = c["result"]
        if side == "YES":
            won = result == "yes"
            trade_pnl = _pnl_yes(float(c["yes_bid"]), won, fee_rate=fee_rate)
            yes_trades += 1
        else:
            won = result == "no"
            trade_pnl = _pnl_no(float(c["yes_ask"]), won, fee_rate=fee_rate)
            no_trades += 1

        wins += int(won)
        losses += int(not won)
        pnl += trade_pnl
        trades.append({**c, "strategy": name, "side": side, "won": won, "pnl": trade_pnl})

    n = wins + losses
    avg_win, avg_loss = _avg_win_loss(trades)
    return {
        "name": name,
        "yes_threshold": yes_threshold,
        "no_threshold": no_threshold,
        "n_trades": n,
        "yes_trades": yes_trades,
        "no_trades": no_trades,
        "wins": wins,
        "losses": losses,
        "win_rate": (wins / n) if n else 0.0,
        "pnl": pnl,
        "avg_pnl": (pnl / n) if n else 0.0,
        "avg_win_pnl": avg_win,
        "avg_loss_pnl": avg_loss,
        "fee_rate": fee_rate,
        "trades": trades,
    }


def backtest_strategy_profiles(
    candidates: list[dict[str, Any]],
    profiles: list[dict[str, Any]],
    *,
    fee_rate: float = 0.0,
) -> dict[str, Any]:
    """Run consensus backtests for named strategy profiles (favorites, high_profit, etc.)."""
    results: list[dict[str, Any]] = []
    for profile in profiles:
        series = profile.get("series")
        sfilter = set(series) if series else None
        profile_fee = float(profile.get("fee_rate", fee_rate))
        r = score_consensus_strategy(
            candidates,
            name=profile["name"],
            yes_threshold=float(profile["yes_threshold"]),
            no_threshold=float(profile["no_threshold"]),
            series_filter=sfilter,
            fee_rate=profile_fee,
        )
        row = {k: v for k, v in r.items() if k != "trades"}
        row["series"] = series or []
        results.append(row)

    by_series: dict[str, list[dict[str, Any]]] = {}
    for profile in profiles:
        profile_fee = float(profile.get("fee_rate", fee_rate))
        for series in profile.get("series") or []:
            scoped = profile.copy()
            scoped["name"] = f"{profile['name']}|{series}"
            r = score_consensus_strategy(
                candidates,
                name=scoped["name"],
                yes_threshold=float(profile["yes_threshold"]),
                no_threshold=float(profile["no_threshold"]),
                series_filter={series},
                fee_rate=profile_fee,
            )
            row = {k: v for k, v in r.items() if k != "trades"}
            row["series"] = [series]
            by_series.setdefault(profile["name"], []).append(row)

    return {
        "profiles": results,
        "per_series": by_series,
        "fee_rate": fee_rate,
    }


def _avg_win_loss(trades: list[dict[str, Any]]) -> tuple[float, float]:
    wins = [t["pnl"] for t in trades if t.get("won")]
    losses = [t["pnl"] for t in trades if not t.get("won")]
    avg_win = sum(wins) / len(wins) if wins else 0.0
    avg_loss = sum(losses) / len(losses) if losses else 0.0
    return avg_win, avg_loss


def hunt_profit_strategies(
    candidates: list[dict[str, Any]],
    *,
    min_trades: int = 25,
    series_filter: set[str] | None = None,
) -> dict[str, Any]:
    """
    Hunt strategies optimized for higher $/contract: looser favorites, longshots, fades.
    """
    if series_filter:
        candidates = [c for c in candidates if c["series"] in series_filter]

    series_list = sorted({c["series"] for c in candidates})
    scopes: list[tuple[str, set[str] | None]] = [("ALL", None)]
    for s in series_list:
        scopes.append((s, {s}))

    favorite_yes_thresholds = [0.70, 0.75, 0.80, 0.85, 0.90, 0.95]
    favorite_no_thresholds = [0.30, 0.25, 0.20, 0.15, 0.10, 0.05]
    longshot_yes_bands = [
        (None, 0.05),
        (None, 0.10),
        (None, 0.15),
        (None, 0.20),
        (None, 0.30),
        (0.30, 0.50),
        (0.40, 0.60),
    ]
    fade_no_thresholds = [0.80, 0.85, 0.90, 0.95]
    fade_yes_thresholds = [0.05, 0.10, 0.15, 0.20]

    results: list[dict[str, Any]] = []

    def _append(r: dict[str, Any], scope: str, family: str) -> None:
        avg_win, avg_loss = _avg_win_loss(r["trades"])
        row = {k: v for k, v in r.items() if k != "trades"}
        row["scope"] = scope
        row["family"] = family
        row["avg_win_pnl"] = avg_win
        row["avg_loss_pnl"] = avg_loss
        row["_trades"] = r["trades"]
        results.append(row)

    for scope_name, sfilter in scopes:
        for t in favorite_yes_thresholds:
            r = score_strategy(
                candidates,
                name=f"{scope_name}|FAVORITE_YES|mid>={t:.2f}",
                side="YES",
                min_mid=t,
                series_filter=sfilter,
            )
            _append(r, scope_name, "favorite_yes")

        for t in favorite_no_thresholds:
            r = score_strategy(
                candidates,
                name=f"{scope_name}|FAVORITE_NO|mid<={t:.2f}",
                side="NO",
                max_mid=t,
                series_filter=sfilter,
            )
            _append(r, scope_name, "favorite_no")

        for min_m, max_m in longshot_yes_bands:
            label = f"mid<={max_m:.2f}" if min_m is None else f"{min_m:.2f}<=mid<={max_m:.2f}"
            r = score_strategy(
                candidates,
                name=f"{scope_name}|LONGSHOT_YES|{label}",
                side="YES",
                min_mid=min_m,
                max_mid=max_m,
                series_filter=sfilter,
            )
            _append(r, scope_name, "longshot_yes")

        for t in fade_no_thresholds:
            r = score_strategy(
                candidates,
                name=f"{scope_name}|FADE_NO|mid>={t:.2f}",
                side="NO",
                min_mid=t,
                series_filter=sfilter,
            )
            _append(r, scope_name, "fade_no")

        for t in fade_yes_thresholds:
            r = score_strategy(
                candidates,
                name=f"{scope_name}|FADE_YES|mid<={t:.2f}",
                side="YES",
                max_mid=t,
                series_filter=sfilter,
            )
            _append(r, scope_name, "fade_yes")

    eligible = [r for r in results if r["n_trades"] >= min_trades]

    def _clean(r: dict[str, Any]) -> dict[str, Any]:
        out = dict(r)
        out.pop("_trades", None)
        return out

    by_avg_pnl = sorted(eligible, key=lambda r: (r["avg_pnl"], r["pnl"], r["win_rate"]), reverse=True)
    by_pnl = sorted(eligible, key=lambda r: (r["pnl"], r["avg_pnl"]), reverse=True)

    def _best_with_min_win_rate(min_wr: float) -> dict[str, Any] | None:
        pool = [r for r in eligible if r["win_rate"] >= min_wr]
        if not pool:
            return None
        return _clean(sorted(pool, key=lambda r: (r["avg_pnl"], r["pnl"]), reverse=True)[0])

    def _best_avg_pnl_at_least(threshold: float) -> list[dict[str, Any]]:
        pool = [r for r in eligible if r["avg_pnl"] >= threshold]
        return [_clean(r) for r in sorted(pool, key=lambda r: (r["avg_pnl"], r["win_rate"]), reverse=True)[:15]]

    return {
        "n_strategies_tested": len(results),
        "n_eligible": len(eligible),
        "n_candidates": len(candidates),
        "best_avg_pnl": _clean(by_avg_pnl[0]) if by_avg_pnl else None,
        "best_total_pnl": _clean(by_pnl[0]) if by_pnl else None,
        "best_avg_pnl_win_rate_ge_50": _best_with_min_win_rate(0.50),
        "best_avg_pnl_win_rate_ge_60": _best_with_min_win_rate(0.60),
        "best_avg_pnl_win_rate_ge_70": _best_with_min_win_rate(0.70),
        "avg_pnl_ge_0.10": _best_avg_pnl_at_least(0.10),
        "avg_pnl_ge_0.25": _best_avg_pnl_at_least(0.25),
        "avg_pnl_ge_0.50": _best_avg_pnl_at_least(0.50),
        "top20_by_avg_pnl": [_clean(r) for r in by_avg_pnl[:20]],
        "top20_by_total_pnl": [_clean(r) for r in by_pnl[:20]],
    }
