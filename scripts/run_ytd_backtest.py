#!/usr/bin/env python
"""Run year-to-date (or full available 2026) backtest + fine-tune."""
from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.backtest import fine_tune, run_backtest
from kalshi_weather_edge.config import load_settings


def main() -> None:
    year = date.today().year
    start = f"{year}-01-01"
    settings = load_settings()
    print(f"Running backtest from {start} through today (all cities)...")
    print("Note: Kalshi settled weather history may start mid-year; report uses available data.")

    bt = run_backtest(settings, start_date=start)
    out_dir = ROOT / "data" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()

    summary = {
        k: bt[k]
        for k in (
            "start_date",
            "end_date",
            "data_start",
            "data_end",
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
        if k in bt
    }

    # Per-city breakdown
    by_city: dict[str, dict] = {}
    for t in bt.get("trades") or []:
        cid = t.get("city_id") or "?"
        slot = by_city.setdefault(cid, {"wins": 0, "losses": 0, "pnl": 0.0, "n": 0})
        slot["n"] += 1
        slot["pnl"] += float(t.get("pnl") or 0)
        if t.get("won"):
            slot["wins"] += 1
        else:
            slot["losses"] += 1
    for cid, slot in by_city.items():
        n = slot["n"] or 1
        slot["win_rate"] = slot["wins"] / n
    summary["by_city"] = by_city

    city_map = {c.id: c for c in settings.cities}
    cands = []
    for c in bt["candidates"]:
        cc = dict(c)
        cc.pop("city", None)
        cc["city"] = city_map.get(c["city_id"])
        cands.append(cc)
    print("Fine-tuning...")
    tuned = fine_tune(cands, settings)
    summary["fine_tune_best"] = tuned.get("best")
    summary["fine_tune_top5"] = (tuned.get("ranked") or [])[:5]

    summary_path = out_dir / f"ytd_{stamp}_summary.json"
    trades_path = out_dir / f"ytd_{stamp}_trades.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    trades_path.write_text(json.dumps(bt.get("trades") or [], indent=2), encoding="utf-8")

    print(json.dumps(summary, indent=2))
    print(f"\nWrote {summary_path}")
    print(f"Wrote {trades_path}")


if __name__ == "__main__":
    main()
