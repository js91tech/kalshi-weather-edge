from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import requests


@dataclass
class TempForecast:
    target_date: str
    mu: float
    sigma: float
    p10: float
    p50: float
    p90: float
    source: str
    members: list[float]


def _percentile(values: list[float], q: float) -> float:
    return float(np.percentile(np.asarray(values, dtype=float), q))


def fetch_ensemble_highs(
    lat: float,
    lon: float,
    timezone: str,
    forecast_days: int = 3,
    sigma_floor: float = 0.5,
    sigma_scale: float = 1.0,
    timeout: float = 30.0,
) -> list[TempForecast]:
    """
    Open-Meteo GFS ensemble daily max temps (°F) → N(μ, σ) via p10/p50/p90.
    σ = max((p90 - p10) / 2.56, sigma_floor) * sigma_scale
    """
    url = "https://ensemble-api.open-meteo.com/v1/ensemble"
    params = {
        "latitude": lat,
        "longitude": lon,
        "daily": "temperature_2m_max",
        "temperature_unit": "fahrenheit",
        "timezone": timezone,
        "forecast_days": forecast_days,
        "models": "gfs_seamless",
    }
    resp = requests.get(url, params=params, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    daily: dict[str, Any] = data.get("daily") or {}
    times: list[str] = daily.get("time") or []
    if not times:
        return []

    member_keys = [
        k
        for k in daily.keys()
        if k.startswith("temperature_2m_max_member") or k == "temperature_2m_max"
    ]
    # Prefer members only for spread; include control as a member if present
    if not member_keys:
        raise RuntimeError("No ensemble temperature members returned from Open-Meteo")

    out: list[TempForecast] = []
    for i, day in enumerate(times):
        members: list[float] = []
        for key in member_keys:
            series = daily.get(key) or []
            if i < len(series) and series[i] is not None:
                members.append(float(series[i]))
        if len(members) < 3:
            continue
        p10 = _percentile(members, 10)
        p50 = _percentile(members, 50)
        p90 = _percentile(members, 90)
        sigma_raw = max((p90 - p10) / 2.56, sigma_floor) * sigma_scale
        sigma = max(sigma_raw, 0.1)
        out.append(
            TempForecast(
                target_date=day,
                mu=p50,
                sigma=sigma,
                p10=p10,
                p50=p50,
                p90=p90,
                source="open_meteo_gfs_ensemble",
                members=members,
            )
        )
    return out
