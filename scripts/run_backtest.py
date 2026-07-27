#!/usr/bin/env python
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.backtest import fine_tune, run_backtest
from kalshi_weather_edge.config import load_settings


def main() -> None:
    settings = load_settings()
    bt = run_backtest(settings)
    summary = {
        k: bt[k]
        for k in (
            "start_date",
            "end_date",
            "n_candidates",
            "n_trades",
            "wins",
            "losses",
            "win_rate",
            "pnl",
            "avg_pnl_per_trade",
            "params",
            "errors",
        )
    }
    print(json.dumps(summary, indent=2))
    print("\nFine-tune...")
    # strip city objects
    cands = []
    for c in bt["candidates"]:
        cc = dict(c)
        cc.pop("city", None)
        cands.append(cc)
    # restore cities for scoring
    city_map = {c.id: c for c in settings.cities}
    for c in cands:
        c["city"] = city_map.get(c["city_id"])
    tuned = fine_tune(cands, settings)
    print(json.dumps({"best": tuned.get("best"), "top5": (tuned.get("ranked") or [])[:5]}, indent=2))


if __name__ == "__main__":
    main()
