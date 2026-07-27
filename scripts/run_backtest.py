#!/usr/bin/env python
"""Run hit-rate backtest (replaces weather edge backtest)."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.hit_rate_scan import build_candidates, hunt_hit_rates


def main() -> None:
    settings = load_settings()
    year = date.today().year
    universe = build_candidates(
        settings.favorites_series,
        settings,
        start_date=f"{year}-01-01",
        max_markets_per_series=settings.backtest_max_markets_per_series,
        entry_hours_before_close=settings.backtest_entry_hours_before_close,
    )
    hunt = hunt_hit_rates(universe["candidates"], min_trades=25)
    summary = {
        "data_start": universe.get("data_start"),
        "data_end": universe.get("data_end"),
        "n_candidates": universe.get("n_candidates"),
        "best": hunt.get("best"),
        "best_pnl_among_hit_rate_ge_70": hunt.get("best_pnl_among_hit_rate_ge_70"),
        "top_high_hit": (hunt.get("all_high_hit") or [])[:10],
    }
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
