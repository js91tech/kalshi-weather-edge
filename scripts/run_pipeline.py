#!/usr/bin/env python
"""Run one favorites scan from the CLI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.favorites_pipeline import run_favorites_scan


def main() -> None:
    result = run_favorites_scan(notes="cli")
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    trades = [r for r in result["rows"] if r["action"] != "PASS"]
    print(f"\nFavorites signals ({len(trades)}):")
    for t in trades:
        print(
            f"  {t['action']:8} {t['ticker']:32} "
            f"mid={t['market_mid']:.3f} x{t['suggested_contracts']:.0f}  "
            f"{t['reason']}"
        )


if __name__ == "__main__":
    main()
