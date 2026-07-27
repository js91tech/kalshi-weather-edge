from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from typing import Any

from .brackets import market_bracket_prob
from .config import City, Settings, load_settings, with_overrides
from .edge import evaluate_market
from .market_history import KalshiMarketData
from .pipeline import _cap_trades_per_event, parse_event_date
from .weather import fetch_historical_highs


def _pnl_for_trade(
    *,
    side: str | None,
    result: str,
    yes_bid: float,
    yes_ask: float,
    contracts: float,
) -> tuple[int, float]:
    """Return (win_flag 1/0, pnl dollars)."""
    result = (result or "").lower()
    if result not in ("yes", "no") or not side:
        return 0, 0.0
    side = side.upper()
    if side == "YES":
        entry = float(yes_bid)
        won = result == "yes"
        pnl = contracts * ((1.0 - entry) if won else (-entry))
    else:
        # Buy NO at ~ (1 - yes_ask)
        entry = 1.0 - float(yes_ask)
        won = result == "no"
        pnl = contracts * ((1.0 - entry) if won else (-entry))
    return (1 if won else 0), float(pnl)


def collect_backtest_universe(
    settings: Settings | None = None,
    *,
    lookback_days: int | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
    cities: list[City] | None = None,
) -> dict[str, Any]:
    """
    Pull settled Kalshi weather markets + historical forecasts + candle entry quotes.
    Returns candidates (pre-decision) for scoring / fine-tuning.

    If start_date is set (e.g. '2026-01-01'), it overrides lookback_days.
    """
    settings = settings or load_settings()
    cities = cities or settings.cities
    client = KalshiMarketData(settings.kalshi_base_url)

    end = date.fromisoformat(end_date) if isinstance(end_date, str) else (end_date or date.today())
    if start_date is not None:
        start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    else:
        lookback_days = lookback_days or settings.backtest_lookback_days
        start = end - timedelta(days=lookback_days)
    start_s, end_s = start.isoformat(), end.isoformat()

    candidates: list[dict[str, Any]] = []
    errors: list[str] = []

    for city in cities:
        try:
            forecasts = fetch_historical_highs(
                city.lat,
                city.lon,
                city.timezone,
                start_s,
                end_s,
                sigma_floor=settings.sigma_floor,
                sigma_scale=settings.sigma_scale,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{city.id} hist forecast: {exc}")
            continue

        try:
            markets = client.get_markets(
                city.series_ticker,
                status="settled",
                limit=settings.backtest_max_markets_per_city,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{city.id} settled markets: {exc}")
            continue

        eligible: list[dict[str, Any]] = []
        for market in markets:
            result = (market.get("result") or "").lower()
            if result not in ("yes", "no"):
                continue
            event_ticker = market.get("event_ticker") or ""
            target_date = parse_event_date(event_ticker)
            if not target_date:
                continue
            try:
                td = date.fromisoformat(target_date)
            except ValueError:
                continue
            if td < start or td > end:
                continue
            fc = forecasts.get(target_date)
            if not fc:
                continue
            eligible.append({"market": market, "target_date": target_date, "fc": fc, "result": result})

        def _quote_one(item: dict[str, Any]) -> dict[str, Any] | None:
            market = item["market"]
            quote = client.entry_quote_from_candles(
                city.series_ticker,
                market["ticker"],
                market.get("close_time"),
                hours_before_close=settings.backtest_entry_hours_before_close,
            )
            if quote.get("yes_bid") is None or quote.get("yes_ask") is None:
                return None
            bp = market_bracket_prob(
                market,
                item["fc"].mu,
                item["fc"].sigma,
                settings.continuity_correction,
            )
            return {
                "city_id": city.id,
                "city_name": city.name,
                "longshot_bias": city.longshot_bias,
                "ticker": market["ticker"],
                "event_ticker": market.get("event_ticker"),
                "target_date": item["target_date"],
                "result": item["result"],
                "strike_type": market.get("strike_type"),
                "subtitle": market.get("subtitle") or market.get("no_sub_title"),
                "raw_model_p": bp.model_p,
                "yes_bid": float(quote["yes_bid"]),
                "yes_ask": float(quote["yes_ask"]),
                "market_mid": float(quote["mid"] or 0),
                "quote_source": quote.get("source"),
                "mu": item["fc"].mu,
                "sigma": item["fc"].sigma,
                "city": city,
            }

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = [pool.submit(_quote_one, item) for item in eligible]
            for fut in as_completed(futures):
                try:
                    row = fut.result()
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"{city.id} candle: {exc}")
                    continue
                if row:
                    candidates.append(row)

        client.flush_cache()

    # Actual data coverage may be shorter than requested window
    dated = [c["target_date"] for c in candidates]
    return {
        "start_date": start_s,
        "end_date": end_s,
        "data_start": min(dated) if dated else start_s,
        "data_end": max(dated) if dated else end_s,
        "candidates": candidates,
        "n_candidates": len(candidates),
        "errors": errors,
    }


def score_universe(
    candidates: list[dict[str, Any]],
    settings: Settings,
) -> dict[str, Any]:
    """Apply current edge rules and compute wins/losses/PnL on historical candidates."""
    rows: list[dict[str, Any]] = []
    city_objs = {c.id: c for c in settings.cities}

    # Score every candidate, then cap trades per city/date
    provisional: list[dict[str, Any]] = []
    for c in candidates:
        city = c.get("city") or city_objs.get(c["city_id"])
        if city is None:
            continue
        # Rebuild City if serialized
        if not isinstance(city, City):
            city = City(
                id=c["city_id"],
                name=c.get("city_name") or c["city_id"],
                series_ticker="",
                lat=0.0,
                lon=0.0,
                timezone="UTC",
                longshot_bias=float(c.get("longshot_bias") or 0.03),
            )

        decision = evaluate_market(
            model_p=float(c["raw_model_p"]),
            yes_bid=float(c["yes_bid"]),
            yes_ask=float(c["yes_ask"]),
            last=float(c["market_mid"]),
            city=city,
            settings=settings,
        )
        provisional.append(
            {
                "city_id": c["city_id"],
                "city_name": c.get("city_name"),
                "ticker": c["ticker"],
                "event_ticker": c.get("event_ticker"),
                "target_date": c["target_date"],
                "result": c["result"],
                "subtitle": c.get("subtitle"),
                "action": decision.action,
                "side": decision.side,
                "execution": decision.execution,
                "model_p": decision.model_p,
                "raw_model_p": c["raw_model_p"],
                "market_mid": decision.market_mid,
                "yes_bid": c["yes_bid"],
                "yes_ask": c["yes_ask"],
                "edge": decision.edge,
                "reason": decision.reason,
                "mu": c.get("mu"),
                "sigma": c.get("sigma"),
            }
        )

    capped = _cap_trades_per_event(provisional, settings.max_trades_per_event)
    contracts = float(settings.backtest_contracts_per_trade)

    wins = losses = passes = 0
    pnl_total = 0.0
    trade_rows: list[dict[str, Any]] = []

    for row in capped:
        if row["action"] == "PASS":
            passes += 1
            row["won"] = None
            row["pnl"] = 0.0
            rows.append(row)
            continue
        won, pnl = _pnl_for_trade(
            side=row.get("side"),
            result=row["result"],
            yes_bid=float(row["yes_bid"]),
            yes_ask=float(row["yes_ask"]),
            contracts=contracts,
        )
        row["won"] = bool(won)
        row["pnl"] = pnl
        rows.append(row)
        trade_rows.append(row)
        if won:
            wins += 1
        else:
            losses += 1
        pnl_total += pnl

    n_trades = wins + losses
    win_rate = (wins / n_trades) if n_trades else 0.0
    return {
        "params": {
            "min_edge": settings.min_edge,
            "min_edge_taker": settings.min_edge_taker,
            "market_shrinkage": settings.market_shrinkage,
            "longshot_overprice_min": settings.longshot_overprice_min,
            "longshot_market_max": settings.longshot_market_max,
            "max_trades_per_event": settings.max_trades_per_event,
        },
        "n_candidates": len(candidates),
        "n_trades": n_trades,
        "wins": wins,
        "losses": losses,
        "passes": passes,
        "win_rate": win_rate,
        "pnl": pnl_total,
        "avg_pnl_per_trade": (pnl_total / n_trades) if n_trades else 0.0,
        "rows": rows,
        "trades": trade_rows,
    }


def run_backtest(
    settings: Settings | None = None,
    lookback_days: int | None = None,
    start_date: str | date | None = None,
    end_date: str | date | None = None,
) -> dict[str, Any]:
    settings = settings or load_settings()
    universe = collect_backtest_universe(
        settings,
        lookback_days=lookback_days,
        start_date=start_date,
        end_date=end_date,
    )
    scored = score_universe(universe["candidates"], settings)
    return {
        **scored,
        "start_date": universe["start_date"],
        "end_date": universe["end_date"],
        "data_start": universe.get("data_start"),
        "data_end": universe.get("data_end"),
        "errors": universe["errors"],
        "candidates": universe["candidates"],
    }


def fine_tune(
    candidates: list[dict[str, Any]],
    settings: Settings | None = None,
) -> dict[str, Any]:
    """
    Grid-search edge params for best historical win rate, then PnL.
    Requires >= 1 trade to rank a param set.
    """
    settings = settings or load_settings()
    results: list[dict[str, Any]] = []

    for min_edge in settings.tune_min_edge:
        for shrink in settings.tune_shrinkage:
            for longshot_over in settings.tune_longshot_overprice:
                tuned = with_overrides(
                    settings,
                    min_edge=float(min_edge),
                    market_shrinkage=float(shrink),
                    longshot_overprice_min=float(longshot_over),
                )
                scored = score_universe(candidates, tuned)
                results.append(
                    {
                        "min_edge": min_edge,
                        "market_shrinkage": shrink,
                        "longshot_overprice_min": longshot_over,
                        "n_trades": scored["n_trades"],
                        "wins": scored["wins"],
                        "losses": scored["losses"],
                        "win_rate": scored["win_rate"],
                        "pnl": scored["pnl"],
                        "avg_pnl_per_trade": scored["avg_pnl_per_trade"],
                    }
                )

    # Prefer more trades only as a weak tie-break; primary = win_rate then pnl
    ranked = sorted(
        [r for r in results if r["n_trades"] >= 3],
        key=lambda r: (r["win_rate"], r["pnl"], r["n_trades"]),
        reverse=True,
    )
    if not ranked:
        ranked = sorted(results, key=lambda r: (r["win_rate"], r["pnl"]), reverse=True)

    best = ranked[0] if ranked else None
    return {"results": results, "ranked": ranked, "best": best}


def apply_best_params_to_settings(settings: Settings, best: dict[str, Any]) -> Settings:
    return with_overrides(
        settings,
        min_edge=float(best["min_edge"]),
        market_shrinkage=float(best["market_shrinkage"]),
        longshot_overprice_min=float(best["longshot_overprice_min"]),
    )


def persist_tuned_params(best: dict[str, Any], config_path: Any = None) -> None:
    """Write winning fine-tune params back into config.yaml edge section."""
    import yaml
    from pathlib import Path
    from .config import ROOT

    path = Path(config_path) if config_path else (ROOT / "config.yaml")
    with path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw.setdefault("edge", {})
    raw["edge"]["min_edge"] = float(best["min_edge"])
    raw["edge"]["market_shrinkage"] = float(best["market_shrinkage"])
    raw["edge"]["longshot_overprice_min"] = float(best["longshot_overprice_min"])
    with path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False)
