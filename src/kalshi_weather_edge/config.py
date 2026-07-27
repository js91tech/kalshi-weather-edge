from __future__ import annotations

import os
from copy import deepcopy
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv


ROOT = Path(__file__).resolve().parents[2]


@dataclass
class Settings:
    mode: str
    strategy: str
    favorites_yes_threshold: float
    favorites_no_threshold: float
    favorites_contracts: float
    favorites_series: list[str]
    kalshi_base_url: str
    kalshi_demo_base_url: str
    taker_fee_rate: float
    maker_fee_rate: float
    live_max_contracts_per_order: int
    live_require_maker: bool
    max_contracts_per_signal: int
    backtest_lookback_days: int
    backtest_max_markets_per_series: int
    backtest_entry_hours_before_close: int
    backtest_contracts_per_trade: float
    data_dir: Path
    db_path: Path
    raw: dict[str, Any]


def load_settings(path: Path | None = None) -> Settings:
    load_dotenv(ROOT / ".env")
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    data_dir = ROOT / raw["paths"]["data_dir"]
    db_path = ROOT / raw["paths"]["db_path"]
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "cache").mkdir(parents=True, exist_ok=True)

    fav = raw.get("favorites") or {}
    bt = raw.get("backtest") or {}
    kalshi = raw["kalshi"]

    return Settings(
        mode=str(raw.get("mode", "paper")).lower(),
        strategy=str(raw.get("strategy", "favorites")).lower(),
        favorites_yes_threshold=float(fav.get("yes_threshold", 0.90)),
        favorites_no_threshold=float(fav.get("no_threshold", 0.10)),
        favorites_contracts=float(fav.get("contracts", 1.0)),
        favorites_series=list(fav.get("series") or []),
        kalshi_base_url=kalshi["base_url"].rstrip("/"),
        kalshi_demo_base_url=str(
            kalshi.get("demo_base_url", "https://demo-api.kalshi.co/trade-api/v2")
        ).rstrip("/"),
        taker_fee_rate=float(kalshi["taker_fee_rate"]),
        maker_fee_rate=float(kalshi["maker_fee_rate"]),
        live_max_contracts_per_order=int(kalshi.get("live_max_contracts_per_order", 5)),
        live_require_maker=bool(kalshi.get("live_require_maker", True)),
        max_contracts_per_signal=int(kalshi.get("live_max_contracts_per_order", 5)),
        backtest_lookback_days=int(bt.get("lookback_days", 220)),
        backtest_max_markets_per_series=int(bt.get("max_markets_per_series", 500)),
        backtest_entry_hours_before_close=int(bt.get("entry_hours_before_close", 12)),
        backtest_contracts_per_trade=float(bt.get("contracts_per_trade", 1.0)),
        data_dir=data_dir,
        db_path=db_path,
        raw=raw,
    )


def with_overrides(settings: Settings, **kwargs: Any) -> Settings:
    return replace(settings, **kwargs)


def live_credentials_configured() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
    env = os.getenv("KALSHI_ENV", "production").strip().lower()
    path_ok = bool(key_path) and Path(key_path).expanduser().exists()
    return {
        "key_id_set": bool(key_id),
        "key_path": key_path,
        "key_path_exists": path_ok,
        "env": env,
        "ready": bool(key_id) and path_ok,
    }


def active_kalshi_base_url(settings: Settings, use_demo: bool | None = None) -> str:
    load_dotenv(ROOT / ".env")
    if use_demo is None:
        use_demo = os.getenv("KALSHI_ENV", "production").strip().lower() == "demo"
    return settings.kalshi_demo_base_url if use_demo else settings.kalshi_base_url


def save_mode_to_config(mode: str, path: Path | None = None) -> None:
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = deepcopy(raw)
    raw["mode"] = "live" if mode == "live" else "paper"
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False)


def save_favorites_thresholds(
    yes_threshold: float,
    no_threshold: float,
    path: Path | None = None,
) -> None:
    cfg_path = path or (ROOT / "config.yaml")
    with cfg_path.open(encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    raw = deepcopy(raw)
    raw.setdefault("favorites", {})
    raw["favorites"]["yes_threshold"] = float(yes_threshold)
    raw["favorites"]["no_threshold"] = float(no_threshold)
    with cfg_path.open("w", encoding="utf-8") as f:
        yaml.safe_dump(raw, f, sort_keys=False)
