#!/usr/bin/env python
"""Backtest favorites vs high_profit strategy profiles side-by-side."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.hit_rate_scan import backtest_strategy_profiles, build_candidates


def _load_cached_candidates(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_candidate_cache(backtests_dir: Path) -> Path | None:
    files = sorted(backtests_dir.glob("hit_rate_candidates_*.json"), reverse=True)
    if not files:
        files = sorted(backtests_dir.glob("profit_candidates_*.json"), reverse=True)
    return files[0] if files else None


def _profiles_from_settings(settings) -> list[dict]:
    return [
        {
            "name": "favorites",
            "yes_threshold": settings.favorites_yes_threshold,
            "no_threshold": settings.favorites_no_threshold,
            "series": settings.favorites_series,
        },
        {
            "name": "high_profit",
            "yes_threshold": settings.high_profit_yes_threshold,
            "no_threshold": settings.high_profit_no_threshold,
            "series": settings.high_profit_series,
        },
    ]


def _print_profile(row: dict) -> None:
    print(
        f"  {row['name']:20} | {row['wins']}W/{row['losses']}L ({row['win_rate']:.1%}) | "
        f"avg ${row['avg_pnl']:.3f}/contract | total ${row['pnl']:.2f} | "
        f"YES {row['yes_trades']} / NO {row['no_trades']} | "
        f"thresholds YES>={row['yes_threshold']:.2f} NO<={row['no_threshold']:.2f}",
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cache", type=str, default="", help="Candidate JSON cache path")
    parser.add_argument("--rebuild", action="store_true", help="Fetch fresh settled history")
    args = parser.parse_args()

    settings = load_settings()
    profiles = _profiles_from_settings(settings)
    out_dir = ROOT / "data" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()

    cache_path: Path | None = Path(args.cache) if args.cache else None
    if not cache_path and not args.rebuild:
        cache_path = _latest_candidate_cache(out_dir)

    all_series = sorted({s for p in profiles for s in p["series"]})

    if cache_path and cache_path.exists() and not args.rebuild:
        print(f"Loading cached candidates from {cache_path}", flush=True)
        candidates = _load_cached_candidates(cache_path)
        universe = {
            "cache_source": str(cache_path),
            "n_candidates": len(candidates),
            "data_start": min((c["event_date"] for c in candidates if c.get("event_date")), default=stamp),
            "data_end": max((c["event_date"] for c in candidates if c.get("event_date")), default=stamp),
        }
    else:
        year = date.today().year
        print(f"Building candidates for {', '.join(all_series)}...", flush=True)
        universe = build_candidates(
            all_series,
            settings,
            start_date=f"{year}-01-01",
            max_markets_per_series=settings.backtest_max_markets_per_series,
            entry_hours_before_close=settings.backtest_entry_hours_before_close,
            max_workers=6,
        )
        candidates = universe["candidates"]

    print(
        f"Candidates: {len(candidates)} | window {universe.get('data_start')} -> {universe.get('data_end')}",
        flush=True,
    )

    results = backtest_strategy_profiles(candidates, profiles)

    print("\n=== Strategy profiles (combined series) ===", flush=True)
    for row in results["profiles"]:
        _print_profile(row)

    for profile_name, rows in results["per_series"].items():
        print(f"\n=== {profile_name} — per series ===", flush=True)
        for row in sorted(rows, key=lambda r: r["avg_pnl"], reverse=True):
            series = (row.get("series") or ["?"])[0]
            print(f"  [{series}]", flush=True)
            _print_profile(row)

    summary = {
        "run_date": stamp,
        "universe": {k: v for k, v in universe.items() if k != "candidates"},
        "profiles_config": profiles,
        "results": results,
    }
    path = out_dir / f"strategy_profiles_{stamp}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
