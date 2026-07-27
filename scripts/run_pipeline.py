#!/usr/bin/env python
"""Run one paper pipeline cycle from the CLI."""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.pipeline import run_pipeline


def main() -> None:
    result = run_pipeline(notes="cli")
    summary = {k: v for k, v in result.items() if k != "rows"}
    print(json.dumps(summary, indent=2))
    trades = [r for r in result["rows"] if r["action"] != "PASS"]
    print(f"\nTrade signals ({len(trades)}):")
    for t in trades:
        print(
            f"  {t['action']:8} {t['ticker']:28} "
            f"model={t['model_p']:.3f} mid={t['market_mid']:.3f} "
            f"edge={t['edge']:.3f} x{t['suggested_contracts']:.1f}"
        )


if __name__ == "__main__":
    main()
