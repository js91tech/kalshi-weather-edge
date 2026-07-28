#!/usr/bin/env python
"""Backtest looser favorites, longshots, and fades for higher $/contract."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.hit_rate_scan import build_candidates, hunt_profit_strategies


def _load_cached_candidates(path: Path) -> list[dict]:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_candidate_cache(backtests_dir: Path) -> Path | None:
    files = sorted(backtests_dir.glob("hit_rate_candidates_*.json"), reverse=True)
    return files[0] if files else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--cache",
        type=str,
        default="",
        help="Path to hit_rate_candidates JSON (skip API fetch)",
    )
    parser.add_argument("--rebuild", action="store_true", help="Force fresh candidate build")
    parser.add_argument("--min-trades", type=int, default=25)
    args = parser.parse_args()

    settings = load_settings()
    series = settings.favorites_series
    out_dir = ROOT / "data" / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()

    cache_path: Path | None = None
    if args.cache:
        cache_path = Path(args.cache)
    elif not args.rebuild:
        cache_path = _latest_candidate_cache(out_dir)

    if cache_path and cache_path.exists() and not args.rebuild:
        print(f"Loading cached candidates from {cache_path}", flush=True)
        candidates = _load_cached_candidates(cache_path)
        universe = {
            "start_date": f"{date.today().year}-01-01",
            "end_date": stamp,
            "data_start": min((c["event_date"] for c in candidates if c.get("event_date")), default=stamp),
            "data_end": max((c["event_date"] for c in candidates if c.get("event_date")), default=stamp),
            "n_candidates": len(candidates),
            "series_stats": {},
            "errors": [],
            "cache_source": str(cache_path),
        }
    else:
        year = date.today().year
        print(f"Building candidates from {year}-01-01 for {', '.join(series)}...", flush=True)
        universe = build_candidates(
            series,
            settings,
            start_date=f"{year}-01-01",
            max_markets_per_series=settings.backtest_max_markets_per_series,
            entry_hours_before_close=settings.backtest_entry_hours_before_close,
            max_workers=6,
        )
        candidates = universe["candidates"]
        cand_path = out_dir / f"profit_candidates_{stamp}.json"
        slim = [
            {k: c[k] for k in ("series", "ticker", "event_date", "result", "mid", "yes_bid", "yes_ask")}
            for c in candidates
        ]
        cand_path.write_text(json.dumps(slim), encoding="utf-8")
        print(f"Wrote {cand_path}", flush=True)

    print(
        f"Candidates: {len(candidates)} | series filter: {', '.join(series)}",
        flush=True,
    )

    hunt_all = hunt_profit_strategies(candidates, min_trades=args.min_trades)
    hunt_fav = hunt_profit_strategies(
        candidates,
        min_trades=args.min_trades,
        series_filter=set(series),
    )

    summary = {
        "run_date": stamp,
        "series": series,
        "universe": {k: v for k, v in universe.items() if k != "candidates"},
        "all_markets": hunt_all,
        "favorites_series_only": hunt_fav,
    }

    path = out_dir / f"profit_hunt_{stamp}.json"
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")

    def _print_block(title: str, hunt: dict) -> None:
        print(f"\n=== {title} ===", flush=True)
        best = hunt.get("best_avg_pnl")
        if best:
            print(
                f"Best avg $/contract: {best['name']} | "
                f"{best['wins']}W/{best['losses']}L ({best['win_rate']:.1%}) | "
                f"avg ${best['avg_pnl']:.3f} | total ${best['pnl']:.2f} | "
                f"avg win ${best.get('avg_win_pnl', 0):.3f} | avg loss ${best.get('avg_loss_pnl', 0):.3f}",
                flush=True,
            )
        for wr in (0.50, 0.60, 0.70):
            key = f"best_avg_pnl_win_rate_ge_{int(wr * 100)}"
            row = hunt.get(key)
            if row:
                print(
                    f"Best avg $/contract (WR>={wr:.0%}): {row['name']} | "
                    f"{row['win_rate']:.1%} | avg ${row['avg_pnl']:.3f} | total ${row['pnl']:.2f}",
                    flush=True,
                )
        for threshold in (0.10, 0.25, 0.50):
            rows = hunt.get(f"avg_pnl_ge_{threshold:.2f}") or []
            print(f"\nStrategies with avg >= ${threshold:.2f}/contract: {len(rows)}", flush=True)
            for row in rows[:5]:
                print(
                    f"  {row['name']} | {row['wins']}W/{row['losses']}L ({row['win_rate']:.1%}) | "
                    f"avg ${row['avg_pnl']:.3f} | total ${row['pnl']:.2f}",
                    flush=True,
                )
        print("\nTop 10 by avg $/contract:", flush=True)
        for row in hunt.get("top20_by_avg_pnl", [])[:10]:
            print(
                f"  {row['name']} | {row['wins']}W/{row['losses']}L ({row['win_rate']:.1%}) | "
                f"avg ${row['avg_pnl']:.3f} | total ${row['pnl']:.2f} | {row['family']}",
                flush=True,
            )

    _print_block("ALL MARKETS IN CACHE", hunt_all)
    _print_block("FAVORITES SERIES ONLY (config.yaml)", hunt_fav)
    print(f"\nWrote {path}", flush=True)


if __name__ == "__main__":
    main()
