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
class SeriesThresholds:
    yes_threshold: float
    no_threshold: float


@dataclass
class Settings:
    mode: str
    strategy: str
    favorites_yes_threshold: float
    favorites_no_threshold: float
    favorites_contracts: float
    favorites_series: list[str]
    high_profit_yes_threshold: float
    high_profit_no_threshold: float
    high_profit_contracts: float
    high_profit_series: list[str]
    high_profit_series_overrides: dict[str, SeriesThresholds]
    max_trades_per_event: int
    max_spread: float
    max_signals_alert: int
    min_edge_for_alert: float
    scan_close_within_hours: float
    dedup_open_trades: bool
    bankroll_risk_fraction: float
    assumed_win_rate: float
    require_positive_net_ev: bool
    fee_assumption: str
    use_balance_sizing: bool
    alerts_enabled: bool
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


def _parse_overrides(
    raw_overrides: dict[str, Any] | None,
    default_yes: float,
    default_no: float,
) -> dict[str, SeriesThresholds]:
    out: dict[str, SeriesThresholds] = {}
    for series, vals in (raw_overrides or {}).items():
        if not isinstance(vals, dict):
            continue
        out[str(series)] = SeriesThresholds(
            yes_threshold=float(vals.get("yes_threshold", default_yes)),
            no_threshold=float(vals.get("no_threshold", default_no)),
        )
    return out


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
    hp = raw.get("high_profit") or {}
    risk = raw.get("risk") or {}
    alerts = raw.get("alerts") or {}
    bt = raw.get("backtest") or {}
    kalshi = raw["kalshi"]

    hp_yes = float(hp.get("yes_threshold", 0.90))
    hp_no = float(hp.get("no_threshold", 0.20))

    return Settings(
        mode=str(raw.get("mode", "paper")).lower(),
        strategy=str(raw.get("strategy", "favorites")).lower(),
        favorites_yes_threshold=float(fav.get("yes_threshold", 0.90)),
        favorites_no_threshold=float(fav.get("no_threshold", 0.10)),
        favorites_contracts=float(fav.get("contracts", 1.0)),
        favorites_series=list(fav.get("series") or []),
        high_profit_yes_threshold=hp_yes,
        high_profit_no_threshold=hp_no,
        high_profit_contracts=float(hp.get("contracts", 1.0)),
        high_profit_series=list(hp.get("series") or fav.get("series") or []),
        high_profit_series_overrides=_parse_overrides(
            hp.get("series_overrides"), hp_yes, hp_no
        ),
        max_trades_per_event=int(risk.get("max_trades_per_event", 3)),
        max_spread=float(risk.get("max_spread", 0.08)),
        max_signals_alert=int(risk.get("max_signals_alert", 10)),
        min_edge_for_alert=float(risk.get("min_edge_for_alert", 0.15)),
        scan_close_within_hours=float(risk.get("scan_close_within_hours", 24)),
        dedup_open_trades=bool(risk.get("dedup_open_trades", True)),
        bankroll_risk_fraction=float(risk.get("bankroll_risk_fraction", 0.02)),
        assumed_win_rate=float(risk.get("assumed_win_rate", 0.94)),
        require_positive_net_ev=bool(risk.get("require_positive_net_ev", True)),
        fee_assumption=str(risk.get("fee_assumption", "maker")).lower(),
        use_balance_sizing=bool(risk.get("use_balance_sizing", True)),
        alerts_enabled=bool(alerts.get("enabled", True)),
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


def active_fee_rate(settings: Settings, fee_assumption: str | None = None) -> float:
    assumption = (fee_assumption or settings.fee_assumption or "maker").lower()
    if assumption == "taker":
        return float(settings.taker_fee_rate)
    return float(settings.maker_fee_rate)


def thresholds_for_series(
    settings: Settings,
    strategy: str,
    series: str,
) -> tuple[float, float]:
    """Return (yes_threshold, no_threshold) for a series under the active strategy."""
    if strategy == "high_profit":
        override = settings.high_profit_series_overrides.get(series)
        if override:
            return override.yes_threshold, override.no_threshold
        return settings.high_profit_yes_threshold, settings.high_profit_no_threshold
    return settings.favorites_yes_threshold, settings.favorites_no_threshold


def with_overrides(settings: Settings, **kwargs: Any) -> Settings:
    return replace(settings, **kwargs)


def live_credentials_configured() -> dict[str, Any]:
    load_dotenv(ROOT / ".env")
    key_id = os.getenv("KALSHI_API_KEY_ID", "").strip()
    key_path = os.getenv("KALSHI_PRIVATE_KEY_PATH", "").strip()
    pem = os.getenv("KALSHI_PRIVATE_KEY_PEM", "").strip()
    env = os.getenv("KALSHI_ENV", "production").strip().lower()
    path_ok = bool(key_path) and Path(key_path).expanduser().exists()
    pem_set = bool(pem)
    return {
        "key_id": key_id,
        "key_id_set": bool(key_id),
        "key_path": key_path,
        "key_path_exists": path_ok,
        "private_key_pem": pem if pem_set else "",
        "pem_set": pem_set,
        "env": env,
        "ready": bool(key_id) and (path_ok or pem_set),
    }


def active_kalshi_base_url(settings: Settings, use_demo: bool | None = None) -> str:
    load_dotenv(ROOT / ".env")
    if use_demo is None:
        use_demo = os.getenv("KALSHI_ENV", "production").strip().lower() == "demo"
    return settings.kalshi_demo_base_url if use_demo else settings.kalshi_base_url


def alert_webhook_url() -> str | None:
    load_dotenv(ROOT / ".env")
    url = os.getenv("ALERT_WEBHOOK_URL", "").strip()
    return url or None


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
