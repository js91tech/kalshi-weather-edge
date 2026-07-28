#!/usr/bin/env python
"""Scheduled scan: consensus signals + settlements + optional webhook alert."""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kalshi_weather_edge.alerts import maybe_alert_scan
from kalshi_weather_edge.config import load_settings
from kalshi_weather_edge.favorites_pipeline import run_consensus_scan


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--strategy",
        choices=["favorites", "high_profit", "both"],
        default="both",
    )
    parser.add_argument("--no-alert", action="store_true")
    parser.add_argument(
        "--out",
        type=str,
        default="",
        help="Optional JSON output path (default data/scans/latest.json)",
    )
    args = parser.parse_args()

    settings = load_settings()
    strategies = (
        ["favorites", "high_profit"] if args.strategy == "both" else [args.strategy]
    )

    results = []
    all_trade_rows: list[dict] = []
    settled_total = 0

    for strategy in strategies:
        print(f"Scanning {strategy}...", flush=True)
        result = run_consensus_scan(
            settings,
            strategy=strategy,
            notes=f"scheduled:{strategy}",
            sync_settlements=True,
        )
        settle = result.get("settlements") or {}
        settled_total += int(settle.get("signals_updated") or 0)
        trades = [r for r in result["rows"] if r["action"] != "PASS"]
        all_trade_rows.extend(trades)
        summary = {
            "strategy": strategy,
            "run_id": result["run_id"],
            "markets_scored": result["markets_scored"],
            "trade_signals": result["trade_signals"],
            "pass_signals": result["pass_signals"],
            "settlements": settle,
            "errors": result["errors"],
            "stats": result["stats"],
            "top_trades": [
                {
                    "ticker": t["ticker"],
                    "action": t["action"],
                    "series": (t.get("meta") or {}).get("series"),
                    "mid": t["market_mid"],
                    "edge": t["edge"],
                    "reason": t["reason"],
                }
                for t in sorted(trades, key=lambda x: abs(float(x.get("edge") or 0)), reverse=True)[
                    :10
                ]
            ],
        }
        results.append(summary)
        print(json.dumps(summary, indent=2), flush=True)

    alert_result = {"skipped": True, "reason": "--no-alert"}
    if not args.no_alert:
        # Prefer high_profit signals for alerts when both ran
        alert_rows = [r for r in all_trade_rows if (r.get("meta") or {}).get("strategy") == "high_profit"]
        if not alert_rows:
            alert_rows = all_trade_rows
        paper_pnl = None
        if results:
            paper_pnl = float((results[-1].get("stats") or {}).get("paper_pnl") or 0)
        alert_result = maybe_alert_scan(
            settings,
            strategy="+".join(strategies),
            rows=alert_rows,
            settled_updated=settled_total,
            paper_pnl=paper_pnl,
        )
        print("Alert:", json.dumps({k: v for k, v in alert_result.items() if k != "preview"}), flush=True)

    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace(":", "")
    out_dir = ROOT / "data" / "scans"
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "strategies": results,
        "alert": {k: v for k, v in alert_result.items() if k != "preview"},
    }
    latest_path = Path(args.out) if args.out else out_dir / "latest.json"
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    stamped = out_dir / f"scan_{stamp}.json"
    stamped.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote {latest_path}", flush=True)
    print(f"Wrote {stamped}", flush=True)


if __name__ == "__main__":
    main()
