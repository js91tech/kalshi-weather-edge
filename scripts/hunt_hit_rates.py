#!/usr/bin/env python
"""Hunt Kalshi series/strategies for high historical hit rates (YTD / available history)."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.hit_rate_scan import (
    DEFAULT_SERIES,
    FAST_SERIES,
    build_candidates,
    hunt_hit_rates,
)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fast", action="store_true", help="Fewer series, capped markets")
    parser.add_argument("--max-per-series", type=int, default=0)
    parser.add_argument("--min-trades", type=int, default=25)
    args = parser.parse_args()

    year = date.today().year
    start = f"{year}-01-01"
    settings = load_settings()
    series = FAST_SERIES if args.fast else DEFAULT_SERIES
    max_per = args.max_per_series or (500 if args.fast else 2500)

    print(f"Building candidates from {start} (live + historical settled)...", flush=True)
    print("Series:", ", ".join(series), flush=True)
    print(f"max_markets_per_series={max_per}", flush=True)

    universe = build_candidates(
        series,
        settings,
        start_date=start,
        max_markets_per_series=max_per,
        entry_hours_before_close=12,
        max_workers=6,
    )
    print(
        f"Candidates: {universe['n_candidates']} | data {universe['data_start']} -> {universe['data_end']}",
        flush=True,
    )
    print("Series stats:", json.dumps(universe["series_stats"], indent=2), flush=True)
    if universe["errors"]:
        print("Errors sample:", universe["errors"][:5], flush=True)

    print(f"Hunting hit-rate strategies (min {args.min_trades} trades)...", flush=True)
    hunt = hunt_hit_rates(universe["candidates"], min_trades=args.min_trades)

    out_dir = ROOT / "data" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    summary = {
        "start_date": universe["start_date"],
        "end_date": universe["end_date"],
        "data_start": universe["data_start"],
        "data_end": universe["data_end"],
        "n_candidates": universe["n_candidates"],
        "series_stats": universe["series_stats"],
        "hunt": {
            "n_strategies_tested": hunt["n_strategies_tested"],
            "n_eligible": hunt["n_eligible"],
            "best": hunt["best"],
            "best_hit_rate_ge_70": hunt["best_hit_rate_ge_70"],
            "best_pnl_among_hit_rate_ge_70": hunt["best_pnl_among_hit_rate_ge_70"],
            "all_high_hit": hunt["all_high_hit"],
            "ranked_top20": hunt["ranked"][:20],
        },
        "errors": universe["errors"],
    }
    path = out_dir / f"hit_rate_hunt_{stamp}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    cand_path = out_dir / f"hit_rate_candidates_{stamp}.json"
    slim = [
        {
            k: c[k]
            for k in (
                "series",
                "ticker",
                "event_date",
                "result",
                "mid",
                "yes_bid",
                "yes_ask",
            )
        }
        for c in universe["candidates"]
    ]
    cand_path.write_text(json.dumps(slim), encoding="utf-8")

    print(json.dumps(summary["hunt"], indent=2), flush=True)
    print(f"\nWrote {path}", flush=True)
    print(f"Wrote {cand_path}", flush=True)


if __name__ == "__main__":
    main()
