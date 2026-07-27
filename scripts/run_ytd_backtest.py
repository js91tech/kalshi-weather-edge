#!/usr/bin/env python
"""Alias for hit-rate backtest — use scripts/hunt_hit_rates.py for full output."""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if __name__ == "__main__":
    runpy.run_path(str(ROOT / "scripts" / "hunt_hit_rates.py"), run_name="__main__")
