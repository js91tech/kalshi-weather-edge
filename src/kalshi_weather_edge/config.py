from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class City:
    id: str
    name: str
    series_ticker: str
    lat: float
    lon: float
    timezone: str
    longshot_bias: float = 0.03


@dataclass
class Settings:
    mode: str
    kalshi_base_url: str
    taker_fee_rate: float
    maker_fee_rate: float
    min_edge: float
    min_edge_taker: float
    longshot_market_max: float
    longshot_overprice_min: float
    market_shrinkage: float
    max_trades_per_event: int
    kelly_fraction: float
    max_contracts_per_signal: int
    paper_bankroll: float
    sigma_floor: float
    sigma_scale: float
    continuity_correction: float
    cities: list[City]
    data_dir: Path
    db_path: Path
    raw: dict[str, Any]


def load_settings(path: Path | None = None) -> Settings:
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    cities = [City(**c) for c in raw["cities"]]
    data_dir = ROOT / raw["paths"]["data_dir"]
    db_path = ROOT / raw["paths"]["db_path"]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)

    return Settings(
        mode=raw.get("mode", "paper"),
        kalshi_base_url=raw["kalshi"]["base_url"].rstrip("/"),
        taker_fee_rate=float(raw["kalshi"]["taker_fee_rate"]),
        maker_fee_rate=float(raw["kalshi"]["maker_fee_rate"]),
        min_edge=float(raw["edge"]["min_edge"]),
        min_edge_taker=float(raw["edge"]["min_edge_taker"]),
        longshot_market_max=float(raw["edge"]["longshot_market_max"]),
        longshot_overprice_min=float(raw["edge"]["longshot_overprice_min"]),
        market_shrinkage=float(raw["edge"].get("market_shrinkage", 0.35)),
        max_trades_per_event=int(raw["edge"].get("max_trades_per_event", 2)),
        kelly_fraction=float(raw["edge"]["kelly_fraction"]),
        max_contracts_per_signal=int(raw["edge"]["max_contracts_per_signal"]),
        paper_bankroll=float(raw["edge"]["paper_bankroll"]),
        sigma_floor=float(raw["forecast"]["sigma_floor"]),
        sigma_scale=float(raw["forecast"]["sigma_scale"]),
        continuity_correction=float(raw["forecast"]["continuity_correction"]),
        cities=cities,
        data_dir=data_dir,
        db_path=db_path,
        raw=raw,
    )
